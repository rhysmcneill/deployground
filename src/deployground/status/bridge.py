"""Status -> progress bridge: turns ``StatusEvent``s into thread updates.

Keeps an in-memory component-state map per stack. Each stack's map is seeded from the
index record (every known component starts Progressing) so a single Healthy event from a
multi-component stack cannot aggregate to Running early. For every non-duplicate event:
re-render the progress message in place, and post a completion message into the thread when
the stack first becomes Running, or when a component goes Failed.
"""

import logging
import threading
from typing import Any, Protocol

from deployground.metadata import StackRecord
from deployground.slack.progress import completion_blocks, render_progress, update_thread
from deployground.states import CompState, StackState
from deployground.status.aggregate import EventDeduper, aggregate
from deployground.status.base import StatusEvent

log = logging.getLogger(__name__)


class _Index(Protocol):
    def get(self, stack: str) -> StackRecord | None: ...


class StatusProgressBridge:
    def __init__(self, index: _Index, client: Any, deduper: EventDeduper | None = None) -> None:
        self._index = index
        self._client = client
        self._deduper = deduper or EventDeduper()
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, CompState]] = {}
        self._overall: dict[str, StackState] = {}

    def statuses(self) -> dict[str, StackState]:
        """Latest aggregate per stack seen so far (for the Home tab)."""
        with self._lock:
            return dict(self._overall)

    def forget(self, stack: str) -> None:
        """Drop all state for a destroyed stack (app.py calls it after each destroy)."""
        self._deduper.clear(stack)
        with self._lock:
            self._states.pop(stack, None)
            self._overall.pop(stack, None)

    def handle(self, event: StatusEvent) -> None:
        record = self._index.get(event.stack_id)
        if record is None:
            # Unknown or destroyed stack (e.g. a late Argo event): keep no state, so a
            # redeploy of the same name starts fresh.
            return
        if self._deduper.seen(event):
            return
        with self._lock:
            states = self._states.get(event.stack_id)
            if states is None:
                states = self._states[event.stack_id] = {
                    n: CompState.Progressing for n in record.components
                }
            states[event.component] = event.state
            snapshot = dict(states)
            overall = aggregate(snapshot)
            previous = self._overall.get(event.stack_id)
            self._overall[event.stack_id] = overall
        update_thread(self._client, record, render_progress(record.name, snapshot, overall))
        if overall is StackState.Running and previous is not StackState.Running:
            self._post(record, completion_blocks(record.name, ok=True, owner=record.owner))
        elif event.state is CompState.Failed:
            self._post(
                record,
                completion_blocks(
                    record.name, ok=False, owner=record.owner, failed_component=event.component
                ),
            )

    def _post(self, record: StackRecord, blocks: list) -> None:
        self._client.chat_postMessage(
            channel=record.channel,
            thread_ts=record.thread_ts,
            blocks=blocks,
            text=f"Deployground: {record.name}",
        )
