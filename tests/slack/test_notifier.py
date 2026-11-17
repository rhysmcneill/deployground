from datetime import UTC, datetime, timedelta

from deployground.lifecycle.reaper import Reaper
from deployground.metadata import StackRecord
from deployground.slack.notifier import SlackNotifier


class FakeClient:
    def __init__(self):
        self.posts = []

    def chat_postMessage(self, **kw):
        self.posts.append(kw)

    def conversations_open(self, users):
        return {"channel": {"id": "D" + users}}


def test_reaper_drives_notifier_warn_and_alert():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = StackRecord("s", "s", "dev", "U1", now + timedelta(minutes=30), "1.1", "C1")

    class Index:
        def list_expiring(self, before):
            return [record]

    class Life:
        def destroy(self, user, stack):
            raise RuntimeError("nope")

    client = FakeClient()
    reaper = Reaper(
        Index(),
        Life(),
        lambda: now,
        SlackNotifier(client),
        timedelta(hours=1),
        timedelta(minutes=1),
    )
    reaper.tick()  # warns
    assert [p["channel"] for p in client.posts] == ["C1", "DU1"]
    assert client.posts[0]["thread_ts"] == "1.1" and client.posts[0]["blocks"]

    client.posts.clear()
    record.expires_at = now - timedelta(minutes=1)
    reaper.tick()  # destroy fails -> alert
    assert [p["channel"] for p in client.posts] == ["C1", "DU1"]
    assert "nope" not in client.posts[0]["text"]
    assert "bot logs" in client.posts[0]["text"]
