"""Slack interaction handlers: plain functions taking ``Deps`` plus bolt-style ack/client/body.

Registered on a bolt App by ``slack/app.py``; kept free of bolt imports so tests call them
directly with fakes.

Design notes:
- Denied on submit: the progress message already exists, so it is edited to Failed with the
  reason, and the user also gets an ephemeral with the reason (no modal error, since the
  modal is already closed by the time lifecycle runs).
- needs_approval: deploy() always runs (it opens a non-auto-merge PR for such envs; the PR
  review is the approval gate, spec section 12). When the capability says approval is
  needed, the progress message carries an "awaiting approval (PR review)" line.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deployground.authz.base import AuthzProvider
from deployground.capability.base import Capability, RenderInputs
from deployground.catalog import Catalog
from deployground.config.models import Environment
from deployground.index import StackIndex
from deployground.lifecycle.service import Denied
from deployground.metadata import StackRecord
from deployground.slack.forms import (
    ACTION_ID,
    OVERRIDE_PICKER_BLOCK,
    OVERRIDE_PREFIX,
    STATIC_SELECT_LIMIT,
    DeployInputs,
    build_deploy_modal,
    parse_submission,
)
from deployground.slack.home import build_home
from deployground.slack.progress import completion_blocks, render_progress, update_thread
from deployground.states import CompState, StackState

logger = logging.getLogger(__name__)

EXTEND_BY = "24h"
KEEP_PREFIX = "keep::"
DESTROY_NOW_PREFIX = "destroy_now::"
CONFIRM_DESTROY_PREFIX = "confirm_destroy::"
CANCEL_DESTROY_PREFIX = "cancel_destroy::"
PREFILL_ACTION = "deploy_prefill"


@dataclass
class Deps:
    """Everything the handlers need; Task 19 constructs this."""

    lifecycle: Any  # LifecycleService (deploy/extend/destroy)
    catalog: Catalog
    authz: AuthzProvider
    index: StackIndex
    capability: Capability
    environments: dict[str, Environment]
    reaper_ack: Callable[[str], None]  # Task 18: acknowledge_warning(stack)
    channel_default: str
    statuses: Callable[[], dict[str, StackState] | None] = lambda: None


def _user(body: dict[str, Any]) -> str:
    return body["user"]["id"]


def _stack_from_action(body: dict[str, Any]) -> str:
    action = body["actions"][0]
    return action["action_id"].split("::", 1)[1]


def _notify(client: Any, body: dict[str, Any], text: str) -> None:
    """Ephemeral in the clicked channel; DM when there is no channel (App Home)."""
    user = _user(body)
    channel = (body.get("channel") or {}).get("id")
    if channel:
        client.chat_postEphemeral(channel=channel, user=user, text=text)
    else:
        client.chat_postMessage(channel=user, text=text)


def _refresh_home(deps: Deps, client: Any, user: str) -> None:
    try:
        client.views_publish(
            user_id=user,
            view=build_home(user, deps.index, deps.authz, statuses=deps.statuses() or None),
        )
    except Exception:
        logger.warning("home refresh failed for %s", user, exc_info=True)


# --- entry points -------------------------------------------------------------------


def handle_open_form(deps: Deps, ack: Callable, body: dict[str, Any], client: Any) -> None:
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view=build_deploy_modal(_user(body), deps.catalog, deps.authz),
    )


def handle_prefill(deps: Deps, ack: Callable, body: dict[str, Any], client: Any) -> None:
    ack()
    try:
        data = json.loads(body["actions"][0].get("value") or "{}")
        prefill = DeployInputs(
            stack=data["stack"],
            env=data["env"],
            overrides=dict(data.get("overrides") or {}),
            ttl=data.get("ttl") or "24h",
        )
    except (ValueError, KeyError, TypeError):
        _notify(client, body, "That deploy button is malformed.")
        return
    client.views_open(
        trigger_id=body["trigger_id"],
        view=build_deploy_modal(_user(body), deps.catalog, deps.authz, prefill=prefill),
    )


def handle_modal_change(deps: Deps, ack: Callable, body: dict[str, Any], client: Any) -> None:
    """Stack or override-component selection changed: rebuild the modal, keeping values."""
    ack()
    view = body["view"]
    values = view["state"]["values"]

    def selected(block: str) -> str:
        return ((values.get(block, {}).get(ACTION_ID) or {}).get("selected_option") or {}).get(
            "value", ""
        )

    picker = values.get(OVERRIDE_PICKER_BLOCK, {}).get(ACTION_ID) or {}
    chosen = [o["value"] for o in picker.get("selected_options") or []]
    overrides: dict[str, str] = {}
    for name in chosen:
        text = (values.get(f"{OVERRIDE_PREFIX}{name}", {}).get(ACTION_ID) or {}).get("value")
        if text:
            overrides[name] = text
    # Selected-but-untyped components get a row prefilled with the catalog default.
    for name in chosen:
        overrides.setdefault(name, _default_version(deps, selected("stack"), name))
    ttl = ((values.get("ttl", {}).get(ACTION_ID) or {}).get("value") or "24h").strip() or "24h"
    prefill = DeployInputs(
        stack=selected("stack"), env=selected("env"), overrides=overrides, ttl=ttl
    )
    client.views_update(
        view_id=view["id"],
        hash=view.get("hash"),
        view=build_deploy_modal(_user(body), deps.catalog, deps.authz, prefill=prefill),
    )


def _default_version(deps: Deps, stack: str, component: str) -> str:
    try:
        return deps.catalog.get(stack).components[component].defaultVersion
    except KeyError:
        return ""


def handle_stack_options(deps: Deps, ack: Callable, body: dict[str, Any]) -> None:
    query = (body.get("value") or "").lower()
    names = [s for s in deps.catalog.list_stacks() if query in s.lower()][:STATIC_SELECT_LIMIT]
    ack(options=[{"text": {"type": "plain_text", "text": s}, "value": s} for s in names])


def handle_home_opened(deps: Deps, client: Any, event: dict[str, Any]) -> None:
    _refresh_home(deps, client, event["user"])


# --- deploy submit ------------------------------------------------------------------


def _owner_email(client: Any, user: str) -> str:
    try:
        return client.users_info(user=user)["user"]["profile"].get("email", "")
    except Exception:
        logger.warning("could not resolve email for %s", user, exc_info=True)
        return ""


def handle_submit(deps: Deps, ack: Callable, body: dict[str, Any], client: Any) -> None:
    inputs, errors = parse_submission(body["view"]["state"]["values"])
    if inputs is None:
        ack(response_action="errors", errors=errors)
        return
    ack()
    user = _user(body)

    # Post the progress message FIRST so deploy() can embed its ts in the files.
    posted = client.chat_postMessage(
        channel=deps.channel_default,
        text=f"Deployground: {inputs.stack}",
        blocks=render_progress(inputs.stack, {}, StackState.Requested),
    )
    ts = posted["ts"]
    channel = posted.get("channel") or deps.channel_default
    placeholder = StackRecord(
        name=inputs.stack,
        env=inputs.env,
        owner=user,
        expires_at=None,
        thread_ts=ts,
        channel=channel,
    )

    def fail(reason: str) -> None:
        blocks = render_progress(inputs.stack, {}, StackState.Failed)
        blocks.extend(completion_blocks(inputs.stack, False, user)[:1])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"Reason: {reason}"}})
        update_thread(client, placeholder, blocks)

    try:
        result = deps.lifecycle.deploy(
            user, _owner_email(client, user), inputs.stack, inputs.env, inputs.overrides,
            inputs.ttl, ts, channel,
        )  # fmt: skip
    except Denied as exc:
        fail(str(exc))
        client.chat_postEphemeral(channel=channel, user=user, text=f"Deploy refused: {exc}")
        return
    except Exception as exc:
        logger.exception("deploy failed for %s", inputs.stack)
        fail(f"{type(exc).__name__}: {exc}")
        return

    env_cfg = deps.environments.get(inputs.env)
    needs_approval = env_cfg is not None and deps.capability.needs_approval(
        RenderInputs(
            stack=inputs.stack, env=inputs.env, overrides=inputs.overrides, ttl=inputs.ttl,
            owner=user, owner_email="", thread_ts=ts, channel=channel,
            expires_at=result.expires_at,
        ),
        env_cfg,
    )  # fmt: skip
    states = {name: CompState.Progressing for name in result.stack_record.components}
    blocks = render_progress(inputs.stack, states, StackState.Deploying)
    pr_text = f"<{result.pr_url}|PR #{result.pr_number}>"
    if needs_approval:
        pr_text += (
            "\n:hourglass: awaiting approval (PR review): this environment does not auto-merge"
        )
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": pr_text}})
    update_thread(client, result.stack_record, blocks)
    _refresh_home(deps, client, user)


# --- lifecycle buttons --------------------------------------------------------------


def handle_extend(deps: Deps, ack: Callable, body: dict[str, Any], client: Any) -> None:
    ack()
    stack, user = _stack_from_action(body), _user(body)
    try:
        deps.lifecycle.extend(user, stack, EXTEND_BY)
    except Denied as exc:
        _notify(client, body, str(exc))
        return
    except Exception as exc:
        logger.exception("extend failed for %s", stack)
        _notify(client, body, f"Extend failed: {exc}")
        return
    deps.reaper_ack(stack)
    _notify(client, body, f"Extended {stack} by {EXTEND_BY}.")
    _refresh_home(deps, client, user)


def handle_keep(deps: Deps, ack: Callable, body: dict[str, Any], client: Any) -> None:
    ack()
    stack = _stack_from_action(body)
    deps.reaper_ack(stack)
    _notify(client, body, f"Keeping {stack}; the warning is cleared and the expiry is unchanged.")


def handle_destroy_click(deps: Deps, ack: Callable, body: dict[str, Any], client: Any) -> None:
    """Always a confirm step first; never destroys directly."""
    ack()
    stack, user = _stack_from_action(body), _user(body)
    record = deps.index.get(stack)
    if record is None:
        _notify(client, body, f"Stack {stack!r} was not found.")
        return
    count = len(record.components)
    text = (
        f":warning: Destroy *{stack}* in *{record.env}* "
        f"({count} component{'s' if count != 1 else ''})? This cannot be undone."
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Confirm destroy"},
                    "style": "danger",
                    "action_id": f"{CONFIRM_DESTROY_PREFIX}{stack}",
                    "value": stack,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "action_id": f"{CANCEL_DESTROY_PREFIX}{stack}",
                    "value": stack,
                },
            ],
        },
    ]
    channel = (body.get("channel") or {}).get("id")
    if channel:
        client.chat_postEphemeral(channel=channel, user=user, text=text, blocks=blocks)
    else:
        client.chat_postMessage(channel=user, text=text, blocks=blocks)


def handle_confirm_destroy(
    deps: Deps, ack: Callable, body: dict[str, Any], client: Any, respond: Callable
) -> None:
    ack()
    stack, user = _stack_from_action(body), _user(body)
    respond(delete_original=True)
    try:
        deps.lifecycle.destroy(user, stack)
    except Denied as exc:
        _notify(client, body, str(exc))
        return
    except Exception as exc:
        logger.exception("destroy failed for %s", stack)
        _notify(client, body, f"Destroy failed: {exc}")
        return
    _notify(client, body, f"Destroying {stack}.")
    _refresh_home(deps, client, user)


def handle_cancel_destroy(
    deps: Deps, ack: Callable, body: dict[str, Any], respond: Callable
) -> None:
    ack()
    respond(delete_original=True)
