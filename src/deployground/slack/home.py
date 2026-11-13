"""App Home view builder (pure dicts; publishing via views.publish is Task 17).

action_id conventions Task 17 parses:
- ``open_deploy_form``: the "New deployment" button (shown only if the user has allowed envs).
- ``extend::<stack>``: per-row Extend button; split on the first "::" for the stack name.
- ``destroy::<stack>``: per-row Destroy button; same parsing.
"""

from datetime import UTC, datetime
from typing import Any

from deployground.authz.base import AuthzProvider
from deployground.index import StackIndex
from deployground.slack.progress import OVERALL_EMOJI
from deployground.states import StackState

View = dict[str, Any]
Block = dict[str, Any]

OPEN_DEPLOY_FORM = "open_deploy_form"
EXTEND_PREFIX = "extend::"
DESTROY_PREFIX = "destroy::"
STATUS_UNKNOWN = "unknown"


def _text(text: str, kind: str = "plain_text") -> dict[str, Any]:
    return {"type": kind, "text": text}


def _status_label(stack: str, statuses: dict[str, StackState] | None) -> str:
    state = (statuses or {}).get(stack)
    if state is None:
        return STATUS_UNKNOWN
    return f"{OVERALL_EMOJI[state]} {state.value.capitalize()}"


def humanize_expiry(expires_at: datetime | None, now: datetime) -> str:
    """'never' for None, 'expired' when past, else '2h 30m' / '45m'."""
    if expires_at is None:
        return "never"
    seconds = (expires_at - now).total_seconds()
    if seconds <= 0:
        return "expired"
    minutes = int(seconds // 60)
    hours, mins = divmod(minutes, 60)
    if hours == 0:
        return f"{mins}m"
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _button(label: str, action_id: str, value: str | None = None, **extra: Any) -> dict[str, Any]:
    button: dict[str, Any] = {"type": "button", "text": _text(label), "action_id": action_id}
    if value is not None:
        button["value"] = value
    return {**button, **extra}


def build_home(
    user: str,
    index: StackIndex,
    authz: AuthzProvider,
    *,
    statuses: dict[str, StackState] | None = None,
    now: datetime | None = None,
) -> View:
    now = now or datetime.now(UTC)
    blocks: list[Block] = []
    if authz.allowed_environments(user):
        blocks.append(
            {
                "type": "actions",
                "elements": [_button("New deployment", OPEN_DEPLOY_FORM, style="primary")],
            }
        )
    blocks.append({"type": "header", "text": _text("My stacks")})
    stacks = index.list_by_owner(user)
    if not stacks:
        blocks.append({"type": "section", "text": _text("You have no running stacks.", "mrkdwn")})
    for rec in sorted(stacks, key=lambda s: s.name):
        expiry = humanize_expiry(rec.expires_at, now)
        blocks.append(
            {
                "type": "section",
                "text": _text(
                    f"*{rec.name}* ({rec.env})\n"
                    f"Status: {_status_label(rec.name, statuses)} | Expires in: {expiry}",
                    "mrkdwn",
                ),
            }
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    _button("Extend", f"{EXTEND_PREFIX}{rec.name}", rec.name),
                    _button("Destroy", f"{DESTROY_PREFIX}{rec.name}", rec.name, style="danger"),
                ],
            }
        )
    return {"type": "home", "blocks": blocks}
