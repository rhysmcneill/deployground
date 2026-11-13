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


_NOTIFY_BODY = (
    '{"app": "{{.app.metadata.name}}", '
    '"labels": {{.app.metadata.labels | toJson}}, '
    '"health": "{{.app.status.health.status}}", '
    '"sync": "{{.app.status.sync.status}}", '
    '"trigger": "{{.trigger}}"}'
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
    template_name = f"{NOTIFICATION_SERVICE}-status"
    data: dict[str, str] = {
        f"service.webhook.{NOTIFICATION_SERVICE}": yaml.safe_dump(
            {
                # Placeholder host: adjust to wherever the bot's webhook is reachable.
                "url": f"http://deployground.deployground.svc.cluster.local{webhook_path}",
                "headers": [{"name": "Content-Type", "value": "application/json"}],
            },
            sort_keys=False,
        ),
        f"template.{template_name}": yaml.safe_dump(
            {
                "webhook": {
                    NOTIFICATION_SERVICE: {
                        "method": "POST",
                        "body": _NOTIFY_BODY,
                    }
                }
            },
            sort_keys=False,
        ),
    }
    for trigger, when in _TRIGGER_WHEN.items():
        data[f"trigger.{trigger}"] = yaml.safe_dump(
            [{"when": when, "send": [template_name]}], sort_keys=False
        )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "argocd-notifications-cm", "namespace": "argocd"},
        "data": data,
    }
