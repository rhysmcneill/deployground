"""Roll per-component states up into a stack state, and drop repeated events."""

import threading

from deployground.states import CompState, StackState
from deployground.status.base import StatusEvent


def aggregate(component_states: dict[str, CompState]) -> StackState:
    """Derive the stack state from its components' states.

    - Failed if ANY component is Failed (a failure is never masked by healthy peers).
    - Running only when ALL components are Healthy.
    - Otherwise Deploying (some component is still Progressing).
    - An empty dict maps to Deploying: with no reported components there is no
      evidence the stack is up, so it is treated as still in progress rather than
      Running (vacuous truth would wrongly report success before any status arrives).
    """
    if not component_states:
        return StackState.Deploying
    values = component_states.values()
    if any(state is CompState.Failed for state in values):
        return StackState.Failed
    if all(state is CompState.Healthy for state in values):
        return StackState.Running
    return StackState.Deploying


class EventDeduper:
    """Idempotency guard with last-state-per-component semantics.

    Tracks the last state recorded for each ``(stack_id, component)``.
    ``seen`` returns True (a no-op duplicate) only when the event's state equals
    that last recorded state, i.e. a repeated event in a row. Any other event
    (including a first sighting, or a genuine transition such as
    Healthy -> Failed -> Healthy) records the new state and returns False, so a
    recovered component is never stuck in its earlier state. Thread-safe.

    ``clear(stack_id)`` forgets the recorded states for a stack; the thread
    updater calls it when a stack is destroyed so memory does not grow unbounded,
    and so a re-created stack with the same id starts fresh.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str], CompState] = {}

    def seen(self, event: StatusEvent) -> bool:
        key = (event.stack_id, event.component)
        with self._lock:
            if self._last.get(key) is event.state:
                return True
            self._last[key] = event.state
            return False

    def clear(self, stack_id: str) -> None:
        with self._lock:
            self._last = {key: state for key, state in self._last.items() if key[0] != stack_id}
