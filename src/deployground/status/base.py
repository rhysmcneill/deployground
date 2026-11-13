"""Status source abstraction: where component state changes come from."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from deployground.states import CompState


@dataclass(frozen=True)
class StatusEvent:
    """One component's state reported for one stack."""

    stack_id: str
    component: str
    state: CompState


class StatusSource(Protocol):
    """A producer of ``StatusEvent``s (e.g. a polling or webhook-backed source)."""

    def start(self, on_change: Callable[[StatusEvent], None]) -> None:
        """Begin reporting changes by calling ``on_change`` for each one.

        Events may repeat; consumers must deduplicate (see ``EventDeduper``).
        """
        ...

    def stop(self) -> None:
        """Stop reporting and release resources. Called at shutdown (Task 19).

        Must be safe to call after ``start``. No ``on_change`` calls are expected after it returns.
        """
        ...
