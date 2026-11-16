import threading
from copy import deepcopy

import pytest

from deployground.app import StartupError, asgi3_config, build_app
from deployground.config.models import Config
from deployground.states import CompState
from deployground.status.base import StatusEvent
from deployground.status.notifications import NotificationsSource

from .config.test_models import VALID
from .test_startup import FakeGit, FakeSlack, ScriptedSource, component_file, make_app


def test_webhook_config_is_asgi3():
    source = NotificationsSource(Config.model_validate(deepcopy(VALID)).statusSource)
    loaded = asgi3_config(source.asgi_app, "127.0.0.1", 0)
    loaded.load()
    assert loaded.interface == "asgi3"


def test_dead_status_source_does_not_stop_reaper():
    class Dead(ScriptedSource):
        def start(self, on_change):
            raise OSError("port in use")

    app = make_app(FakeGit([component_file("api")]), Dead())
    started = threading.Event()
    app.reaper.run = lambda stop: started.set()
    app.start()
    app.stop()
    assert started.wait(2)


def test_redeploy_after_destroy_reemits_completion():
    slack = FakeSlack()
    source = ScriptedSource()
    app = make_app(FakeGit([component_file("api")]), source, slack)
    app.start()
    try:
        source.on_change(StatusEvent("api", "api", CompState.Healthy))
        assert len(slack.posts) == 1
        assert app.bridge.statuses()

        record = app.index.get("api")
        app.deps.lifecycle._lifecycle.destroy = lambda user, stack: app.index.remove(stack)
        app.deps.lifecycle.destroy("U1", "api")
        assert app.bridge.statuses() == {}

        source.on_change(StatusEvent("api", "api", CompState.Healthy))  # late event: ignored
        assert app.bridge.statuses() == {}

        app.index.upsert(record)  # redeploy, same name
        source.on_change(StatusEvent("api", "api", CompState.Healthy))
        assert len(slack.posts) == 2
    finally:
        app.stop()


def test_missing_image_deploy_capability_is_startup_error():
    data = deepcopy(VALID)
    data["capabilities"] = {}
    with pytest.raises(StartupError, match="image-deploy"):
        build_app(
            Config.model_validate(data),
            {},
            slack_client=FakeSlack(),
            git_client=FakeGit(),
            status_source=ScriptedSource(),
        )


def test_slack_auth_failure_is_startup_error():
    def boom(token):
        raise RuntimeError("invalid_auth")

    with pytest.raises(StartupError, match="Slack auth failed: invalid_auth"):
        build_app(
            Config.model_validate(deepcopy(VALID)),
            {"SLACK_BOT_TOKEN": "xoxb-x"},
            git_client=FakeGit(),
            status_source=ScriptedSource(),
            app_factory=boom,
        )
