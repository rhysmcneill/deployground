import threading
from datetime import UTC, datetime, timedelta

from deployground.lifecycle.reaper import Reaper, warning_blocks
from deployground.metadata import StackRecord

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def make_record(name: str, expires_at: datetime | None) -> StackRecord:
    return StackRecord(
        name=name,
        env="dev",
        owner="U1",
        expires_at=expires_at,
        thread_ts="1.1",
        channel="C1",
    )


class FakeIndex:
    def __init__(self, *records: StackRecord) -> None:
        self.records = {r.name: r for r in records}

    def list_expiring(self, before: datetime) -> list[StackRecord]:
        assert before.tzinfo is not None
        return [r for r in self.records.values() if r.expires_at and r.expires_at <= before]

    def get(self, stack: str) -> StackRecord | None:
        return self.records.get(stack)


class FakeLifecycle:
    def __init__(self, index: FakeIndex) -> None:
        self.index = index
        self.destroyed: list[tuple[str, str]] = []
        self.fail = False

    def destroy(self, user: str, stack: str) -> None:
        if self.fail:
            raise RuntimeError("boom https://github.com/org/secret-repo")
        self.destroyed.append((user, stack))
        self.index.records.pop(stack, None)


class FakeNotifier:
    def __init__(self) -> None:
        self.warnings: list[tuple[StackRecord, list]] = []
        self.alerts: list[tuple[StackRecord, str]] = []

    def warn(self, record: StackRecord, blocks: list) -> None:
        self.warnings.append((record, blocks))

    def alert(self, record: StackRecord, text: str) -> None:
        self.alerts.append((record, text))


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def build(*records: StackRecord):
    index = FakeIndex(*records)
    lifecycle = FakeLifecycle(index)
    notifier = FakeNotifier()
    clock = Clock()
    reaper = Reaper(
        index,
        lifecycle,
        clock,
        notifier,
        warn_before=timedelta(hours=1),
        interval=timedelta(seconds=60),
    )
    return reaper, index, lifecycle, notifier, clock


def test_warns_once_before_expiry():
    reaper, _, lifecycle, notifier, _ = build(make_record("s", NOW + timedelta(minutes=30)))
    reaper.tick()
    reaper.tick()
    assert len(notifier.warnings) == 1
    assert lifecycle.destroyed == []
    action_ids = [
        el["action_id"]
        for b in notifier.warnings[0][1]
        if b["type"] == "actions"
        for el in b["elements"]
    ]
    assert action_ids == ["extend::s", "keep::s", "destroy_now::s"]


def test_destroys_after_expiry():
    reaper, _, lifecycle, notifier, _ = build(make_record("s", NOW - timedelta(minutes=1)))
    reaper.tick()
    assert lifecycle.destroyed == [("U1", "s")]
    assert notifier.warnings == []


def test_skips_ttl_none():
    reaper, _, lifecycle, notifier, _ = build(make_record("s", None))
    reaper.tick()
    assert lifecycle.destroyed == []
    assert notifier.warnings == []


def test_failed_destroy_retried_next_tick():
    reaper, _, lifecycle, notifier, _ = build(make_record("s", NOW - timedelta(minutes=1)))
    lifecycle.fail = True
    reaper.tick()
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0][0].name == "s"
    assert "secret-repo" not in notifier.alerts[0][1]
    assert "bot logs" in notifier.alerts[0][1]
    lifecycle.fail = False
    reaper.tick()
    assert lifecycle.destroyed == [("U1", "s")]


def test_warnedAt_in_memory_only():
    record = make_record("s", NOW + timedelta(minutes=30))
    reaper, index, lifecycle, notifier, clock = build(record)
    reaper.tick()
    assert reaper._warned == {"s"}
    fresh = Reaper(
        index,
        lifecycle,
        clock,
        notifier,
        warn_before=timedelta(hours=1),
        interval=timedelta(seconds=60),
    )
    assert fresh._warned == set()
    fresh.tick()
    assert len(notifier.warnings) == 2


def test_acknowledge_warning_suppresses_rewarn_but_still_expires():
    reaper, _, lifecycle, notifier, clock = build(make_record("s", NOW + timedelta(minutes=30)))
    reaper.acknowledge_warning("s")
    reaper.tick()
    assert notifier.warnings == []
    clock.now = NOW + timedelta(minutes=31)
    reaper.tick()
    assert lifecycle.destroyed == [("U1", "s")]


def test_extended_stack_is_rewarned():
    record = make_record("s", NOW + timedelta(minutes=30))
    reaper, _, _, notifier, _ = build(record)
    reaper.tick()
    record.expires_at = NOW + timedelta(days=1)
    reaper.tick()
    assert reaper._warned == set()
    record.expires_at = NOW + timedelta(minutes=30)
    reaper.tick()
    assert len(notifier.warnings) == 2


def test_run_survives_bad_tick():
    reaper, _, _, _, _ = build()
    stop = threading.Event()
    calls: list[int] = []

    def bad_tick() -> None:
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("x")

    reaper.tick = bad_tick  # type: ignore[method-assign]
    reaper._interval = timedelta(seconds=0)
    reaper.run(stop)
    assert len(calls) == 2


def test_warning_blocks_labels():
    blocks = warning_blocks(make_record("s", NOW), "30 minutes")
    labels = [el["text"]["text"] for b in blocks if b["type"] == "actions" for el in b["elements"]]
    assert labels == ["Extend 24h", "Keep", "Destroy now"]
