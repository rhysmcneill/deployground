from datetime import UTC, datetime

import pytest
import yaml

from deployground.capability.base import GuardrailViolation, RenderContext, RenderInputs
from deployground.capability.image_deploy import ImageDeployCapability
from deployground.catalog import Catalog
from deployground.config.models import CapabilityCfg, Config
from deployground.deploytarget.applicationset import ApplicationSetTarget
from deployground.metadata import ComponentRecord, StackRecord, parse_metadata_block
from tests.deploytarget.test_applicationset import CFG, REPO, make_config

EXPIRES = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def make_cfg(ingress_host="demo-api.home.example") -> Config:
    base = make_config().model_dump()
    base["environments"]["prod"]["requiresApproval"] = True
    base["environments"]["dev"]["ttl"] = {"default": "24h", "max": "72h"}
    base["catalog"] = {
        "demo": {
            "components": {
                "api": {
                    "image": "ghcr.io/org/api",
                    "defaultVersion": "1.0.0",
                    "port": 8080,
                    "ingressHost": ingress_host,
                },
                "web": {"image": "ghcr.io/org/web", "defaultVersion": "2.0.0"},
            }
        }
    }
    base["capabilities"] = {
        "image-deploy": {
            "allowedRegistries": ["ghcr.io/org"],
            "ingressDomains": ["home.example"],
        }
    }
    return Config.model_validate(base)


def make_ctx(config: Config | None = None) -> RenderContext:
    config = config or make_cfg()
    return RenderContext(
        catalog=Catalog(lambda: config),
        deploy_target=ApplicationSetTarget(CFG, REPO),
        capability_cfg=config.capabilities["image-deploy"],
        environments=config.environments,
    )


def make_inputs(**kw) -> RenderInputs:
    args = dict(
        catalog="demo",
        stack="demo",
        env="dev",
        overrides={},
        ttl="24h",
        owner="U012ABC",
        owner_email="a@example.com",
        thread_ts="1.1",
        channel="C1",
        expires_at=EXPIRES,
    )
    args.update(kw)
    return RenderInputs(**args)


def test_render_emits_all_component_files_in_one_batch():
    changes = ImageDeployCapability().render(make_inputs(), make_ctx())
    assert sorted(c.path for c in changes) == [
        "dev/demo/api/config.yaml",
        "dev/demo/system/config.yaml",
        "dev/demo/web/config.yaml",
    ]
    assert all(c.content for c in changes)


def test_render_file_includes_deployground_block():
    changes = ImageDeployCapability().render(make_inputs(), make_ctx())
    by_path = {c.path: yaml.safe_load(c.content) for c in changes}
    record = parse_metadata_block(by_path["dev/demo/api/config.yaml"])
    assert record == ComponentRecord(
        catalog="demo",
        stack="demo",
        component="api",
        owner="U012ABC",
        owner_email="a@example.com",
        env="dev",
        expires_at=EXPIRES,
        version="1.0.0",
        thread_ts="1.1",
        channel="C1",
    )


def test_render_resolves_versions_from_catalog_not_stack_name():
    changes = ImageDeployCapability().render(
        make_inputs(catalog="demo", stack="pr-123"), make_ctx()
    )
    by_path = {c.path: yaml.safe_load(c.content) for c in changes}
    assert sorted(by_path) == [
        "dev/pr-123/api/config.yaml",
        "dev/pr-123/system/config.yaml",
        "dev/pr-123/web/config.yaml",
    ]
    for path in by_path:
        if path == "dev/pr-123/system/config.yaml":
            continue
        record = parse_metadata_block(by_path[path])
        assert record.stack == "pr-123"
        assert record.catalog == "demo"
    assert parse_metadata_block(by_path["dev/pr-123/api/config.yaml"]).version == "1.0.0"


def test_render_applies_version_overrides():
    changes = ImageDeployCapability().render(
        make_inputs(overrides={"web": {"version": "9.9.9"}}), make_ctx()
    )
    by_path = {c.path: yaml.safe_load(c.content) for c in changes}
    assert parse_metadata_block(by_path["dev/demo/web/config.yaml"]).version == "9.9.9"
    assert parse_metadata_block(by_path["dev/demo/api/config.yaml"]).version == "1.0.0"


def test_render_rejects_disallowed_ingress_domain():
    ctx = make_ctx(make_cfg(ingress_host="demo-api.evil.example"))
    with pytest.raises(GuardrailViolation, match="ingress"):
        ImageDeployCapability().render(make_inputs(), ctx)


def test_render_rejects_disallowed_registry():
    config = make_cfg()
    ctx = RenderContext(
        catalog=Catalog(lambda: config),
        deploy_target=ApplicationSetTarget(CFG, REPO),
        capability_cfg=CapabilityCfg(
            allowedRegistries=["ghcr.io/other"], ingressDomains=["home.example"]
        ),
        environments=config.environments,
    )
    with pytest.raises(GuardrailViolation, match="registry"):
        ImageDeployCapability().render(make_inputs(), ctx)


def test_destroy_emits_delete_for_all_components():
    stack = StackRecord(
        name="demo",
        catalog="demo",
        env="dev",
        owner="U012ABC",
        expires_at=EXPIRES,
        thread_ts="1.1",
        channel="C1",
        components={"api": object(), "web": object()},  # only names are used
    )
    changes = ImageDeployCapability().destroy(stack, make_ctx())
    assert sorted(c.path for c in changes) == [
        "dev/demo/api/config.yaml",
        "dev/demo/system/config.yaml",
        "dev/demo/web/config.yaml",
    ]
    assert all(c.content is None for c in changes)


def test_needs_approval_follows_env():
    cap = ImageDeployCapability()
    ctx = make_ctx()
    inputs = make_inputs()
    assert cap.needs_approval(inputs, ctx.environments["dev"]) is False
    assert cap.needs_approval(inputs, ctx.environments["prod"]) is True
    assert cap.ttl_policy("dev", ctx).max == "72h"
