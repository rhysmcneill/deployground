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
    _default_override_value,
    _overridable_fields,
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
# Unexpected errors can carry repo URLs/paths; Slack gets this, the logs get the detail.
GENERIC_ERROR = "an unexpected error occurred; check the bot logs"


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
            catalog=data["catalog"],
            stack=data["stack"],
            env=data["env"],
            overrides={k: dict(v) for k, v in (data.get("overrides") or {}).items()},
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
    catalog = selected("catalog")
    overrides: dict[str, dict[str, str]] = {}
    for name in chosen:
        defaults = _default_overrides(deps, catalog, name)
        comp_overrides: dict[str, str] = {}
        for path, default in defaults.items():
            block_id = f"{OVERRIDE_PREFIX}{name}__{path}"
            text = (values.get(block_id, {}).get(ACTION_ID) or {}).get("value")
            comp_overrides[path] = text if text else default
        overrides[name] = comp_overrides
    ttl = ((values.get("ttl", {}).get(ACTION_ID) or {}).get("value") or "24h").strip() or "24h"
    stack = (values.get("stack", {}).get(ACTION_ID) or {}).get("value") or ""
    prefill = DeployInputs(
        catalog=catalog, stack=stack, env=selected("env"), overrides=overrides, ttl=ttl
    )
    client.views_update(
        view_id=view["id"],
        hash=view.get("hash"),
        view=build_deploy_modal(_user(body), deps.catalog, deps.authz, prefill=prefill),
    )


def _default_overrides(deps: Deps, catalog: str, component: str) -> dict[str, str]:
    try:
        comp = deps.catalog.get(catalog).components[component]
    except KeyError:
        return {}
    return {
        field.path: _default_override_value(comp, field.path)
        for field in _overridable_fields(comp)
    }


def handle_catalog_options(deps: Deps, ack: Callable, body: dict[str, Any]) -> None:
    query = (body.get("value") or "").lower()
    names = [s for s in deps.catalog.list_entries() if query in s.lower()][:STATIC_SELECT_LIMIT]
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
    if inputs is not None and deps.index.get(inputs.stack) is not None:
        errors = {**errors, "stack": f"{inputs.stack!r} is already in use by a live stack."}
        inputs = None
    if inputs is None:
        ack(response_action="errors", errors=errors)
        return
    ack()
    user = _user(body)

    # Post the progress message FIRST so deploy() can embed its ts in the files. DMs the
    # deploying user directly (channel=user opens/uses their DM) rather than a shared
    # channel, so every deploy's whole lifecycle lives in that user's own Messages Tab.
    posted = client.chat_postMessage(
        channel=user,
        text=f"Deployground: {inputs.stack}",
        blocks=render_progress(inputs.stack, {}, StackState.Requested),
    )
    ts = posted["ts"]
    channel = posted.get("channel") or user
    placeholder = StackRecord(
        name=inputs.stack,
        env=inputs.env,
        owner=user,
        expires_at=None,
        thread_ts=ts,
        channel=channel,
        catalog=inputs.catalog,
    )

    def fail(reason: str) -> None:
        blocks = render_progress(inputs.stack, {}, StackState.Failed)
        blocks.extend(completion_blocks(inputs.stack, False, user)[:1])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"Reason: {reason}"}})
        update_thread(client, placeholder, blocks)

    try:
        result = deps.lifecycle.deploy(
            user=user,
            owner_email=_owner_email(client, user),
            catalog=inputs.catalog,
            stack=inputs.stack,
            env=inputs.env,
            overrides=inputs.overrides,
            ttl=inputs.ttl,
            thread_ts=ts,
            channel=channel,
        )
    except Denied as exc:
        fail(str(exc))
        client.chat_postEphemeral(channel=channel, user=user, text=f"Deploy refused: {exc}")
        return
    except Exception:
        logger.exception("deploy failed for %s", inputs.stack)
        fail(GENERIC_ERROR)
        return

    env_cfg = deps.environments.get(inputs.env)
    needs_approval = env_cfg is not None and deps.capability.needs_approval(
        RenderInputs(
            catalog=inputs.catalog, stack=inputs.stack, env=inputs.env,
            overrides=inputs.overrides, ttl=inputs.ttl,
            owner=user, owner_email="", thread_ts=ts, channel=channel,
            expires_at=result.expires_at,
        ),  # fmt: skip
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
        result = deps.lifecycle.extend(user, stack, EXTEND_BY)
    except Denied as exc:
        _notify(client, body, str(exc))
        return
    except Exception:
        logger.exception("extend failed for %s", stack)
        _notify(client, body, f"Extend failed: {GENERIC_ERROR}")
        return
    deps.reaper_ack(stack)
    pr_text = f"<{result.pr_url}|PR #{result.pr_number}>"
    _notify(client, body, f"Extended {stack} by {EXTEND_BY}. {pr_text}")
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
    # Authorize BEFORE destroy: the lifecycle wrapper repaints the thread to Destroying before
    # its own can_manage gate, so a non-owner must be stopped here to avoid that flicker.
    record = deps.index.get(stack)
    if record is not None and not deps.authz.can_manage(user, record.owner):
        _notify(client, body, f"You are not allowed to manage stack {stack!r}.")
        return
    try:
        result = deps.lifecycle.destroy(user, stack)
    except Denied as exc:
        _notify(client, body, str(exc))
        return
    except Exception:
        logger.exception("destroy failed for %s", stack)
        _notify(client, body, f"Destroy failed: {GENERIC_ERROR}")
        return
    pr_text = f"<{result.pr_url}|PR #{result.pr_number}>"
    _notify(client, body, f"Destroying {stack}. {pr_text}")
    _refresh_home(deps, client, user)


def handle_cancel_destroy(
    deps: Deps, ack: Callable, body: dict[str, Any], respond: Callable
) -> None:
    ack()
    respond(delete_original=True)
