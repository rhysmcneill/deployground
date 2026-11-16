import yaml

from deployground.deploytarget.notifications import (
    DEFAULT_WEBHOOK_BASE_URL,
    notifications_cm,
)
from tests.deploytarget.test_applicationset import make_config


def service_url(**status) -> str:
    cfg = make_config()
    cfg = cfg.model_copy(update={"statusSource": cfg.statusSource.model_copy(update=status)})
    cm = notifications_cm(cfg)
    return yaml.safe_load(cm["data"]["service.webhook.deployground"])["url"]


def test_default_base_url_plus_path():
    assert service_url() == f"{DEFAULT_WEBHOOK_BASE_URL}/hooks/argocd"


def test_configured_base_url_plus_path():
    assert service_url(webhookUrl="https://dg.example.com:8443") == (
        "https://dg.example.com:8443/hooks/argocd"
    )


def test_trailing_slash_in_base_url_is_stripped():
    assert service_url(webhookUrl="https://dg.example.com/") == (
        "https://dg.example.com/hooks/argocd"
    )
