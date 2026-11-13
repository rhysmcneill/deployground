from deployground.states import CompState, StackState
from deployground.status.aggregate import EventDeduper, aggregate
from deployground.status.base import StatusEvent


def test_all_healthy_is_running():
    assert aggregate({"web": CompState.Healthy, "db": CompState.Healthy}) == StackState.Running


def test_any_failed_is_failed():
    # One Failed among Healthy components must never report success.
    states = {"web": CompState.Healthy, "db": CompState.Failed}
    assert aggregate(states) == StackState.Failed


def test_mixed_is_progressing():
    states = {"web": CompState.Healthy, "db": CompState.Progressing}
    assert aggregate(states) == StackState.Deploying


def test_duplicate_event_is_noop():
    deduper = EventDeduper()
    event = StatusEvent(stack_id="s1", component="web", state=CompState.Healthy)
    assert deduper.seen(event) is False
    assert deduper.seen(event) is True
    # A different state for the same component is a new event.
    other = StatusEvent(stack_id="s1", component="web", state=CompState.Failed)
    assert deduper.seen(other) is False
