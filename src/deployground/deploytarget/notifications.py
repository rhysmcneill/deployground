"""Argo CD notifications ConfigMap shared by every deploy target.

Wires the `deployground` webhook service, status template and the three triggers that
Applications subscribe to via `notifications.argoproj.io/subscribe.*` annotations.
"""

from typing import Any

import yaml

from deployground.config.models import Config

SUBSCRIBE_TRIGGERS = ("on-deployed", "on-health-degraded", "on-sync-failed")
NOTIFICATION_SERVICE = "deployground"
DEFAULT_WEBHOOK_PATH = "/webhooks/argocd"


def subscribe_annotations() -> dict[str, str]:
    return {
        f"notifications.argoproj.io/subscribe.{t}.{NOTIFICATION_SERVICE}": ""
        for t in SUBSCRIBE_TRIGGERS
    }


# Argo Notifications templates have no `.trigger` variable, so each trigger gets its own
# template that hardcodes its name as a literal; NotificationsSource reads that value.
WEBHOOK_TOKEN_HEADER = "X-Deployground-Token"
WEBHOOK_TOKEN_SECRET = "$deployground-webhook-token"


def template_name(trigger: str) -> str:
    return f"{NOTIFICATION_SERVICE}-{trigger.removeprefix('on-')}"


def _notify_body(trigger: str) -> str:
    return (
        '{"app": "{{.app.metadata.name}}", '
        '"labels": {{.app.metadata.labels | toJson}}, '
        '"health": "{{.app.status.health.status}}", '
        '"sync": "{{.app.status.sync.status}}", '
        f'"trigger": "{trigger}"}}'
    )


_TRIGGER_WHEN = {
    "on-deployed": (
        "app.status.operationState.phase in ['Succeeded'] and app.status.health.status == 'Healthy'"
    ),
    "on-health-degraded": "app.status.health.status == 'Degraded'",
    "on-sync-failed": "app.status.operationState.phase in ['Error', 'Failed']",
}


def notifications_cm(config: Config) -> dict[str, Any]:
    webhook_path = config.statusSource.webhookPath or DEFAULT_WEBHOOK_PATH
    headers = [{"name": "Content-Type", "value": "application/json"}]
    if config.statusSource.secretRef is not None:
        # Operator: set `deployground-webhook-token` in argocd-notifications-secret to the value
        # of the env var named by statusSource.secretRef.tokenEnv on the bot.
        headers.append({"name": WEBHOOK_TOKEN_HEADER, "value": WEBHOOK_TOKEN_SECRET})
    data: dict[str, str] = {
        f"service.webhook.{NOTIFICATION_SERVICE}": yaml.safe_dump(
            {
                # Placeholder host: adjust to wherever the bot's webhook is reachable.
                "url": f"http://deployground.deployground.svc.cluster.local{webhook_path}",
                "headers": headers,
            },
            sort_keys=False,
        ),
    }
    for trigger, when in _TRIGGER_WHEN.items():
        data[f"template.{template_name(trigger)}"] = yaml.safe_dump(
            {"webhook": {NOTIFICATION_SERVICE: {"method": "POST", "body": _notify_body(trigger)}}},
            sort_keys=False,
        )
        data[f"trigger.{trigger}"] = yaml.safe_dump(
            [{"when": when, "send": [template_name(trigger)]}], sort_keys=False
        )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "argocd-notifications-cm", "namespace": "argocd"},
        "data": data,
    }
