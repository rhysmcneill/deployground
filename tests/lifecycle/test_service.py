from datetime import UTC, datetime, timedelta

import pytest
import yaml

from deployground.capability.base import RenderInputs
from deployground.git.base import FileChange, PrResult
from deployground.lifecycle.service import Denied, LifecycleService
from deployground.metadata import ComponentRecord, StackRecord
from tests.capability.test_image_deploy import make_cfg, make_ctx
from tests.deploytarget.test_applicationset import REPO

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


class FakeAuthz:
    def __init__(self, envs=("dev", "prod"), admin=False, manage=True):
        self.envs, self.admin, self.manage = set(envs), admin, manage

    def allowed_environments(self, user):
        return self.envs

    def is_admin(self, user):
        return self.admin

    def can_manage(self, user, stack_owner):
        return self.manage


class FakeCapability:
    def __init__(self):
        self.rendered: list[RenderInputs] = []
        self.destroyed: list[StackRecord] = []

    def render(self, inputs, ctx):
        self.rendered.append(inputs)
        return [FileChange(REPO, "dev/demo/api/config.yaml", "x")]

    def destroy(self, stack, ctx):
        self.destroyed.append(stack)
        return [FileChange(REPO, f"{stack.env}/{stack.name}/api/config.yaml", None)]


class FakeGit:
    def __init__(self):
        self.prs = []

    def open_pr(self, repo, branch, title, changes, auto_merge):
        self.prs.append((repo, branch, title, changes, auto_merge))
        return PrResult(url="https://pr/1", number=1, merged=auto_merge)


class FakeIndex:
    def __init__(self, live=0):
        self.live = live
        self.records = {}
        self.removed = []

    def count_live_for(self, user):
        return self.live

    def upsert(self, record):
        self.records[record.name] = record

    def get(self, stack):
        return self.records.get(stack)

    def remove(self, stack):
        self.removed.append(stack)


def make_service(authz=None, index=None, config=None, max_live=10):
    config = config or make_cfg()
    git, cap = FakeGit(), FakeCapability()
    index = index or FakeIndex()
    svc = LifecycleService(
        authz=authz or FakeAuthz(),
        capability=cap,
        git=git,
        index=index,
        environments=config.environments,
        repos=config.git.repos,
        lifecycle_cfg=config.lifecycle.model_copy(update={"maxLivePerUser": max_live}),
        render_ctx=make_ctx(config),
        clock=lambda: NOW,
    )
    return svc, git, cap, index


def deploy(svc, user="U1", env="dev", ttl=None, email="a@example.com", overrides=None):
    return svc.deploy(
        user=user,
        owner_email=email,
        stack="demo",
        env=env,
        overrides=overrides or {},
        ttl=ttl,
        thread_ts="1.1",
        channel="C1",
    )


def test_deploy_denied_for_disallowed_env():
    svc, git, _, _ = make_service(authz=FakeAuthz(envs=("dev",)))
    with pytest.raises(Denied):
        deploy(svc, env="prod")
    assert git.prs == []


def test_deploy_opens_single_pr_and_upserts_index():
    svc, git, _, index = make_service()
    result = deploy(svc, overrides={"api": "1.2.3"})
    assert len(git.prs) == 1
    repo, _branch, _title, _changes, auto_merge = git.prs[0]
    assert repo == REPO and auto_merge is True
    assert (result.pr_url, result.pr_number, result.merged) == ("https://pr/1", 1, True)
    record = index.records["demo"]
    assert result.stack_record is record
    assert record.expires_at == NOW + timedelta(hours=24)
    assert {n: c.version for n, c in record.components.items()} == {
        "api": "1.2.3",
        "web": "2.0.0",
    }
    assert record.components["api"].owner == "U1"


def test_deploy_takes_owner_from_payload_not_store():
    svc, _, cap, index = make_service()
    deploy(svc, user="U9", email="u9@example.com")
    assert cap.rendered[0].owner == "U9"
    assert cap.rendered[0].owner_email == "u9@example.com"
    assert index.records["demo"].owner == "U9"


def test_ttl_none_requires_admin():
    svc, _, _, _ = make_service()
    with pytest.raises(Denied):
        deploy(svc, ttl="none")
    svc, _, cap, _ = make_service(authz=FakeAuthz(admin=True))
    result = deploy(svc, ttl="none")
    assert result.expires_at is None
    assert cap.rendered[0].expires_at is None


def test_ttl_capped_at_env_max():
    svc, _, cap, _ = make_service()  # dev: default 24h, max 72h
    result = deploy(svc, ttl="200h")
    assert result.expires_at == NOW + timedelta(hours=72)
    assert cap.rendered[0].ttl == "72h"


def test_max_live_per_user_enforced_from_index():
    svc, git, _, _ = make_service(index=FakeIndex(live=3), max_live=3)
    with pytest.raises(Denied):
        deploy(svc)
    assert git.prs == []


def test_destroy_requires_manage_permission():
    record = StackRecord("demo", "dev", "U1", NOW, "1.1", "C1")
    svc, git, _, index = make_service(authz=FakeAuthz(manage=False))
    index.records["demo"] = record
    with pytest.raises(Denied):
        svc.destroy("U2", "demo")
    assert git.prs == [] and index.removed == []

    svc, git, cap, index = make_service()
    index.records["demo"] = record
    svc.destroy("U1", "demo")
    assert len(git.prs) == 1 and cap.destroyed == [record]
    assert index.removed == ["demo"]


def test_extend_updates_expiry_in_all_component_files():
    comps = {
        n: ComponentRecord("demo", n, "U1", "a@example.com", "dev", NOW, v, "1.1", "C1")
        for n, v in (("api", "1.0.0"), ("web", "2.0.0"))
    }
    record = StackRecord("demo", "dev", "U1", NOW, "1.1", "C1", comps)
    svc, git, _, index = make_service()
    index.records["demo"] = record
    svc.extend("U1", "demo", "6h")
    assert len(git.prs) == 1
    changes = git.prs[0][3]
    assert len(changes) == 2
    for change in changes:
        block = yaml.safe_load(change.content)["deployground"]
        assert block["expiresAt"] == "2026-11-12T15:00:00Z"
    updated = index.records["demo"]
    new_expiry = NOW + timedelta(hours=6)
    assert updated.expires_at == new_expiry
    assert all(c.expires_at == new_expiry for c in updated.components.values())
    assert {c.version for c in updated.components.values()} == {"1.0.0", "2.0.0"}


def test_extend_without_expiry_denied():
    record = StackRecord("demo", "dev", "U1", None, "1.1", "C1")
    svc, git, _, index = make_service()
    index.records["demo"] = record
    with pytest.raises(Denied, match="no expiry"):
        svc.extend("U1", "demo", "1h")
    assert git.prs == []
