"""Slack deploy form engine: pure Block Kit builders and a view-state parser.

No slack_sdk/bolt imports; views and blocks are plain dicts (wiring lives in Task 17).

Conventions Task 17 depends on:
- Every input block holds exactly one element whose action_id is ``ACTION_ID`` ("val"),
  so state is read as ``view["state"]["values"][block_id]["val"]``.
- Block ids: "catalog" (which catalog entry to deploy), "stack" (the free-typed name for
  this live deployment), "env", "ttl", "override_components" (multi-select of components),
  and "override::<component>" (one text input per overridden component).
- The "catalog" and "override_components" blocks have ``dispatch_action`` set, so picking a
  catalog entry or picking components emits block_actions; the handler then calls
  views.update with ``build_override_blocks`` output (preserve existing override values when
  re-rendering). The "stack" block is a plain text input with no dispatch_action.
- With no allowed environments the modal is a notice only (no input blocks, no submit).
- Modal callback_id is "deploy_submit".
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from deployground.authz.base import AuthzProvider
from deployground.catalog import Catalog
from deployground.config.models import CatalogEntry, parse_duration

View = dict[str, Any]
Block = dict[str, Any]

ACTION_ID = "val"
CALLBACK_ID = "deploy_submit"
STATIC_SELECT_LIMIT = 100
DEFAULT_TTL = "24h"
OVERRIDE_PREFIX = "override::"
OVERRIDE_PICKER_BLOCK = "override_components"
STACK_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class DeployInputs:
    catalog: str
    stack: str
    env: str
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)
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
    entry: CatalogEntry,
    selected_components: list[str],
    values: Mapping[str, dict[str, str]] | None = None,
) -> list[Block]:
    """One version input per selected component, prefilled to `values` or the default."""
    blocks: list[Block] = []
    for name in selected_components:
        comp = entry.components.get(name)
        if comp is None:
            continue
        initial = (values or {}).get(name, {}).get("version", comp.defaultVersion)
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
    entries = catalog.list_entries()
    envs = sorted(authz.allowed_environments(user))
    if not envs:
        # Notice-only view: no input blocks, so Slack requires no submit button.
        return {
            "type": "modal",
            "callback_id": CALLBACK_ID,
            "title": _text("Deploy"),
            "close": _text("Close"),
            "blocks": [
                {
                    "type": "section",
                    "block_id": "no_envs",
                    "text": {
                        "type": "mrkdwn",
                        "text": "You are not allowed to deploy to any environment.",
                    },
                }
            ],
        }

    if len(entries) <= STATIC_SELECT_LIMIT:
        catalog_element: dict[str, Any] = {
            "type": "static_select",
            "placeholder": _text("Select a catalog entry"),
            "options": [_option(s) for s in entries],
        }
    else:
        catalog_element = {
            "type": "external_select",
            "placeholder": _text("Search for a catalog entry"),
            "min_query_length": 0,
        }
    known_catalog = prefill is not None and prefill.catalog in entries
    if known_catalog:
        assert prefill is not None
        catalog_element["initial_option"] = _option(prefill.catalog)

    blocks: list[Block] = [
        {"type": "header", "text": _text("🚀  New deployment")},
        _input(
            "catalog",
            "Catalog",
            catalog_element,
            dispatch_action=True,
            hint=_text("What to deploy"),
        ),
    ]

    if known_catalog:
        assert prefill is not None
        catalog_entry = catalog.get(prefill.catalog)
        components = sorted(catalog_entry.components)
        picker: dict[str, Any] = {
            "type": "multi_static_select",
            "placeholder": _text("Override component versions"),
            "options": [_option(c) for c in components],
        }
        chosen = [c for c in sorted(prefill.overrides) if c in catalog_entry.components]
        if chosen:
            picker["initial_options"] = [_option(c) for c in chosen]
        blocks.append(
            _input(
                OVERRIDE_PICKER_BLOCK,
                "Override components",
                picker,
                optional=True,
                dispatch_action=True,
            )
        )
        blocks.extend(build_override_blocks(catalog_entry, chosen, prefill.overrides))

    blocks.append(
        _input(
            "stack",
            "Stack name",
            {
                "type": "plain_text_input",
                "initial_value": prefill.stack if prefill else "",
            },
            hint=_text("Used for the namespace and URL — lowercase letters, numbers, hyphens"),
        )
    )
    blocks.append({"type": "divider"})

    env_element: dict[str, Any] = {
        "type": "static_select",
        "placeholder": _text("Select an environment"),
        "options": [_option(e) for e in envs],
    }
    if prefill is not None and prefill.env in envs:
        env_element["initial_option"] = _option(prefill.env)
    blocks.append(_input("env", "Environment", env_element))

    ttl = prefill.ttl if prefill is not None else DEFAULT_TTL
    blocks.append(
        _input(
            "ttl",
            "Expires in",
            {"type": "plain_text_input", "initial_value": ttl},
            hint=_text('e.g. 15m, 24h, 7d, or "none" for no expiry'),
        )
    )

    view: View = {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "title": _text("Deploy"),
        "close": _text("Cancel"),
        "submit": _text("Deploy"),
        "blocks": blocks,
    }
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

    catalog = _selected(view_state, "catalog")
    if not catalog:
        errors["catalog"] = "Select a catalog entry."

    name = _text_value(view_state, "stack")
    if not STACK_NAME_PATTERN.fullmatch(name):
        errors["stack"] = (
            "Use lowercase letters, numbers and hyphens only "
            "(1-63 characters, no leading/trailing hyphen)."
        )

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

    overrides: dict[str, dict[str, str]] = {}
    for block_id in view_state:
        if not block_id.startswith(OVERRIDE_PREFIX):
            continue
        component_name = block_id[len(OVERRIDE_PREFIX) :]
        value = _text_value(view_state, block_id)
        if not value:
            errors[block_id] = "Version cannot be empty."
        else:
            overrides.setdefault(component_name, {})["version"] = value

    if errors or catalog is None or env is None:
        return None, errors
    return DeployInputs(catalog=catalog, stack=name, env=env, overrides=overrides, ttl=ttl), {}
