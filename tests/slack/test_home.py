from datetime import UTC, datetime, timedelta

from deployground.metadata import StackRecord
from deployground.slack.home import build_home, humanize_expiry

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


class FakeIndex:
    def __init__(self, records):
        self.records = records

    def list_by_owner(self, user):
        return [r for r in self.records if r.owner == user]


class FakeAuthz:
    def __init__(self, envs):
        self.envs = envs

    def allowed_environments(self, user):
        return self.envs


def _rec(name, owner="U1", expires=None):
    return StackRecord(
        name=name, env="dev", owner=owner, expires_at=expires, thread_ts="1.0", channel="C1"
    )


def _action_ids(view):
    ids = []
    for b in view["blocks"]:
        if b["type"] == "actions":
            ids += [e["action_id"] for e in b["elements"]]
        if "accessory" in b:
            ids.append(b["accessory"]["action_id"])
    return ids


def test_home_lists_user_stacks_from_index_with_buttons():
    idx = FakeIndex(
        [_rec("shop", expires=NOW + timedelta(hours=2, minutes=30)), _rec("other", owner="U2")]
    )
    view = build_home("U1", idx, FakeAuthz({"dev"}), now=NOW)
    assert view["type"] == "home"
    text = str(view)
    assert "shop" in text and "other" not in text
    assert "2h 30m" in text
    ids = _action_ids(view)
    assert "open_deploy_form" in ids
    assert "extend::shop" in ids and "destroy::shop" in ids


def test_home_hides_new_button_without_envs():
    view = build_home("U1", FakeIndex([]), FakeAuthz(set()), now=NOW)
    assert "open_deploy_form" not in _action_ids(view)


def test_humanize_expiry():
    assert humanize_expiry(None, NOW) == "never"
    assert humanize_expiry(NOW - timedelta(minutes=1), NOW) == "expired"
    assert humanize_expiry(NOW + timedelta(minutes=45), NOW) == "45m"
    assert humanize_expiry(NOW + timedelta(hours=2, minutes=30), NOW) == "2h 30m"
