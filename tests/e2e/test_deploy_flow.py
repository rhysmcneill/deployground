"""End-to-end wiring through ``build_app``: real config/catalog/capability/lifecycle/index/
bridge/reaper, with fakes only at the boundaries (git API, Slack client, status source, clock)."""

import itertools
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import yaml

from deployground.app import build_app
from deployground.config.models import Config
from deployground.git.base import PrResult
from deployground.lifecycle.reaper import Reaper
from deployground.slack import actions
from deployground.slack.notifier import SlackNotifier
from deployground.states import CompState
from deployground.status.base import StatusEvent
from tests.config.test_models import VALID
from tests.test_startup import FakeSlack, ScriptedSource

USER = "U012ABC"
CHANNEL = "C-DEPLOYS"
_ts = itertools.count(1)  # unique thread ts per post: update_thread dedupes per (channel, ts)
POOL_FILES = {f"homelab/pool-party/{n}/config.yaml" for n in ("web", "worker", "db", "_system")}


class E2ESlack(FakeSlack):
    def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ok": True, "ts": f"{next(_ts)}.000", "channel": kwargs["channel"]}

    def chat_postEphemeral(self, **kwargs):
        self.posts.append(kwargs)

    def users_info(self, **kwargs):
        return {"user": {"profile": {"email": "owner@example.com"}}}

    def views_publish(self, **kwargs):
        pass

    def conversations_open(self, **kwargs):
        return {"channel": {"id": "D1"}}


class RepoFakeGit:
    """In-memory repo: a merged (auto_merge) PR applies its changes to the file map."""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.prs: list[dict] = []

    def open_pr(self, repo, branch, title, changes, auto_merge):
        self.prs.append({"branch": branch, "title": title, "changes": list(changes)})
        if auto_merge:
            for change in changes:
                if change.content is None:
                    self.files.pop(change.path, None)
                else:
                    self.files[change.path] = change.content
        return PrResult(url=f"http://pr/{len(self.prs)}", number=len(self.prs), merged=auto_merge)

    def list_files(self, repo, path_prefix):
        return [(p, c) for p, c in self.files.items() if p.startswith(path_prefix)]


def config():
    data = deepcopy(VALID)
    data["catalog"]["pool-party"] = {
        "components": {
            name: {"image": f"ghcr.io/my-org/{name}", "defaultVersion": "v1.0.0", "port": 80}
            for name in ("web", "worker", "db")
        }
    }
    return Config.model_validate(data)


def make(git=None, slack=None):
    git = git or RepoFakeGit()
    slack = slack or E2ESlack()
    source = ScriptedSource()
    app = build_app(
        config(),
        {"DG_DEFAULT_CHANNEL": CHANNEL},
        slack_client=slack,
        git_client=git,
        status_source=source,
    )
    app.start()
    return app, git, slack, source


def ack(*_a, **_kw):
    pass


def submit(app, slack, stack, overrides=None, ttl="24h"):
    values = {
        "catalog": {"val": {"selected_option": {"value": stack}}},
        "stack": {"val": {"value": stack}},
        "env": {"val": {"selected_option": {"value": "homelab"}}},
        "ttl": {"val": {"value": ttl}},
    }
    for name, version in (overrides or {}).items():
        values[f"override::{name}"] = {"val": {"value": version}}
    body = {"user": {"id": USER}, "view": {"state": {"values": values}}}
    actions.handle_submit(app.deps, ack, body, slack)


def last_text(slack):
    return json.dumps(slack.updates[-1]["blocks"])


def completion_posts(slack):
    return [p for p in slack.posts if "thread_ts" in p]


def files_of(changes):
    return {c.path: yaml.safe_load(c.content) for c in changes if c.content is not None}


def test_single_service_deploy_to_healthy():
    app, git, slack, source = make()
    try:
        submit(app, slack, "api")

        assert len(git.prs) == 1
        files = files_of(git.prs[0]["changes"])
        assert sorted(files) == ["homelab/api/_system/config.yaml", "homelab/api/api/config.yaml"]
        block = files["homelab/api/api/config.yaml"]["deployground"]
        assert block["stack"] == "api" and block["owner"] == USER
        assert block["ownerEmail"] == "owner@example.com"
        assert "Deploying" in last_text(slack)
        ts = slack.updates[0]["ts"]
        assert block["threadTs"] == ts
        assert app.index.get("api").thread_ts == ts

        source.on_change(StatusEvent("api", "api", CompState.Healthy))

        assert "Running" in last_text(slack)
        assert slack.updates[-1]["ts"] == ts
        done = completion_posts(slack)
        assert len(done) == 1 and done[0]["thread_ts"] == ts
        assert "Deployment complete" in json.dumps(done[0]["blocks"])
    finally:
        app.stop()


def test_stack_with_overrides():
    app, git, slack, source = make()
    try:
        submit(app, slack, "pool-party", {"web": "v9.9.9", "db": "v2.0.0"})

        assert len(git.prs) == 1
        files = files_of(git.prs[0]["changes"])
        assert set(files) == POOL_FILES
        versions = {
            p.split("/")[2]: f["deployground"]["version"]
            for p, f in files.items()
            if p.split("/")[2] != "_system"
        }
        assert versions == {"web": "v9.9.9", "db": "v2.0.0", "worker": "v1.0.0"}
        dumped = {p.split("/")[2]: yaml.safe_dump(f) for p, f in files.items()}
        assert "ghcr.io/my-org/web:v9.9.9" in dumped["web"]
        assert "ghcr.io/my-org/db:v2.0.0" in dumped["db"]
        assert "ghcr.io/my-org/worker:v1.0.0" in dumped["worker"]

        source.on_change(StatusEvent("pool-party", "web", CompState.Healthy))
        source.on_change(StatusEvent("pool-party", "worker", CompState.Failed))

        assert "Failed" in last_text(slack)
        done = completion_posts(slack)
        assert len(done) == 1
        assert "worker failed" in json.dumps(done[0]["blocks"])
        everything = json.dumps(slack.updates) + json.dumps(slack.posts)
        assert "Deployment complete" not in everything and "Running" not in everything
    finally:
        app.stop()


def test_destroy_updates_thread_destroying_then_destroyed():
    app, git, slack, source = make()
    try:
        submit(app, slack, "pool-party")
        ts = slack.updates[0]["ts"]
        source.on_change(StatusEvent("pool-party", "web", CompState.Healthy))
        assert "pool-party" in app.bridge.statuses()
        before = len(slack.updates)

        body = {
            "user": {"id": USER},
            "channel": {"id": CHANNEL},
            "actions": [{"action_id": "confirm_destroy::pool-party", "value": "pool-party"}],
        }
        actions.handle_confirm_destroy(app.deps, ack, body, slack, lambda **_kw: None)

        assert len(git.prs) == 2
        deletes = git.prs[1]["changes"]
        assert {c.path for c in deletes} == POOL_FILES
        assert all(c.content is None for c in deletes)
        assert git.files == {}
        assert app.index.get("pool-party") is None
        assert "pool-party" not in app.bridge.statuses()  # _ForgetOnDestroy dropped bridge state

        new = slack.updates[before:]
        assert all(u["ts"] == ts for u in new)
        rendered = [json.dumps(u["blocks"]) for u in new]
        assert len(rendered) == 2
        assert "Destroying" in rendered[0] and "Destroyed" in rendered[1]
    finally:
        app.stop()


def test_expiry_reaps_all_components():
    app, git, slack, source = make()
    try:
        submit(app, slack, "pool-party", ttl="1h")
        assert app.index.get("pool-party").expires_at is not None
        clock = [datetime.now(UTC)]
        reaper = Reaper(
            app.index,
            app.deps.lifecycle,
            lambda: clock[0],
            SlackNotifier(slack),
            timedelta(minutes=10),
            timedelta(minutes=1),
        )

        reaper.tick()  # not yet expiring
        assert len(git.prs) == 1

        clock[0] += timedelta(hours=2)
        reaper.tick()

        assert len(git.prs) == 2
        assert {c.path for c in git.prs[1]["changes"]} == POOL_FILES
        assert all(c.content is None for c in git.prs[1]["changes"])
        assert app.index.get("pool-party") is None
        assert "Destroyed" in last_text(slack)
    finally:
        app.stop()


def test_index_rebuilds_from_git_after_restart():
    app, git, slack, source = make()
    try:
        submit(app, slack, "pool-party", {"web": "v3"}, ttl="2h")
        original = app.index.get("pool-party")
    finally:
        app.stop()

    # "Restart": a brand-new app over the same repo contents; nothing in memory survives.
    app2, _, _, _ = make(git=git, slack=E2ESlack())
    try:
        mine = app2.index.list_by_owner(USER)
        assert [s.name for s in mine] == ["pool-party"]
        restored = mine[0]
        assert restored.env == "homelab" and restored.owner == USER
        assert restored.thread_ts == original.thread_ts and restored.channel == original.channel
        assert set(restored.components) == {"web", "worker", "db"}
        assert restored.components["web"].version == "v3"
        assert restored.expires_at is not None
        assert abs(restored.expires_at - original.expires_at) < timedelta(seconds=1)

        later = original.expires_at + timedelta(minutes=1)
        assert [s.name for s in app2.index.list_expiring(later)] == ["pool-party"]
        assert app2.index.list_expiring(original.expires_at - timedelta(hours=1)) == []
    finally:
        app2.stop()
