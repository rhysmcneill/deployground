import threading
from copy import deepcopy

import pytest

from deployground.app import StartupError, asgi3_config, build_app
from deployground.config.models import Config
from deployground.lifecycle.service import DestroyResult
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

        def fake_destroy(user, stack):
            app.index.remove(stack)
            return DestroyResult("https://pr/1", 1, True)

        app.deps.lifecycle._lifecycle.destroy = fake_destroy
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


def test_failed_destroy_without_prior_status_does_not_repaint_running():
    from types import SimpleNamespace

    from deployground.app import _ForgetOnDestroy

    shown = []

    class Lifecycle:
        def destroy(self, user, stack):
            raise RuntimeError("pr failed")

    class Bridge:
        def statuses(self):
            return {}

        def forget(self, stack):
            raise AssertionError("must not forget on failure")

    record = SimpleNamespace(name="s")
    index = SimpleNamespace(get=lambda stack: record)
    wrapper = _ForgetOnDestroy(Lifecycle(), index, object(), Bridge())
    wrapper._show = lambda rec, state: shown.append(state)
    with pytest.raises(RuntimeError):
        wrapper.destroy("U1", "s")
    assert [s.name for s in shown] == ["Destroying"]  # no Running (or any) restore


def _destroy_wrapper(merged):
    from types import SimpleNamespace

    from deployground.app import _ForgetOnDestroy
    from deployground.lifecycle.service import DestroyResult

    shown, posts, forgot = [], [], []

    class Lifecycle:
        def destroy(self, user, stack):
            return DestroyResult("https://pr/9", 9, merged)

    class Bridge:
        def statuses(self):
            return {}

        def forget(self, stack):
            forgot.append(stack)

    class Client:
        def chat_postMessage(self, **kwargs):
            posts.append(kwargs)

    record = SimpleNamespace(name="s", channel="C1", thread_ts="1.1")
    index = SimpleNamespace(get=lambda stack: record)
    wrapper = _ForgetOnDestroy(Lifecycle(), index, Client(), Bridge())
    wrapper._show = lambda rec, state: shown.append(state.name)
    return wrapper, shown, posts, forgot


def test_merged_destroy_renders_destroyed():
    wrapper, shown, posts, forgot = _destroy_wrapper(merged=True)
    wrapper.destroy("U1", "s")
    assert shown == ["Destroying", "Destroyed"] and posts == [] and forgot == ["s"]


def test_unmerged_destroy_stays_destroying_and_posts_pr_url():
    wrapper, shown, posts, forgot = _destroy_wrapper(merged=False)
    wrapper.destroy("U1", "s")
    assert shown == ["Destroying"] and forgot == ["s"]
    assert len(posts) == 1
    assert posts[0]["channel"] == "C1" and posts[0]["thread_ts"] == "1.1"
    assert "https://pr/9" in posts[0]["text"] and "awaiting" in posts[0]["text"]


def test_unmerged_destroy_post_failure_is_non_fatal():
    wrapper, shown, _, _ = _destroy_wrapper(merged=False)

    def boom(**kwargs):
        raise RuntimeError("slack down")

    wrapper._client.chat_postMessage = boom
    wrapper.destroy("U1", "s")  # must not raise
    assert shown == ["Destroying"]
