"""Composition root: builds every service from a Config and runs the process.

Environment variables read here (NOT ``DEPLOYGROUND_*``: the config loader treats every
variable with that prefix as a config override and rejects unknown ones):

- ``SLACK_BOT_TOKEN`` / ``SLACK_APP_TOKEN``: bot token and Socket Mode app token.
- ``DG_DEFAULT_CHANNEL``: channel where deploy progress threads are posted by default.
- ``DG_WEBHOOK_PORT``: port of the Argo CD Notifications webhook server (default 8080;
  only used when ``statusSource.type`` is ``argocd-notifications``). The path comes from
  ``statusSource.webhookPath``.
- ``DG_WEBHOOK_HOST``: bind host for that server (default ``0.0.0.0``).

``build_app`` takes injectable seams (``slack_client``, ``git_client``, ``status_source``) so
tests run without network. Only the catalog follows config hot-reload (via
``ConfigWatcher.current``); everything else is built once from the config at startup.
"""

import logging
import signal
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import uvicorn

from deployground.authz.allowlist import AllowlistAuthz
from deployground.capability.base import RenderContext
from deployground.capability.image_deploy import ImageDeployCapability
from deployground.catalog import Catalog
from deployground.config.loader import ConfigWatcher
from deployground.config.models import Config, parse_duration
from deployground.deploytarget import make_deploy_target
from deployground.git.github import GitHubClient
from deployground.index import StackIndex
from deployground.lifecycle.reaper import Reaper
from deployground.lifecycle.service import LifecycleService
from deployground.slack.actions import Deps
from deployground.slack.membership import SlackMembershipResolver
from deployground.slack.notifier import SlackNotifier
from deployground.slack.progress import render_progress, update_thread
from deployground.states import StackState
from deployground.status import make_status_source
from deployground.status.base import StatusSource
from deployground.status.bridge import StatusProgressBridge

log = logging.getLogger(__name__)

DEFAULT_WEBHOOK_PORT = 8080


def asgi3_config(asgi_app: Any, host: str, port: int) -> Any:
    """uvicorn config for a bound-method ASGI app.

    uvicorn auto-detects a bound method as ASGI2 (called as ``app(scope)``), which would
    500 every request, so the interface is pinned to asgi3.
    """
    return uvicorn.Config(
        asgi_app, host=host, port=port, interface="asgi3", lifespan="off", log_level="info"
    )


class _ForgetOnDestroy:
    """Wraps LifecycleService so every destroy (Slack button or reaper) also moves the stack's
    thread Destroying -> Destroyed and, on success, drops the stack's bridge state; everything
    else is delegated untouched. A failed destroy puts the thread back to its last status."""

    def __init__(self, lifecycle: Any, index: Any, client: Any, bridge: Any) -> None:
        self._lifecycle = lifecycle
        self._index = index
        self._client = client
        self._bridge = bridge

    def _show(self, record: Any, state: StackState) -> None:
        try:
            update_thread(self._client, record, render_progress(record.name, {}, state))
        except Exception:
            log.warning("could not update thread for %s", record.name, exc_info=True)

    def destroy(self, user: str, stack: str) -> None:
        record = self._index.get(stack)
        previous = self._bridge.statuses().get(stack)  # None: leave the thread alone on failure
        if record is not None:
            self._show(record, StackState.Destroying)
        try:
            self._lifecycle.destroy(user, stack)
        except Exception:
            if record is not None and previous is not None:
                self._show(record, previous)
            raise
        self._bridge.forget(stack)
        if record is not None:
            self._show(record, StackState.Destroyed)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lifecycle, name)


class StartupError(RuntimeError):
    """A fatal, user-presentable problem while starting (bad environment, Git unreachable)."""


@dataclass
class Application:
    config: Config
    index: StackIndex
    bridge: StatusProgressBridge
    reaper: Reaper
    status_source: StatusSource
    slack_client: Any
    deps: Deps
    slack_app: Any | None = None  # bolt App; None when a fake slack_client was injected
    watcher: ConfigWatcher | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _servers: list[Any] = field(default_factory=list, repr=False)

    def start(self) -> None:
        """Everything except the blocking Socket Mode connection."""
        try:
            self.index.rebuild()
        except Exception as exc:
            raise StartupError(f"cannot rebuild the stack index from Git: {exc}") from exc
        log.info("index rebuilt: %d live stacks", len(self.index.list_live()))

        # The reaper guards core state (expiry); status is best-effort, so it starts first
        # and a dead status source (port in use, Argo unreachable) only degrades progress.
        threading.Thread(
            target=self.reaper.run, args=(self._stop,), name="reaper", daemon=True
        ).start()
        try:
            self.status_source.start(self.bridge.handle)
            asgi_app = getattr(self.status_source, "asgi_app", None)
            if asgi_app is not None:
                self._serve_webhook(asgi_app)
        except Exception:
            log.exception("status source failed to start; progress updates are disabled")

    def run(self) -> None:
        """Start all services, then block on Socket Mode until interrupted."""
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        if self.slack_app is None:
            raise StartupError("no Slack app configured")
        app_token = self.env.get("SLACK_APP_TOKEN")
        if not app_token:
            raise StartupError("environment variable SLACK_APP_TOKEN is not set")
        self.start()
        if self.watcher is not None and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGHUP, lambda *_: self.watcher.reload())  # type: ignore[union-attr]
        try:
            SocketModeHandler(self.slack_app, app_token).start()
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        self.status_source.stop()
        for server in self._servers:
            server.should_exit = True

    def _serve_webhook(self, asgi_app: Any) -> None:
        port = int(self.env.get("DG_WEBHOOK_PORT", DEFAULT_WEBHOOK_PORT))
        host = self.env.get("DG_WEBHOOK_HOST", "0.0.0.0")  # noqa: S104 - must accept Argo's POST
        server = uvicorn.Server(asgi3_config(asgi_app, host, port))
        self._servers.append(server)
        threading.Thread(target=server.run, name="webhook", daemon=True).start()
        log.info("notifications webhook listening on %s:%s", host, port)


def build_app(
    config: Config | ConfigWatcher,
    env: Mapping[str, str],
    *,
    slack_client: Any | None = None,
    git_client: Any | None = None,
    status_source: StatusSource | None = None,
    app_factory: Any | None = None,
) -> Application:
    watcher = config if isinstance(config, ConfigWatcher) else None
    cfg = watcher.current() if watcher else config
    assert isinstance(cfg, Config)
    config_provider = watcher.current if watcher else (lambda: cfg)

    slack_app = None
    if slack_client is None:
        from slack_bolt import App

        token = env.get("SLACK_BOT_TOKEN")
        if not token:
            raise StartupError("environment variable SLACK_BOT_TOKEN is not set")
        try:
            slack_app = (app_factory or App)(token=token)
        except Exception as exc:  # App() calls auth.test over the network
            raise StartupError(f"Slack auth failed: {exc}") from exc
        slack_client = slack_app.client

    git = git_client or GitHubClient(env)
    repo_ref = cfg.git.repos[cfg.deployTarget.repo]
    index = StackIndex(git, cfg.deployTarget, repo_ref)
    env_clusters = {name: e.cluster for name, e in cfg.environments.items()}
    deploy_target = make_deploy_target(cfg.deployTarget, repo_ref, env_clusters)

    catalog = Catalog(config_provider)
    authz = AllowlistAuthz(
        cfg.access.rules, SlackMembershipResolver(slack_client), cfg.environments
    )
    capability = ImageDeployCapability()
    capability_cfg = cfg.capabilities.get(capability.id)
    if capability_cfg is None or not capability_cfg.enabled:
        raise StartupError("config has no enabled image-deploy capability")
    render_ctx = RenderContext(
        catalog=catalog,
        deploy_target=deploy_target,
        capability_cfg=capability_cfg,
        environments=cfg.environments,
    )
    lifecycle = LifecycleService(
        authz,
        capability,
        git,
        index,
        cfg.environments,
        cfg.git.repos,
        cfg.lifecycle,
        render_ctx,
    )

    bridge = StatusProgressBridge(index, slack_client)
    lifecycle = _ForgetOnDestroy(lifecycle, index, slack_client, bridge)
    source = status_source or make_status_source(cfg.statusSource)

    warn_before = parse_duration(cfg.lifecycle.warnBefore)
    interval = parse_duration(cfg.lifecycle.reaperInterval)
    if warn_before is None or interval is None:
        raise StartupError("lifecycle.warnBefore and lifecycle.reaperInterval cannot be 'none'")
    reaper = Reaper(
        index,
        lifecycle,
        lambda: datetime.now(UTC),
        SlackNotifier(slack_client),
        warn_before,
        interval,
    )

    channel_default = env.get("DG_DEFAULT_CHANNEL", "")
    if not channel_default:
        log.warning("DG_DEFAULT_CHANNEL is not set; deploy threads have no default channel")
    deps = Deps(
        lifecycle=lifecycle,
        catalog=catalog,
        authz=authz,
        index=index,
        capability=capability,
        environments=cfg.environments,
        reaper_ack=reaper.acknowledge_warning,
        channel_default=channel_default,
        statuses=bridge.statuses,
    )
    if slack_app is not None:
        from deployground.slack.app import build_slack_app

        build_slack_app(deps, slack_app)

    return Application(
        config=cfg,
        index=index,
        bridge=bridge,
        reaper=reaper,
        status_source=source,
        slack_client=slack_client,
        deps=deps,
        slack_app=slack_app,
        watcher=watcher,
        env=env,
    )
