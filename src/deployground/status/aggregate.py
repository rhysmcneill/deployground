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
    """Idempotency guard keyed by ``(stack_id, component, state)``.

    ``seen`` records the event and returns False the first time a key is seen,
    and True (a no-op) for any repeat. Thread-safe.

    ``clear(stack_id)`` forgets every recorded key for a stack; the thread
    updater calls it when a stack is destroyed so memory does not grow unbounded,
    and so a re-created stack with the same id starts fresh.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str, CompState]] = set()

    def seen(self, event: StatusEvent) -> bool:
        key = (event.stack_id, event.component, event.state)
        with self._lock:
            if key in self._seen:
                return True
            self._seen.add(key)
            return False

    def clear(self, stack_id: str) -> None:
        with self._lock:
            self._seen = {key for key in self._seen if key[0] != stack_id}
