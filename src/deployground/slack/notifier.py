"""Slack implementation of the reaper's notifier: posts into the stack's own thread.

That thread lives in the owner's DM (Messages Tab) -- see ``handle_submit`` -- so posting
there already reaches the owner directly; no separate DM is needed on top.
"""

import logging
from typing import Any

from deployground.metadata import StackRecord

log = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, client: Any) -> None:
        self._client = client

    def warn(self, record: StackRecord, blocks: list) -> None:
        text = f"Stack {record.name} is about to expire"
        self._client.chat_postMessage(
            channel=record.channel, thread_ts=record.thread_ts, blocks=blocks, text=text
        )

    def alert(self, record: StackRecord, text: str) -> None:
        self._client.chat_postMessage(channel=record.channel, thread_ts=record.thread_ts, text=text)
