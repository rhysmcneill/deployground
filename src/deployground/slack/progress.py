"""Live progress rendering for the deploy/teardown thread message (pure Block Kit dicts).

`update_thread` is the only function touching Slack; it takes any object with
``chat_update`` and is idempotent: re-applying identical blocks to the same
(channel, ts) is a no-op, so duplicate Argo events don't spam the API.
"""

import json
import threading
from collections import OrderedDict
from typing import Any, Protocol

from deployground.metadata import StackRecord
from deployground.states import CompState, StackState

Block = dict[str, Any]

MAX_COMPONENT_ROWS = 10
CACHE_SIZE = 1024

COMP_EMOJI: dict[CompState, str] = {
    CompState.Progressing: "⏳",
    CompState.Healthy: "✅",
    CompState.Failed: "❌",
}
OVERALL_EMOJI: dict[StackState, str] = {
    StackState.Requested: "📝",
    StackState.Deploying: "🚀",
    StackState.Running: "🟢",
    StackState.Expiring: "⏰",
    StackState.Destroying: "🧹",
    StackState.Destroyed: "🗑️",
    StackState.Failed: "🔴",
}


class ChatUpdater(Protocol):
    def chat_update(self, **kwargs: Any) -> Any: ...


def _section(text: str) -> Block:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _summary_line(states: dict[str, CompState]) -> str:
    total = len(states)
    counts = {s: sum(1 for v in states.values() if v == s) for s in CompState}
    parts = [f"{counts[CompState.Healthy]}/{total} Healthy"]
    if counts[CompState.Failed]:
        parts.append(f"{counts[CompState.Failed]} Failed")
    if counts[CompState.Progressing]:
        parts.append(f"{counts[CompState.Progressing]} Progressing")
    return ", ".join(parts)


def render_progress(
    stack: str, component_states: dict[str, CompState], overall: StackState
) -> list[Block]:
    """Overall line plus per-component rows (<= MAX_COMPONENT_ROWS) or a counts summary."""
    blocks = [_section(f"{OVERALL_EMOJI[overall]} *{stack}*: {overall.value.capitalize()}")]
    if len(component_states) <= MAX_COMPONENT_ROWS:
        if component_states:
            rows = "\n".join(
                f"{COMP_EMOJI[state]} {name}: {state.value}"
                for name, state in sorted(component_states.items())
            )
            blocks.append(_section(rows))
    else:
        blocks.append(_section(_summary_line(component_states)))
    return blocks


def completion_blocks(
    stack: str,
    ok: bool,
    owner: str,
    links: list[tuple[str, str]] | None = None,
    failed_component: str | None = None,
) -> list[Block]:
    """Final message: success pings the owner; failure names the failing component."""
    link_text = " | ".join(f"<{url}|{name}>" for name, url in (links or []))
    if ok:
        text = f"✅ Deployment complete: *{stack}* <@{owner}>"
    else:
        what = f"{failed_component} failed" if failed_component else f"{stack} failed"
        text = f"❌ *{stack}*: {what}"
    blocks = [_section(text)]
    if link_text:
        blocks.append(_section(link_text))
    return blocks


_last_sent: OrderedDict[tuple[str, str], str] = OrderedDict()
_lock = threading.Lock()


def update_thread(client: ChatUpdater, record: StackRecord, blocks: list[Block]) -> None:
    """Edit the stack's thread message in place; skip the call if blocks are unchanged."""
    key = (record.channel, record.thread_ts)
    digest = json.dumps(blocks, sort_keys=True)
    with _lock:
        if _last_sent.get(key) == digest:
            return
    client.chat_update(
        channel=record.channel,
        ts=record.thread_ts,
        blocks=blocks,
        text=f"Deployground: {record.name}",
    )
    with _lock:
        _last_sent[key] = digest
        _last_sent.move_to_end(key)
        while len(_last_sent) > CACHE_SIZE:
            _last_sent.popitem(last=False)
