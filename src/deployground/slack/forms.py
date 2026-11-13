"""Slack deploy form engine: pure Block Kit builders and a view-state parser.

No slack_sdk/bolt imports; views and blocks are plain dicts (wiring lives in Task 17).

Conventions Task 17 depends on:
- Every input block holds exactly one element whose action_id is ``ACTION_ID`` ("val"),
  so state is read as ``view["state"]["values"][block_id]["val"]``.
- Block ids: "stack", "env", "ttl", "override_components" (multi-select of components),
  and "override::<component>" (one text input per overridden component).
- The "stack" block has ``dispatch_action`` set, so choosing a stack emits a block_actions
  event; the handler then calls views.update with ``build_override_blocks`` output.
- Modal callback_id is "deploy_submit".
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from deployground.authz.base import AuthzProvider
from deployground.catalog import Catalog
from deployground.config.models import StackDef, parse_duration

View = dict[str, Any]
Block = dict[str, Any]

ACTION_ID = "val"
CALLBACK_ID = "deploy_submit"
STATIC_SELECT_LIMIT = 100
DEFAULT_TTL = "24h"
OVERRIDE_PREFIX = "override::"
OVERRIDE_PICKER_BLOCK = "override_components"


@dataclass(frozen=True)
class DeployInputs:
    stack: str
    env: str
    overrides: dict[str, str] = field(default_factory=dict)
    ttl: str = DEFAULT_TTL


def _text(text: str) -> dict[str, str]:
    return {"type": "plain_text", "text": text}


def _option(value: str) -> dict[str, Any]:
    return {"text": _text(value), "value": value}


def _input(block_id: str, label: str, element: dict[str, Any], **extra: Any) -> Block:
    element["action_id"] = ACTION_ID
    return {
        "type": "input",
        "block_id": block_id,
        "label": _text(label),
        "element": element,
        **extra,
    }


def build_override_blocks(
    stack: StackDef,
    selected_components: list[str],
    values: Mapping[str, str] | None = None,
) -> list[Block]:
    """One version input per selected component, prefilled to `values` or the default."""
    blocks: list[Block] = []
    for name in selected_components:
        comp = stack.components.get(name)
        if comp is None:
            continue
        initial = (values or {}).get(name, comp.defaultVersion)
        blocks.append(
            _input(
                f"{OVERRIDE_PREFIX}{name}",
                f"{name} version",
                {"type": "plain_text_input", "initial_value": initial},
            )
        )
    return blocks


def build_deploy_modal(
    user: str,
    catalog: Catalog,
    authz: AuthzProvider,
    prefill: DeployInputs | None = None,
) -> View:
    stacks = catalog.list_stacks()
    envs = sorted(authz.allowed_environments(user))

    if len(stacks) <= STATIC_SELECT_LIMIT:
        stack_element: dict[str, Any] = {
            "type": "static_select",
            "placeholder": _text("Select a stack"),
            "options": [_option(s) for s in stacks],
        }
    else:
        stack_element = {
            "type": "external_select",
            "placeholder": _text("Search for a stack"),
            "min_query_length": 0,
        }
    known_stack = prefill is not None and prefill.stack in stacks
    if known_stack:
        assert prefill is not None
        stack_element["initial_option"] = _option(prefill.stack)

    blocks: list[Block] = [_input("stack", "Stack", stack_element, dispatch_action=True)]

    if envs:
        env_element: dict[str, Any] = {
            "type": "static_select",
            "placeholder": _text("Select an environment"),
            "options": [_option(e) for e in envs],
        }
        if prefill is not None and prefill.env in envs:
            env_element["initial_option"] = _option(prefill.env)
        blocks.append(_input("env", "Environment", env_element))
    else:
        blocks.append(
            {
                "type": "section",
                "block_id": "no_envs",
                "text": {
                    "type": "mrkdwn",
                    "text": "You are not allowed to deploy to any environment.",
                },
            }
        )

    ttl = prefill.ttl if prefill is not None else DEFAULT_TTL
    blocks.append(
        _input(
            "ttl",
            "Expires in",
            {"type": "plain_text_input", "initial_value": ttl},
            hint=_text('e.g. 15m, 24h, 7d, or "none" for no expiry'),
        )
    )

    if known_stack:
        assert prefill is not None
        stack_def = catalog.get(prefill.stack)
        components = sorted(stack_def.components)
        picker: dict[str, Any] = {
            "type": "multi_static_select",
            "placeholder": _text("Override component versions"),
            "options": [_option(c) for c in components],
        }
        chosen = [c for c in sorted(prefill.overrides) if c in stack_def.components]
        if chosen:
            picker["initial_options"] = [_option(c) for c in chosen]
        blocks.append(_input(OVERRIDE_PICKER_BLOCK, "Override components", picker, optional=True))
        blocks.extend(build_override_blocks(stack_def, chosen, prefill.overrides))

    view: View = {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "title": _text("Deploy"),
        "close": _text("Cancel"),
        "blocks": blocks,
    }
    if envs:
        view["submit"] = _text("Deploy")
    return view


def _selected(view_state: Mapping[str, Any], block_id: str) -> str | None:
    action = view_state.get(block_id, {}).get(ACTION_ID) or {}
    option = action.get("selected_option") or {}
    return option.get("value") or None


def _text_value(view_state: Mapping[str, Any], block_id: str) -> str:
    action = view_state.get(block_id, {}).get(ACTION_ID) or {}
    return (action.get("value") or "").strip()


def parse_submission(
    view_state: Mapping[str, Any],
) -> tuple[DeployInputs | None, dict[str, str]]:
    """Parse `view["state"]["values"]`; returns (inputs, {}) or (None, {block_id: message})."""
    errors: dict[str, str] = {}

    stack = _selected(view_state, "stack")
    if not stack:
        errors["stack"] = "Select a stack."
    env = _selected(view_state, "env")
    if not env:
        errors["env"] = "Select an environment."

    ttl = _text_value(view_state, "ttl")
    if not ttl:
        errors["ttl"] = 'Enter a duration such as "24h", or "none".'
    else:
        try:
            parse_duration(ttl)
        except ValueError:
            errors["ttl"] = 'Invalid duration. Use e.g. "15m", "24h" or "none".'

    overrides: dict[str, str] = {}
    for block_id in view_state:
        if not block_id.startswith(OVERRIDE_PREFIX):
            continue
        name = block_id[len(OVERRIDE_PREFIX) :]
        value = _text_value(view_state, block_id)
        if not value:
            errors[block_id] = "Version cannot be empty."
        else:
            overrides[name] = value

    if errors or stack is None or env is None:
        return None, errors
    return DeployInputs(stack=stack, env=env, overrides=overrides, ttl=ttl), {}
