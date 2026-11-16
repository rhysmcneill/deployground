"""Slack implementation of the reaper's notifier: DM the owner and post in the stack thread."""

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
        self._dm(record.owner, text=text, blocks=blocks)

    def alert(self, record: StackRecord, text: str) -> None:
        self._client.chat_postMessage(channel=record.channel, thread_ts=record.thread_ts, text=text)
        self._dm(record.owner, text=text)

    def _dm(self, user: str, **message: Any) -> None:
        # A failed DM must not hide the (already sent) thread post from the reaper.
        try:
            channel = self._client.conversations_open(users=user)["channel"]["id"]
            self._client.chat_postMessage(channel=channel, **message)
        except Exception:
            log.exception("could not DM %s", user)
