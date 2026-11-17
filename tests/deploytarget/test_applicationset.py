from datetime import UTC, datetime

import yaml

import deployground
from deployground.catalog import ResolvedComponent
from deployground.config.models import Config, DeployTargetCfg, RepoRef, TokenRef
from deployground.deploytarget.applicationset import ApplicationSetTarget
from deployground.metadata import ComponentRecord, to_annotations, to_metadata_block

REPO = RepoRef(owner="acme", name="deploys", branch="main", authRef=TokenRef(tokenEnv="TOK"))
CFG = DeployTargetCfg(
    type="applicationset", repo="deploys", pathTemplate="{env}/{stack}/{component}/config.yaml"
)
EXPIRES = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
NAMES = ["api", "web", "worker"]
TRIGGERS = ("on-deployed", "on-health-degraded", "on-sync-failed")


def resolved(name: str) -> ResolvedComponent:
    is_api = name == "api"
    return ResolvedComponent(
        image=f"ghcr.io/org/{name}",
        defaultVersion="1.0.0",
        version="1.2.3",
        port=8080 if is_api else None,
        replicas=2,
        ingressHost="demo-api.home.example" if is_api else None,
        env={"KEY": "value"} if is_api else {},
        secretRefs=["db"] if is_api else [],
        imagePullSecrets=["harbor-pull"] if is_api else [],
        serviceAccountName="my-irsa-sa" if is_api else None,
        ingressClassName="tailscale" if is_api else None,
    )


def record(name: str) -> ComponentRecord:
    return ComponentRecord(
        stack="demo",
        component=name,
        owner="U012ABC",
        owner_email="a@example.com",
        env="dev",
        expires_at=EXPIRES,
        version="1.2.3",
        thread_ts="1.1",
        channel="C1",
    )


def make_config() -> Config:
    return Config.model_validate(
        {
            "git": {
                "provider": "github",
                "repos": {
                    "deploys": {
                        "owner": "acme",
                        "name": "deploys",
                        "branch": "main",
                        "authRef": {"tokenEnv": "TOK"},
                    }
                },
            },
            "environments": {
                name: {
                    "cluster": cluster,
                    "repo": "deploys",
                    "autoMerge": True,
                    "ttl": {},
                    "requiresApproval": False,
                }
                for name, cluster in (("dev", "dev-cluster"), ("prod", "prod-eks"))
            },
            "stacks": {},
            "deployTarget": {
                "type": "applicationset",
                "repo": "deploys",
                "pathTemplate": "{env}/{stack}/{component}/config.yaml",
            },
            "statusSource": {"type": "argocd-notifications", "webhookPath": "/hooks/argocd"},
        }
    )


def make_files():
    return ApplicationSetTarget(CFG, REPO).files_for(
        "demo",
        "dev",
        {n: resolved(n) for n in NAMES},
        {n: record(n) for n in NAMES},
    )


def test_files_one_per_component_at_path_template():
    changes = make_files()
    assert sorted(c.path for c in changes) == [f"dev/demo/{n}/config.yaml" for n in NAMES]
    assert all(c.repo == REPO and c.content is not None for c in changes)


def test_file_contains_workload_and_deployground_block():
    by_path = {c.path: yaml.safe_load(c.content) for c in make_files()}
    api = by_path["dev/demo/api/config.yaml"]
    assert api["image"] == "ghcr.io/org/api:1.2.3"
    assert api["port"] == 8080
    assert api["replicas"] == 2
    assert api["ingressHost"] == "demo-api.home.example"
    assert api["env"] == {"KEY": "value"}
    assert api["secretRefs"] == ["db"]
    assert api["imagePullSecrets"] == ["harbor-pull"]
    assert api["serviceAccountName"] == "my-irsa-sa"
    assert api["ingressClassName"] == "tailscale"
    block = api["deployground"]
    assert block["owner"] == "U012ABC"
    assert block["expiresAt"] == "2026-11-13T09:00:00Z"
    assert block["version"] == "1.2.3"
    assert block["threadTs"] == "1.1"
    assert block == to_metadata_block(record("api"))
    web = by_path["dev/demo/web/config.yaml"]
    omitted = (
        "port",
        "ingressHost",
        "env",
        "secretRefs",
        "imagePullSecrets",
        "serviceAccountName",
        "ingressClassName",
    )
    for key in omitted:
        assert key not in web


def test_delete_files_targets_all_components():
    changes = ApplicationSetTarget(CFG, REPO).delete_files_for("demo", "dev", NAMES)
    assert sorted(c.path for c in changes) == [f"dev/demo/{n}/config.yaml" for n in NAMES]
    assert all(c.content is None and c.repo == REPO for c in changes)


def test_bootstrap_applicationset_templates_labels_annotations_and_subscription():
    manifests = ApplicationSetTarget(CFG, REPO).bootstrap_manifests(make_config())
    assert set(manifests) == {
        "applicationset-dev.yaml",
        "applicationset-prod.yaml",
        "argocd-notifications-cm.yaml",
    }
    appset = yaml.safe_load(manifests["applicationset-dev.yaml"])
    assert appset["kind"] == "ApplicationSet"

    gen = appset["spec"]["generators"][0]["git"]
    assert gen["repoURL"] == "https://github.com/acme/deploys.git"
    assert gen["revision"] == "main"
    assert gen["files"] == [{"path": "dev/*/*/config.yaml"}]

    tmpl = appset["spec"]["template"]
    assert tmpl["metadata"]["labels"] == {
        "deployground.io/stack": "{{deployground.stack}}",
        "deployground.io/component": "{{deployground.component}}",
        "deployground.io/managed": "true",
    }
    annotations = tmpl["metadata"]["annotations"]
    for key in to_annotations(record("api")):
        assert key in annotations
    assert annotations["deployground.io/owner-email"] == "{{deployground.ownerEmail}}"
    assert annotations["deployground.io/expires-at"] == "{{deployground.expiresAt}}"
    assert annotations["deployground.io/thread-ts"] == "{{deployground.threadTs}}"
    for trigger in TRIGGERS:
        assert annotations[f"notifications.argoproj.io/subscribe.{trigger}.deployground"] == ""

    assert tmpl["spec"]["syncPolicy"]["automated"]["prune"] is True
    assert "resources-finalizer.argocd.argoproj.io" in tmpl["metadata"]["finalizers"]

    sources = tmpl["spec"]["sources"]
    assert len(sources) == 2
    chart, values_ref = sources
    assert chart["repoURL"] == "oci://ghcr.io/rhysmcneill/golden-path-chart"
    assert chart["targetRevision"] == deployground.__version__
    assert chart["helm"]["valueFiles"] == ["$values/{{path.path}}/{{path.filename}}"]
    assert values_ref["repoURL"] == "https://github.com/acme/deploys.git"
    assert values_ref["targetRevision"] == "main"
    assert values_ref["ref"] == "values"


def test_bootstrap_applicationset_chart_source_uses_git_override():
    cfg = DeployTargetCfg(
        type="applicationset",
        repo="deploys",
        pathTemplate=CFG.pathTemplate,
        workloadChart={
            "type": "git",
            "gitRepo": "https://github.com/acme/charts.git",
            "gitRef": "v1.0.0",
            "chartPath": "charts/custom",
        },
    )
    manifests = ApplicationSetTarget(cfg, REPO).bootstrap_manifests(make_config())
    appset = yaml.safe_load(manifests["applicationset-dev.yaml"])
    chart = appset["spec"]["template"]["spec"]["sources"][0]
    assert chart["repoURL"] == "https://github.com/acme/charts.git"
    assert chart["targetRevision"] == "v1.0.0"
    assert chart["path"] == "charts/custom"


def test_bootstrap_applicationset_chart_source_pins_explicit_version():
    cfg = DeployTargetCfg(
        type="applicationset",
        repo="deploys",
        pathTemplate=CFG.pathTemplate,
        workloadChart={"version": "9.9.9"},
    )
    manifests = ApplicationSetTarget(cfg, REPO).bootstrap_manifests(make_config())
    appset = yaml.safe_load(manifests["applicationset-dev.yaml"])
    chart = appset["spec"]["template"]["spec"]["sources"][0]
    assert chart["targetRevision"] == "9.9.9"

    cm = yaml.safe_load(manifests["argocd-notifications-cm.yaml"])
    assert cm["kind"] == "ConfigMap"
    assert "/hooks/argocd" in cm["data"]["service.webhook.deployground"]
    for trigger in TRIGGERS:
        sends = yaml.safe_load(cm["data"][f"trigger.{trigger}"])[0]["send"]
        assert len(sends) == 1
        tmpl_yaml = yaml.safe_load(cm["data"][f"template.{sends[0]}"])
        body = tmpl_yaml["webhook"]["deployground"]["body"]
        assert f'"trigger": "{trigger}"' in body
    assert not any("{{.trigger}}" in v for v in cm["data"].values())
    assert "X-Deployground-Token" not in cm["data"]["service.webhook.deployground"]


def test_bootstrap_one_applicationset_per_env_with_its_cluster():
    manifests = ApplicationSetTarget(CFG, REPO).bootstrap_manifests(make_config())
    dev = yaml.safe_load(manifests["applicationset-dev.yaml"])
    prod = yaml.safe_load(manifests["applicationset-prod.yaml"])
    assert dev["metadata"]["name"] == "deployground-dev"
    assert prod["metadata"]["name"] == "deployground-prod"
    assert dev["spec"]["template"]["spec"]["destination"]["name"] == "dev-cluster"
    assert prod["spec"]["template"]["spec"]["destination"]["name"] == "prod-eks"
    assert prod["spec"]["generators"][0]["git"]["files"] == [{"path": "prod/*/*/config.yaml"}]
