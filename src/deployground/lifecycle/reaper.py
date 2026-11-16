"""Expiry loop: warn shortly before a stack expires, destroy it once expired, retry failures.

The warned set is in-memory only: a restarted reaper may re-warn once, which is
acceptable. A failed destroy is alerted to the owner and simply retried next tick.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from deployground.metadata import StackRecord

log = logging.getLogger(__name__)


class ReaperIndex(Protocol):
    def list_expiring(self, before: datetime) -> list[StackRecord]: ...


class ReaperLifecycle(Protocol):
    def destroy(self, user: str, stack: str) -> None: ...


class ReaperNotifier(Protocol):
    def warn(self, record: StackRecord, blocks: list) -> None: ...

    def alert(self, record: StackRecord, text: str) -> None: ...


def warning_blocks(record: StackRecord, expires_in_text: str) -> list[dict[str, Any]]:
    """Block Kit warning with the Extend/Keep/Destroy buttons (action_ids routed by the app)."""

    def button(label: str, action_id: str, style: str | None = None) -> dict[str, Any]:
        element: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": label},
            "action_id": action_id,
            "value": record.name,
        }
        if style:
            element["style"] = style
        return element

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":hourglass: Stack *{record.name}* ({record.env}) expires in "
                f"{expires_in_text}.",
            },
        },
        {
            "type": "actions",
            "elements": [
                button("Extend 24h", f"extend::{record.name}", "primary"),
                button("Keep", f"keep::{record.name}"),
                button("Destroy now", f"destroy_now::{record.name}", "danger"),
            ],
        },
    ]


def _humanize(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes >= 120:
        return f"{minutes // 60} hours"
    if minutes >= 60:
        return "1 hour"
    return f"{minutes} minutes"


class Reaper:
    def __init__(
        self,
        index: ReaperIndex,
        lifecycle: ReaperLifecycle,
        clock: Callable[[], datetime],
        notifier: ReaperNotifier,
        warn_before: timedelta,
        interval: timedelta,
    ) -> None:
        self._index = index
        self._lifecycle = lifecycle
        self._clock = clock
        self._notifier = notifier
        self._warn_before = warn_before
        self._interval = interval
        self._warned: set[str] = set()

    def acknowledge_warning(self, stack: str) -> None:
        """Keep button: suppress re-warning without touching expiry."""
        self._warned.add(stack)

    def tick(self) -> None:
        now = self._clock()
        expiring = self._index.list_expiring(now + self._warn_before)
        # Stacks destroyed or extended out of the window drop out, so a later re-entry re-warns.
        self._warned &= {r.name for r in expiring}
        for record in expiring:
            if record.expires_at is None:
                continue
            if record.expires_at <= now:
                self._destroy(record)
            elif record.name not in self._warned:
                self._warn(record, record.expires_at - now)

    def _destroy(self, record: StackRecord) -> None:
        try:
            self._lifecycle.destroy(record.owner, record.name)
        except Exception as exc:
            log.exception("Reaper failed to destroy stack %s", record.name)
            try:
                self._notifier.alert(
                    record,
                    f"Automatic teardown of stack {record.name} failed ({exc}); will retry.",
                )
            except Exception:
                log.exception("Reaper failed to alert owner of %s", record.name)

    def _warn(self, record: StackRecord, remaining: timedelta) -> None:
        try:
            self._notifier.warn(record, warning_blocks(record, _humanize(remaining)))
        except Exception:
            log.exception("Reaper failed to warn about stack %s", record.name)
            return  # not marked warned: retried next tick
        self._warned.add(record.name)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Reaper tick failed")
            stop.wait(self._interval.total_seconds())
