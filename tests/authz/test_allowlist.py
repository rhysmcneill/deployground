import logging

from deployground.authz.allowlist import AllowlistAuthz
from deployground.config.models import AccessRule

ENVS = ["dev", "staging", "prod"]


class FakeResolver:
    def __init__(self, groups=None, channels=None):
        self.groups = groups or {}
        self.channels = channels or {}

    def usergroups_of(self, user):
        return set(self.groups.get(user, set()))

    def channels_of(self, user):
        return set(self.channels.get(user, set()))


class RaisingResolver:
    def usergroups_of(self, user):
        raise RuntimeError("paid_teams_only")

    def channels_of(self, user):
        raise RuntimeError("slack down")


class CountingResolver(FakeResolver):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = {"groups": 0, "channels": 0}

    def usergroups_of(self, user):
        self.calls["groups"] += 1
        return super().usergroups_of(user)

    def channels_of(self, user):
        self.calls["channels"] += 1
        return super().channels_of(user)


def make(rules, resolver=None):
    return AllowlistAuthz([AccessRule(**r) for r in rules], resolver or FakeResolver(), ENVS)


def test_user_rule_grants_scoped_envs():
    authz = make([{"user": "U1", "environments": ["dev", "staging"]}])
    assert authz.allowed_environments("U1") == {"dev", "staging"}
    assert authz.allowed_environments("U2") == set()


def test_group_and_channel_membership_grant():
    resolver = FakeResolver(groups={"U1": {"S1"}}, channels={"U1": {"C1"}})
    authz = make(
        [
            {"group": "S1", "environments": ["dev"]},
            {"channel": "C1", "environments": ["staging"]},
            {"group": "S9", "environments": ["prod"]},
        ],
        resolver,
    )
    assert authz.allowed_environments("U1") == {"dev", "staging"}


def test_default_deny_when_no_rule():
    assert make([]).allowed_environments("U1") == set()
    assert make([]).is_admin("U1") is False
    assert make([{"user": "U2"}]).allowed_environments("U1") == set()


def test_admin_can_manage_others():
    authz = make([{"user": "UA", "admin": True}])
    assert authz.is_admin("UA") is True
    assert authz.can_manage("UA", "U1") is True


def test_non_admin_cannot_manage_others():
    authz = make([{"user": "U1"}])
    assert authz.can_manage("U1", "U1") is True
    assert authz.can_manage("U1", "U2") is False


def test_membership_lookup_error_denies(caplog):
    authz = make(
        [
            {"group": "S1", "admin": True},
            {"channel": "C1"},
            {"user": "U1", "environments": ["dev"]},
        ],
        RaisingResolver(),
    )
    with caplog.at_level(logging.WARNING):
        # group/channel rules never match; only the direct user rule applies
        assert authz.allowed_environments("U1") == {"dev"}
        assert authz.is_admin("U1") is False
        assert authz.can_manage("U1", "U2") is False
    assert authz.allowed_environments("U2") == set()
    assert "membership lookup failed" in caplog.text


def test_star_expands_to_all_envs():
    authz = make([{"user": "U1"}])
    assert authz.allowed_environments("U1") == set(ENVS)
    authz = make([{"user": "U1", "environments": ["dev", "*"]}])
    assert authz.allowed_environments("U1") == set(ENVS)


def test_resolver_called_once_per_type_per_invocation():
    resolver = CountingResolver(groups={"U1": {"S1"}})
    authz = make(
        [{"group": "S1"}, {"group": "S2"}, {"channel": "C1"}, {"channel": "C2"}],
        resolver,
    )
    authz.allowed_environments("U1")
    assert resolver.calls == {"groups": 1, "channels": 1}
