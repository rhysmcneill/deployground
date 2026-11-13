"""Poll status source: periodically lists managed Argo CD Applications and diffs their health."""

import logging
import os
import threading
from collections.abc import Callable
from typing import Any

import httpx

from deployground.config.models import StatusSourceCfg, parse_duration
from deployground.states import CompState
from deployground.status.base import StatusEvent
from deployground.status.notifications import event_for, health_to_state

log = logging.getLogger(__name__)

HttpGet = Callable[[str, dict[str, str]], Any]
_DEFAULT_INTERVAL_SECONDS = 15.0


def _httpx_get(url: str, headers: dict[str, str]) -> Any:
    response = httpx.get(url, headers=headers, timeout=10.0)
    response.raise_for_status()
    return response.json()


class ArgoApiSource:
    """``http_get(url, headers) -> parsed JSON`` is the injectable seam (default: httpx).

    The thread loop calls ``_poll_once(on_change)``; tests call it directly as a manual tick.
    """

    def __init__(self, cfg: StatusSourceCfg, http_get: HttpGet | None = None) -> None:
        if not cfg.url or cfg.tokenRef is None:
            raise ValueError("argocd-api status source requires url and tokenRef")
        self._url = (
            f"{cfg.url.rstrip('/')}/api/v1/applications?selector=deployground.io/managed=true"
        )
        self._token_env = cfg.tokenRef.tokenEnv
        interval = parse_duration(cfg.pollInterval) if cfg.pollInterval else None
        self._interval = interval.total_seconds() if interval else _DEFAULT_INTERVAL_SECONDS
        self._http_get = http_get or _httpx_get
        self._last: dict[tuple[str, str], CompState] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _poll_once(self, on_change: Callable[[StatusEvent], None]) -> None:
        token = os.environ.get(self._token_env, "")
        data = self._http_get(self._url, {"Authorization": f"Bearer {token}"})
        for item in (data.get("items") or []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            labels = (item.get("metadata") or {}).get("labels")
            health = ((item.get("status") or {}).get("health") or {}).get("status")
            event = event_for(labels, health_to_state(health))
            if event is None:
                continue
            key = (event.stack_id, event.component)
            if self._last.get(key) != event.state:
                self._last[key] = event.state
                on_change(event)

    def _run(self, on_change: Callable[[StatusEvent], None]) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once(on_change)
            except Exception as exc:  # keep polling through transient API errors
                # Log the exception type only: never risk echoing request headers/token.
                log.warning("argo poll failed: %s", type(exc).__name__)
            self._stop.wait(self._interval)

    def start(self, on_change: Callable[[StatusEvent], None]) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_change,), name="argo-status-poll", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
