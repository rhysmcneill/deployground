"""Behavior tests for the Slack action handlers.

Approach: call the handler functions directly with fake ack/client/respond/body dicts
(no bolt App needed), recording what they do. app wiring gets one registration smoke test.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from deployground.catalog import Catalog
from deployground.config.models import Config, Environment
from deployground.lifecycle.service import Denied
from deployground.metadata import ComponentRecord, StackRecord
from deployground.slack import actions
from deployground.slack.actions import Deps
from tests.slack.test_forms import CFG, FakeAuthz

CHANNEL = "C-DEPLOYS"


class FakeClient:
    def __init__(self):
        self.calls = []

    def _rec(self, name, **kw):
        self.calls.append((name, kw))
        return {"ok": True, "ts": "111.222", "channel": kw.get("channel", CHANNEL)}

    def __getattr__(self, name):
        return lambda **kw: self._rec(name, **kw)

    def users_info(self, **kw):
        self.calls.append(("users_info", kw))
        return {"user": {"profile": {"email": "u@example.com"}}}

    def named(self, name):
        return [kw for n, kw in self.calls if n == name]


class FakeLifecycle:
    def __init__(self, deploy_exc=None):
        self.calls = []
        self.deploy_exc = deploy_exc

    def deploy(self, user, owner_email, catalog, stack, env, overrides, ttl, thread_ts, channel):
        self.calls.append(
            ("deploy", user, owner_email, catalog, stack, env, overrides, ttl, thread_ts, channel)
        )
        if self.deploy_exc:
            raise self.deploy_exc
        rec = StackRecord(
            name=stack, catalog=catalog, env=env, owner=user, expires_at=None,
            thread_ts=thread_ts, channel=channel,
        )  # fmt: skip
        return SimpleNamespace(
            pr_url="http://pr/1", pr_number=1, merged=True, expires_at=None, stack_record=rec
        )

    def extend(self, user, stack, by):
        self.calls.append(("extend", user, stack, by))
        return SimpleNamespace(pr_url="http://pr/2", pr_number=2, merged=True, expires_at=None)

    def destroy(self, user, stack):
        self.calls.append(("destroy", user, stack))
        return SimpleNamespace(pr_url="http://pr/3", pr_number=3, merged=True)


class FakeIndex:
    def __init__(self, records):
        self.records = {r.name: r for r in records}

    def get(self, stack):
        return self.records.get(stack)

    def list_by_owner(self, user):
        return [r for r in self.records.values() if r.owner == user]


class FakeCapability:
    def __init__(self, approval=False):
        self.approval = approval

    def needs_approval(self, inputs, env):
        return self.approval


def _record():
    comp = ComponentRecord(
        stack="shop", component="web", owner="U1", owner_email="e", env="dev",
        expires_at=None, version="v1", thread_ts="1.0", channel="C1", catalog="shop",
    )  # fmt: skip
    return StackRecord(
        name="shop", env="dev", owner="U1", expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        thread_ts="1.0", channel="C1", components={"web": comp, "worker": comp}, catalog="shop",
    )  # fmt: skip


class OwnerAuthz(FakeAuthz):
    def can_manage(self, user, stack_owner):
        return user == stack_owner


def make_deps(lifecycle=None, approval=False):
    cfg = Config.model_validate(CFG)
    acks = []
    deps = Deps(
        lifecycle=lifecycle or FakeLifecycle(),
        catalog=Catalog(lambda: cfg),
        authz=OwnerAuthz({"dev", "staging"}),
        index=FakeIndex([_record()]),
        capability=FakeCapability(approval),
        environments={k: Environment.model_validate(v) for k, v in CFG["environments"].items()},
        reaper_ack=acks.append,
    )
    return deps, acks


class Ack:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append(kw)


class Respond:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append(kw)


def _select(value):
    return {"val": {"selected_option": {"value": value}}}


def _text(value):
    return {"val": {"value": value}}


def _submit_body(values=None):
    state = {
        "catalog": _select("shop"),
        "stack": _text("new-deploy"),
        "env": _select("dev"),
        "ttl": _text("24h"),
    }
    state.update(values or {})
    return {
        "user": {"id": "U1"},
        "view": {"callback_id": "deploy_submit", "state": {"values": state}},
    }


def _button_body(action_id, stack="shop"):
    return {
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "response_url": "http://resp",
        "actions": [{"action_id": action_id, "value": stack}],
    }


def test_destroy_requires_confirmation_before_acting():
    deps, _ = make_deps()
    client, ack = FakeClient(), Ack()
    actions.handle_destroy_click(deps, ack, _button_body("destroy::shop"), client)
    assert ack.calls
    assert not [c for c in deps.lifecycle.calls if c[0] == "destroy"]
    posted = client.named("chat_postEphemeral")
    assert len(posted) == 1
    text = json.dumps(posted[0]["blocks"])
    assert "shop" in text and "dev" in text and "2 component" in text
    assert "confirm_destroy::shop" in text and "cancel_destroy::shop" in text


def test_confirm_triggers_destroy():
    deps, _ = make_deps()
    client, ack, respond = FakeClient(), Ack(), Respond()
    actions.handle_confirm_destroy(
        deps, ack, _button_body("confirm_destroy::shop"), client, respond
    )
    assert ("destroy", "U1", "shop") in deps.lifecycle.calls
    assert {"delete_original": True} in respond.calls


def test_confirm_destroy_by_non_owner_is_denied_before_destroy():
    deps, _ = make_deps()
    client, ack, respond = FakeClient(), Ack(), Respond()
    body = _button_body("confirm_destroy::shop")
    body["user"] = {"id": "U-OTHER"}
    actions.handle_confirm_destroy(deps, ack, body, client, respond)
    assert not [c for c in deps.lifecycle.calls if c[0] == "destroy"]
    assert not client.named("chat_update")  # no Destroying repaint of the owner's thread
    msg = client.named("chat_postEphemeral")[0]["text"]
    assert "not allowed" in msg


SECRET = "https://github.com/org/secret-repo/path/x"


def test_deploy_unexpected_error_hides_internal_text():
    deps, _ = make_deps(FakeLifecycle(deploy_exc=RuntimeError(SECRET)))
    client = FakeClient()
    actions.handle_submit(deps, Ack(), _submit_body(), client)
    rendered = json.dumps(client.calls, default=str)
    assert SECRET not in rendered and "secret-repo" not in rendered
    assert "unexpected error occurred" in json.dumps(client.named("chat_update")[-1]["blocks"])


def test_destroy_unexpected_error_hides_internal_text():
    lifecycle = FakeLifecycle()

    def boom(user, stack):
        raise RuntimeError(SECRET)

    lifecycle.destroy = boom
    deps, _ = make_deps(lifecycle)
    client = FakeClient()
    actions.handle_confirm_destroy(
        deps, Ack(), _button_body("confirm_destroy::shop"), client, Respond()
    )
    text = client.named("chat_postEphemeral")[0]["text"]
    assert SECRET not in text and "unexpected error occurred" in text


def test_submit_posts_message_then_calls_deploy_with_thread_ts():
    deps, _ = make_deps()
    client, ack = FakeClient(), Ack()
    actions.handle_submit(deps, ack, _submit_body(), client)
    names = [n for n, _ in client.calls]
    assert names.index("chat_postMessage") < len(names)
    assert client.named("chat_postMessage")[0]["channel"] == "U1"
    deploy = deps.lifecycle.calls[0]
    assert deploy[0] == "deploy"
    assert deploy[3:5] == ("shop", "new-deploy")
    assert deploy[8] == "111.222" and deploy[9] == "U1"
    assert deploy[2] == "u@example.com"
    update = client.named("chat_update")[-1]
    assert update["ts"] == "111.222"
    assert "Deploying" in json.dumps(update["blocks"])
    assert "awaiting approval" not in json.dumps(update["blocks"])


def test_submit_rejects_a_stack_name_already_live():
    deps, _ = make_deps()
    client, ack = FakeClient(), Ack()
    actions.handle_submit(deps, ack, _submit_body({"stack": _text("shop")}), client)
    assert ack.calls[0]["response_action"] == "errors"
    assert "stack" in ack.calls[0]["errors"]
    assert deps.lifecycle.calls == []


def test_submit_notes_approval_when_required():
    deps, _ = make_deps(approval=True)
    client = FakeClient()
    actions.handle_submit(deps, Ack(), _submit_body(), client)
    assert "awaiting approval" in json.dumps(client.named("chat_update")[-1]["blocks"])


def test_submit_shows_inline_errors_on_bad_input():
    deps, _ = make_deps()
    client, ack = FakeClient(), Ack()
    actions.handle_submit(deps, ack, _submit_body({"ttl": _text("bogus")}), client)
    assert ack.calls[0]["response_action"] == "errors"
    assert "ttl" in ack.calls[0]["errors"]
    assert not deps.lifecycle.calls
    assert not client.named("chat_postMessage")


def test_deploy_failure_marks_thread_failed():
    deps, _ = make_deps(lifecycle=FakeLifecycle(deploy_exc=RuntimeError("git down")))
    client = FakeClient()
    actions.handle_submit(deps, Ack(), _submit_body(), client)
    update = client.named("chat_update")[-1]
    assert update["ts"] == "111.222"
    assert "failed" in json.dumps(update["blocks"]).lower()
    assert "Deploying" not in json.dumps(update["blocks"])


def test_denied_marks_thread_failed_and_tells_user():
    deps, _ = make_deps(lifecycle=FakeLifecycle(deploy_exc=Denied("nope")))
    client = FakeClient()
    actions.handle_submit(deps, Ack(), _submit_body(), client)
    assert "nope" in json.dumps(client.named("chat_update")[-1]["blocks"])
    assert "nope" in client.named("chat_postEphemeral")[0]["text"]


def test_prefilled_button_opens_modal_with_values():
    deps, _ = make_deps()
    client, ack = FakeClient(), Ack()
    value = json.dumps(
        {
            "catalog": "shop",
            "stack": "shop",
            "env": "staging",
            "overrides": {"web": {"version": "v9"}},
            "ttl": "2h",
        }
    )
    body = {
        "user": {"id": "U1"},
        "trigger_id": "T1",
        "actions": [{"action_id": "deploy_prefill", "value": value}],
    }
    actions.handle_prefill(deps, ack, body, client)
    view = client.named("views_open")[0]["view"]
    text = json.dumps(view)
    assert client.named("views_open")[0]["trigger_id"] == "T1"
    assert '"initial_option": {"text": {"type": "plain_text", "text": "staging"}' in text
    assert '"initial_value": "v9"' in text and '"initial_value": "2h"' in text


def test_keep_button_clears_warning_without_changing_date():
    deps, acks = make_deps()
    actions.handle_keep(deps, Ack(), _button_body("keep::shop"), FakeClient())
    assert acks == ["shop"]
    assert not deps.lifecycle.calls


def test_extend_button_calls_extend():
    deps, acks = make_deps()
    actions.handle_extend(deps, Ack(), _button_body("extend::shop"), FakeClient())
    assert ("extend", "U1", "shop", "24h") in deps.lifecycle.calls
    assert acks == ["shop"]


def test_cancel_deletes_confirmation_without_destroy():
    deps, _ = make_deps()
    respond = Respond()
    actions.handle_cancel_destroy(deps, Ack(), _button_body("cancel_destroy::shop"), respond)
    assert respond.calls == [{"delete_original": True}]
    assert not deps.lifecycle.calls


def test_stack_suggestions_filter_by_query():
    deps, _ = make_deps()
    ack = Ack()
    actions.handle_catalog_options(deps, ack, {"value": "sh"})
    assert [o["value"] for o in ack.calls[0]["options"]] == ["shop"]


def test_stack_change_updates_view_preserving_values():
    deps, _ = make_deps()
    client = FakeClient()
    body = _submit_body({"override_components": {"val": {"selected_options": [{"value": "web"}]}},
                         "override::web": _text("v7")})  # fmt: skip
    body["view"].update(id="V1", hash="H1")
    actions.handle_modal_change(deps, Ack(), body, client)
    upd = client.named("views_update")[0]
    assert upd["view_id"] == "V1"
    assert '"initial_value": "v7"' in json.dumps(upd["view"])


def test_build_slack_app_registers():
    from slack_bolt import App

    from deployground.slack.app import build_slack_app

    deps, _ = make_deps()
    app = App(token="xoxb-test", signing_secret="s", token_verification_enabled=False)
    assert build_slack_app(deps, app) is app
