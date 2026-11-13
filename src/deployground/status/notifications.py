"""Push status source: Argo CD Notifications webhook, served as a raw ASGI app.

Raw ASGI (no Starlette) keeps dependencies lean. Task 19 mounts/serves ``asgi_app``;
``start``/``stop`` here only manage the callback, never a server.
"""

import hmac
import json
import logging
import os
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from deployground.config.models import StatusSourceCfg
from deployground.deploytarget.notifications import DEFAULT_WEBHOOK_PATH, WEBHOOK_TOKEN_HEADER
from deployground.metadata import LABEL_COMPONENT, LABEL_STACK
from deployground.states import CompState
from deployground.status.base import StatusEvent

log = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

SYNC_FAILED_TRIGGER = "on-sync-failed"


def health_to_state(health: Any) -> CompState:
    """Argo health status -> CompState: Healthy, Degraded/Missing -> Failed, else Progressing."""
    if health == "Healthy":
        return CompState.Healthy
    if health in ("Degraded", "Missing"):
        return CompState.Failed
    return CompState.Progressing


def event_for(labels: Any, state: CompState) -> StatusEvent | None:
    """Build an event from an app's labels, or None if it isn't a deployground component."""
    if not isinstance(labels, dict):
        return None
    stack, component = labels.get(LABEL_STACK), labels.get(LABEL_COMPONENT)
    if not isinstance(stack, str) or not isinstance(component, str) or not stack or not component:
        return None
    return StatusEvent(stack_id=stack, component=component, state=state)


class NotificationsSource:
    """Receives the payload shipped by ``deploytarget.notifications`` (app, labels, health, sync,
    trigger) at POST ``cfg.webhookPath``."""

    def __init__(self, cfg: StatusSourceCfg) -> None:
        self._path = cfg.webhookPath or DEFAULT_WEBHOOK_PATH
        self._token_env = cfg.secretRef.tokenEnv if cfg.secretRef else None
        self._on_change: Callable[[StatusEvent], None] | None = None
        if self._token_env is None:
            log.warning("notifications webhook is unauthenticated (statusSource.secretRef unset)")

    def start(self, on_change: Callable[[StatusEvent], None]) -> None:
        self._on_change = on_change

    def stop(self) -> None:
        """No-op: the HTTP server lifecycle belongs to whoever serves ``asgi_app``."""

    async def asgi_app(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if scope["path"] != self._path:
            await self._respond(send, 404)
            return
        if scope["method"] != "POST":
            await self._respond(send, 405)
            return
        if not self._authorized(scope):
            await self._respond(send, 401)
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        event = self._parse(body)
        if event is not None and self._on_change is not None:
            self._on_change(event)
        # Always 204 for accepted deliveries so Argo does not retry ignorable payloads.
        await self._respond(send, 204)

    def _authorized(self, scope: Scope) -> bool:
        if self._token_env is None:
            return True
        expected = os.environ.get(self._token_env, "")
        if not expected:
            return False  # fail closed when the configured secret is not set
        wanted = WEBHOOK_TOKEN_HEADER.lower().encode()
        for name, value in scope.get("headers", []):
            if name.lower() == wanted:
                return hmac.compare_digest(value, expected.encode())
        return False

    @staticmethod
    def _parse(body: bytes) -> StatusEvent | None:
        try:
            payload = json.loads(body)
        except ValueError:
            log.warning("ignoring non-JSON notifications payload")
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("trigger") == SYNC_FAILED_TRIGGER:
            state = CompState.Failed
        else:
            state = health_to_state(payload.get("health"))
        return event_for(payload.get("labels"), state)

    @staticmethod
    async def _respond(send: Send, status: int) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})
