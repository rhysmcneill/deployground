import pytest

from deployground.catalog import Catalog, UnknownComponent, UnknownStack, resolve_ingress_host
from deployground.config.models import Config

BASE = {
    "git": {
        "provider": "github",
        "repos": {
            "gitops": {
                "owner": "my-org",
                "name": "gitops",
                "branch": "main",
                "authRef": {"tokenEnv": "GITHUB_TOKEN"},
            }
        },
    },
    "environments": {
        "homelab": {
            "cluster": "homelab",
            "repo": "gitops",
            "autoMerge": True,
            "ttl": {"default": "24h", "max": "168h"},
            "requiresApproval": False,
        }
    },
    "catalog": {
        "api": {
            "components": {
                "api": {
                    "image": "ghcr.io/my-org/api",
                    "defaultVersion": "v1.0.0",
                    "port": 8080,
                }
            }
        },
        "shop": {
            "components": {
                "web": {"image": "ghcr.io/my-org/web", "defaultVersion": "v2.0.0"},
                "worker": {"image": "ghcr.io/my-org/worker", "defaultVersion": "v3.0.0"},
                "cache": {"image": "ghcr.io/my-org/cache", "defaultVersion": "v4.0.0"},
            }
        },
    },
    "capabilities": {
        "image-deploy": {"enabled": True, "allowedRegistries": ["ghcr.io/my-org"]}
    },
    "access": {"rules": [{"user": "U012ABC", "environments": ["*"]}]},
    "deployTarget": {
        "type": "applicationset",
        "repo": "gitops",
        "pathTemplate": "{env}/{stack}/{component}/config.yaml",
    },
    "statusSource": {"type": "argocd-notifications", "webhookPath": "/argocd/notifications"},
}


def make_catalog() -> Catalog:
    cfg = Config.model_validate(BASE)
    return Catalog(lambda: cfg)


def test_single_service_is_stack_of_one():
    catalog = make_catalog()
    assert catalog.list_entries() == ["api", "shop"]
    stack = catalog.get("api")
    assert list(stack.components) == ["api"]
    resolved = catalog.resolve_versions("api", {})
    assert set(resolved) == {"api"}
    assert resolved["api"].version == "v1.0.0"
    assert resolved["api"].image == "ghcr.io/my-org/api"
    assert resolved["api"].port == 8080


def test_resolve_versions_preserves_image_pull_secrets_and_service_account():
    cfg = Config.model_validate(BASE)
    cfg.catalog["api"].components["api"].imagePullSecrets = ["harbor-pull"]
    cfg.catalog["api"].components["api"].serviceAccountName = "my-irsa-sa"
    catalog = Catalog(lambda: cfg)
    resolved = catalog.resolve_versions("api", {})
    assert resolved["api"].imagePullSecrets == ["harbor-pull"]
    assert resolved["api"].serviceAccountName == "my-irsa-sa"


def test_resolve_versions_preserves_ingress_class_name():
    cfg = Config.model_validate(BASE)
    cfg.catalog["api"].components["api"].ingressClassName = "tailscale"
    catalog = Catalog(lambda: cfg)
    resolved = catalog.resolve_versions("api", {})
    assert resolved["api"].ingressClassName == "tailscale"


def test_resolve_uses_default_when_no_override():
    resolved = make_catalog().resolve_versions("shop", {})
    assert {name: r.version for name, r in resolved.items()} == {
        "web": "v2.0.0",
        "worker": "v3.0.0",
        "cache": "v4.0.0",
    }


def test_resolve_applies_override_per_component():
    resolved = make_catalog().resolve_versions("shop", {"worker": "v3.1.0"})
    assert resolved["worker"].version == "v3.1.0"
    assert resolved["web"].version == "v2.0.0"
    assert resolved["cache"].version == "v4.0.0"


def test_unknown_component_override_raises():
    with pytest.raises(UnknownComponent, match="nope"):
        make_catalog().resolve_versions("shop", {"nope": "v9"})


def test_unknown_stack_raises():
    with pytest.raises(UnknownStack, match="missing"):
        make_catalog().get("missing")


def test_resolve_ingress_host_substitutes_stack_placeholder():
    assert resolve_ingress_host("{stack}", None, "pr-123") == "pr-123"
    assert (
        resolve_ingress_host("{stack}.apps.example.com", None, "pr-123")
        == "pr-123.apps.example.com"
    )


def test_resolve_ingress_host_leaves_literal_host_unchanged():
    assert resolve_ingress_host("fixed.example.com", None, "pr-123") == "fixed.example.com"


def test_resolve_ingress_host_defaults_to_stack_name_when_only_ingress_class_is_set():
    assert resolve_ingress_host(None, "tailscale", "pr-123") == "pr-123"


def test_resolve_ingress_host_stays_private_with_neither_set():
    assert resolve_ingress_host(None, None, "pr-123") is None
