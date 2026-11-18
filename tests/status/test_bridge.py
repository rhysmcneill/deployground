from datetime import UTC, datetime

from deployground.metadata import RESERVED_COMPONENT, StackRecord
from deployground.states import CompState
from deployground.status.base import StatusEvent
from deployground.status.bridge import StatusProgressBridge

EXPIRES = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self):
        self.updates = []
        self.posts = []

    def chat_update(self, **kw):
        self.updates.append(kw)

    def chat_postMessage(self, **kw):
        self.posts.append(kw)


def _record():
    record = StackRecord(
        name="demo",
        catalog="demo",
        env="dev",
        owner="U1",
        expires_at=EXPIRES,
        thread_ts="1.1",
        channel="C1",
    )
    record.components = {"api": object(), "web": object()}
    return record


class Index:
    def __init__(self, record):
        self._record = record

    def get(self, stack):
        return self._record if stack == self._record.name else None


def test_reserved_component_event_is_ignored():
    record = _record()
    client = FakeClient()
    bridge = StatusProgressBridge(Index(record), client)

    bridge.handle(StatusEvent(stack_id="demo", component=RESERVED_COMPONENT, state=CompState.Healthy))
    assert client.updates == []
    assert client.posts == []
    assert bridge.statuses() == {}

    bridge.handle(StatusEvent(stack_id="demo", component=RESERVED_COMPONENT, state=CompState.Failed))
    assert client.updates == []
    assert client.posts == []
    assert bridge.statuses() == {}
