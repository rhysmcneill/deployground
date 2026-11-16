from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from deployground.__main__ import main
from deployground.app import StartupError, build_app
from deployground.config.loader import load_config
from deployground.config.models import Config
from deployground.metadata import ComponentRecord, to_metadata_block
from deployground.states import CompState
from deployground.status.base import StatusEvent

from .config.test_models import VALID

EXPIRES = datetime.now(UTC) + timedelta(days=30)


def component_file(component):
    record = ComponentRecord(
        stack="api",
        component=component,
        owner="U1",
        owner_email="u1@example.com",
        env="homelab",
        expires_at=EXPIRES,
        version="v1",
        thread_ts="111.222",
        channel="C1",
    )
    path = f"homelab/api/{component}/config.yaml"
    return path, yaml.safe_dump({"deployground": to_metadata_block(record)})


class FakeGit:
    def __init__(self, files=(), error=None):
        self.files = list(files)
        self.error = error
        self.list_calls = []

    def list_files(self, repo, path_prefix):
        self.list_calls.append((repo.name, path_prefix))
        if self.error:
            raise self.error
        return self.files


class FakeSlack:
    def __init__(self):
        self.updates = []
        self.posts = []

    def chat_update(self, **kwargs):
        self.updates.append(kwargs)

    def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ok": True}


class ScriptedSource:
    def __init__(self):
        self.on_change = None
        self.stopped = False

    def start(self, on_change):
        self.on_change = on_change

    def stop(self):
        self.stopped = True


def make_app(git, source=None, slack=None):
    config = Config.model_validate(deepcopy(VALID))
    return build_app(
        config,
        {},
        slack_client=slack or FakeSlack(),
        git_client=git,
        status_source=source or ScriptedSource(),
    )


def test_startup_rebuilds_index_from_git():
    git = FakeGit([component_file("api")])
    app = make_app(git)

    app.start()
    try:
        assert git.list_calls == [("gitops", "")]
        record = app.index.get("api")
        assert record is not None and record.owner == "U1" and record.thread_ts == "111.222"
    finally:
        app.stop()


def test_startup_fails_fast_when_git_unreachable():
    app = make_app(FakeGit(error=RuntimeError("boom")))

    with pytest.raises(StartupError, match="boom"):
        app.start()


def test_build_app_wires_status_to_progress():
    slack = FakeSlack()
    source = ScriptedSource()
    app = make_app(FakeGit([component_file("api"), component_file("web")]), source, slack)
    app.start()
    try:
        assert source.on_change is not None

        source.on_change(StatusEvent("api", "api", CompState.Healthy))
        assert len(slack.updates) == 1
        update = slack.updates[0]
        assert (update["channel"], update["ts"]) == ("C1", "111.222")
        assert "Deploying" in update["blocks"][0]["text"]["text"]
        assert slack.posts == []  # web is still progressing: not complete

        source.on_change(StatusEvent("api", "web", CompState.Healthy))
        assert "Running" in slack.updates[-1]["blocks"][0]["text"]["text"]
        assert len(slack.posts) == 1
        assert slack.posts[0]["thread_ts"] == "111.222"
        assert "Deployment complete" in slack.posts[0]["blocks"][0]["text"]["text"]
    finally:
        app.stop()


def test_failed_component_posts_failure_to_thread():
    slack = FakeSlack()
    source = ScriptedSource()
    app = make_app(FakeGit([component_file("api")]), source, slack)
    app.start()
    try:
        source.on_change(StatusEvent("api", "api", CompState.Failed))
        assert "api failed" in slack.posts[0]["blocks"][0]["text"]["text"]
    finally:
        app.stop()


def test_startup_fails_fast_on_invalid_config(tmp_path, capsys):
    broken = deepcopy(VALID)
    broken["environments"]["homelab"]["repo"] = "missing"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(broken))

    with pytest.raises(ValueError, match="missing"):
        load_config(path, {})

    assert main(["--config", str(path)]) == 2
    assert "missing" in capsys.readouterr().err
