from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError

from deployground.config.models import Config, WorkloadChartCfg, parse_duration

VALID = {
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
    "stacks": {
        "api": {
            "components": {
                "api": {
                    "image": "ghcr.io/my-org/api",
                    "defaultVersion": "v1.2.3",
                    "port": 8080,
                }
            }
        }
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


def make(**overrides):
    data = deepcopy(VALID)
    data.update(overrides)
    return data


def test_minimal_valid_config_parses():
    cfg = Config.model_validate(VALID)
    cfg.validate_refs()
    assert cfg.stacks["api"].components["api"].replicas == 1
    assert cfg.lifecycle.maxLivePerUser == 10
    assert parse_duration(cfg.lifecycle.warnBefore) == timedelta(hours=1)
    assert parse_duration(cfg.lifecycle.reaperInterval) == timedelta(minutes=1)


def test_component_image_pull_secrets_and_service_account_default_empty():
    cfg = Config.model_validate(VALID)
    comp = cfg.stacks["api"].components["api"]
    assert comp.imagePullSecrets == []
    assert comp.serviceAccountName is None


def test_component_image_pull_secrets_and_service_account_round_trip():
    data = make()
    data["stacks"]["api"]["components"]["api"]["imagePullSecrets"] = ["harbor-pull"]
    data["stacks"]["api"]["components"]["api"]["serviceAccountName"] = "my-irsa-sa"
    cfg = Config.model_validate(data)
    comp = cfg.stacks["api"].components["api"]
    assert comp.imagePullSecrets == ["harbor-pull"]
    assert comp.serviceAccountName == "my-irsa-sa"


def test_rule_requires_exactly_one_principal():
    with pytest.raises(ValidationError):
        Config.model_validate(make(access={"rules": [{"user": "U1", "group": "S1"}]}))
    with pytest.raises(ValidationError):
        Config.model_validate(make(access={"rules": [{"environments": ["*"]}]}))


def test_env_referencing_unknown_repo_fails():
    data = make()
    data["environments"]["homelab"]["repo"] = "missing-repo"
    cfg = Config.model_validate(data)
    with pytest.raises(ValueError, match="missing-repo"):
        cfg.validate_refs()


def test_unsupported_git_provider_rejected():
    data = make()
    data["git"]["provider"] = "gitlab"
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_deploy_target_unknown_repo_fails():
    data = make()
    data["deployTarget"]["repo"] = "nowhere"
    with pytest.raises(ValueError, match="nowhere"):
        Config.model_validate(data).validate_refs()


def test_access_rule_unknown_environment_fails():
    data = make(access={"rules": [{"user": "U1", "environments": ["staging"]}]})
    with pytest.raises(ValueError, match="staging"):
        Config.model_validate(data).validate_refs()


def test_stack_image_outside_allowed_registry_fails():
    data = make()
    data["stacks"]["api"]["components"]["api"]["image"] = "docker.io/evil/api"
    with pytest.raises(ValueError, match="docker.io/evil/api"):
        Config.model_validate(data).validate_refs()


def test_registry_match_respects_path_boundary():
    data = make()
    data["stacks"]["api"]["components"]["api"]["image"] = "ghcr.io/my-org-evil/api"
    with pytest.raises(ValueError, match="my-org-evil"):
        Config.model_validate(data).validate_refs()


def test_ttl_none_parses():
    data = make()
    data["environments"]["homelab"]["ttl"] = {"default": "none"}
    cfg = Config.model_validate(data)
    ttl = cfg.environments["homelab"].ttl
    assert ttl.default == "none"
    assert parse_duration(ttl.default) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15s", timedelta(seconds=15)),
        ("1m", timedelta(minutes=1)),
        ("24h", timedelta(hours=24)),
        ("168h", timedelta(hours=168)),
        ("none", None),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["", "24", "h", "1d", "-1h", "1.5h"])
def test_parse_duration_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_invalid_duration_rejected_at_parse_time():
    data = make()
    data["environments"]["homelab"]["ttl"] = {"default": "soon"}
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_deploy_target_default_workload_chart_is_oci_golden_path():
    cfg = Config.model_validate(VALID)
    chart = cfg.deployTarget.workloadChart
    assert chart.type == "oci"
    assert chart.repo == "ghcr.io/rhysmcneill"
    assert chart.chart == "golden-path-chart"
    assert chart.version is None


def test_workload_chart_oci_override_parses():
    data = make()
    data["deployTarget"] = {
        **data["deployTarget"],
        "workloadChart": {"repo": "ghcr.io/my-org", "chart": "my-chart", "version": "1.2.3"},
    }
    cfg = Config.model_validate(data)
    chart = cfg.deployTarget.workloadChart
    assert chart.repo == "ghcr.io/my-org"
    assert chart.chart == "my-chart"
    assert chart.version == "1.2.3"


def test_workload_chart_git_requires_repo_and_path():
    with pytest.raises(ValidationError, match="gitRepo"):
        WorkloadChartCfg(type="git")
    with pytest.raises(ValidationError, match="gitRepo"):
        WorkloadChartCfg(type="git", gitRepo="https://github.com/acme/charts.git")


def test_workload_chart_git_with_repo_and_path_parses():
    chart = WorkloadChartCfg(
        type="git",
        gitRepo="https://github.com/acme/charts.git",
        gitRef="main",
        chartPath="charts/custom",
    )
    assert chart.gitRepo == "https://github.com/acme/charts.git"
    assert chart.chartPath == "charts/custom"
