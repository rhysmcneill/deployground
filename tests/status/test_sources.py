import asyncio
import json

import pytest

from deployground.config.models import StatusSourceCfg
from deployground.states import CompState
from deployground.status import make_status_source
from deployground.status.argocd_api import ArgoApiSource
from deployground.status.base import StatusEvent
from deployground.status.notifications import NotificationsSource

LABELS = {"deployground.io/stack": "s1", "deployground.io/component": "web"}


def _post(app, path: str, body: bytes, method: str = "POST", headers=None) -> int:
    """Drive the raw ASGI app directly and return the response status."""
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": headers or []}
    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _notif_source():
    cfg = StatusSourceCfg(type="argocd-notifications", webhookPath="/webhooks/argocd")
    events: list[StatusEvent] = []
    src = NotificationsSource(cfg)
    src.start(events.append)
    return src, events


def _payload(labels, health="Healthy", trigger="on-deployed"):
    return json.dumps(
        {"app": "a", "labels": labels, "health": health, "sync": "Synced", "trigger": trigger}
    ).encode()


def test_notifications_payload_maps_labels_to_event():
    src, events = _notif_source()
    assert _post(src.asgi_app, "/webhooks/argocd", _payload(LABELS)) == 204
    assert _post(src.asgi_app, "/webhooks/argocd", _payload(LABELS, "Degraded")) == 204
    assert _post(src.asgi_app, "/webhooks/argocd", _payload(LABELS, "Progressing")) == 204
    # a sync failure is Failed even though health still reads Healthy
    assert (
        _post(src.asgi_app, "/webhooks/argocd", _payload(LABELS, "Healthy", "on-sync-failed"))
        == 204
    )
    assert [e.state for e in events] == [
        CompState.Healthy,
        CompState.Failed,
        CompState.Progressing,
        CompState.Failed,
    ]
    assert events[0] == StatusEvent("s1", "web", CompState.Healthy)


def test_notifications_unknown_labels_ignored():
    src, events = _notif_source()
    assert _post(src.asgi_app, "/webhooks/argocd", _payload({"other": "x"})) == 204
    assert _post(src.asgi_app, "/webhooks/argocd", b"not json") == 204
    assert _post(src.asgi_app, "/nope", _payload(LABELS)) == 404
    assert _post(src.asgi_app, "/webhooks/argocd", b"", method="GET") == 405
    assert events == []


def _app(stack, comp, health):
    return {
        "metadata": {"labels": {"deployground.io/stack": stack, "deployground.io/component": comp}},
        "status": {"health": {"status": health}},
    }


def test_api_poll_emits_on_change(monkeypatch):
    monkeypatch.setenv("ARGO_TOKEN", "sekret")
    cfg = StatusSourceCfg(
        type="argocd-api",
        url="https://argo.example",
        tokenRef={"tokenEnv": "ARGO_TOKEN"},
        pollInterval="15s",
    )
    responses = [
        {"items": [_app("s1", "web", "Progressing"), {"metadata": {"labels": {}}}]},
        {"items": [_app("s1", "web", "Progressing")]},
        {"items": [_app("s1", "web", "Healthy")]},
    ]
    calls: list[tuple[str, dict]] = []

    def fake_get(url, headers):
        calls.append((url, headers))
        return responses[len(calls) - 1]

    src = ArgoApiSource(cfg, http_get=fake_get)
    events: list[StatusEvent] = []
    for _ in responses:
        src._poll_once(events.append)
    assert events == [
        StatusEvent("s1", "web", CompState.Progressing),
        StatusEvent("s1", "web", CompState.Healthy),
    ]
    url, headers = calls[0]
    assert url == "https://argo.example/api/v1/applications?selector=deployground.io/managed=true"
    assert headers["Authorization"] == "Bearer sekret"


def test_factory_selects_by_type():
    n = make_status_source(StatusSourceCfg(type="argocd-notifications", webhookPath="/w"))
    a = make_status_source(
        StatusSourceCfg(
            type="argocd-api",
            url="https://x",
            tokenRef={"tokenEnv": "T"},
            pollInterval="1m",
        )
    )
    assert isinstance(n, NotificationsSource)
    assert isinstance(a, ArgoApiSource)
    with pytest.raises(ValueError):
        make_status_source(StatusSourceCfg.model_construct(type="bogus"))


def _secured(monkeypatch):
    monkeypatch.setenv("HOOK_TOKEN", "s3cret")
    cfg = StatusSourceCfg(
        type="argocd-notifications",
        webhookPath="/w",
        secretRef={"tokenEnv": "HOOK_TOKEN"},
    )
    events: list[StatusEvent] = []
    src = NotificationsSource(cfg)
    src.start(events.append)
    return src, events


def test_notifications_secret_required_when_configured(monkeypatch):
    src, events = _secured(monkeypatch)

    def hdr(v):
        return [(b"x-deployground-token", v)]

    assert _post(src.asgi_app, "/w", _payload(LABELS)) == 401
    assert _post(src.asgi_app, "/w", _payload(LABELS), headers=hdr(b"wrong")) == 401
    assert events == []
    assert _post(src.asgi_app, "/w", _payload(LABELS), headers=hdr(b"s3cret")) == 204
    assert len(events) == 1


def test_notifications_unauthenticated_warns_once(caplog):
    with caplog.at_level("WARNING"):
        src, events = _notif_source()
    assert sum("unauthenticated" in r.message for r in caplog.records) == 1
    assert _post(src.asgi_app, "/webhooks/argocd", _payload(LABELS)) == 204
    assert len(events) == 1
