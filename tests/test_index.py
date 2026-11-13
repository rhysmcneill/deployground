import logging
from datetime import UTC, datetime

import pytest
import yaml

from deployground.config.models import DeployTargetCfg, RepoRef, TokenRef
from deployground.index import StackIndex
from deployground.metadata import ComponentRecord, StackRecord, to_metadata_block

REPO = RepoRef(owner="acme", name="deploys", branch="main", authRef=TokenRef(tokenEnv="TOK"))
CFG = DeployTargetCfg(
    type="applicationset", repo="deploys", pathTemplate="stacks/{stack}/{component}.yaml"
)

T1 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


class FakeGit:
    def __init__(self, files):
        self.files = files
        self.calls = []

    def list_files(self, repo, path_prefix):
        self.calls.append((repo, path_prefix))
        return self.files


def comp(stack, component, owner="alice", expires_at=T1):
    return ComponentRecord(
        stack=stack,
        component=component,
        owner=owner,
        owner_email=f"{owner}@example.com",
        env="dev",
        expires_at=expires_at,
        version="1.0",
        thread_ts="1.1",
        channel="C1",
    )


def file_for(record):
    path = f"stacks/{record.stack}/{record.component}.yaml"
    return path, yaml.safe_dump({"kind": "x", "deployground": to_metadata_block(record)})


def stack_record(name, owner="alice", expires_at=T1):
    return StackRecord(
        name=name, env="dev", owner=owner, expires_at=expires_at, thread_ts="1.1", channel="C1"
    )


def make_index(*records, extra=()):
    git = FakeGit([file_for(r) for r in records] + list(extra))
    index = StackIndex(git, CFG, REPO)
    index.rebuild()
    return index, git


def test_rebuild_groups_components_by_stack():
    index, git = make_index(comp("s1", "api"), comp("s1", "web"), comp("s2", "api", owner="bob"))
    assert git.calls == [(REPO, "stacks/")]
    s1 = index.get("s1")
    assert s1 is not None
    assert set(s1.components) == {"api", "web"}
    assert (s1.owner, s1.env, s1.expires_at, s1.thread_ts, s1.channel) == (
        "alice",
        "dev",
        T1,
        "1.1",
        "C1",
    )
    s2 = index.get("s2")
    assert s2 is not None
    assert s2.owner == "bob"
    assert index.get("nope") is None
    assert len(index.list_live()) == 2


def test_list_expiring_filters_by_time():
    index, _ = make_index(
        comp("early", "a", expires_at=T1),
        comp("late", "a", expires_at=T2),
        comp("never", "a", expires_at=None),
    )
    assert [s.name for s in index.list_expiring(T1)] == ["early"]
    assert {s.name for s in index.list_expiring(T2)} == {"early", "late"}


def test_list_by_owner():
    index, _ = make_index(comp("s1", "a"), comp("s2", "a", owner="bob"), comp("s3", "a"))
    assert {s.name for s in index.list_by_owner("alice")} == {"s1", "s3"}
    assert index.list_by_owner("carol") == []


def test_count_live_for():
    index, _ = make_index(comp("s1", "a"), comp("s2", "a", owner="bob"), comp("s3", "a"))
    assert index.count_live_for("alice") == 2
    assert index.count_live_for("bob") == 1
    assert index.count_live_for("carol") == 0


def test_upsert_and_remove_update_cache():
    index, _ = make_index()
    index.upsert(stack_record("s1"))
    got = index.get("s1")
    assert got is not None
    assert got.owner == "alice"
    index.upsert(stack_record("s1", owner="bob"))
    got = index.get("s1")
    assert got is not None
    assert got.owner == "bob"
    assert index.count_live_for("alice") == 0
    index.remove("s1")
    assert index.get("s1") is None
    index.remove("s1")  # removing a missing stack is a no-op


@pytest.mark.parametrize(
    "bad",
    [
        ("stacks/bad/no-block.yaml", "kind: x\n"),
        ("stacks/bad/not-yaml.yaml", "a: [unclosed\n"),
        ("stacks/bad/list.yaml", "- 1\n- 2\n"),
    ],
)
def test_rebuild_skips_malformed_file_and_logs(bad, caplog):
    with caplog.at_level(logging.WARNING, logger="deployground.index"):
        index, _ = make_index(comp("good", "api"), extra=[bad])
    assert [s.name for s in index.list_live()] == ["good"]
    assert bad[0] in caplog.text
