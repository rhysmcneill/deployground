import pytest
import yaml

import deployground
from deployground.config.models import DeployTargetCfg
from deployground.deploytarget import make_deploy_target
from deployground.deploytarget.application import ApplicationTarget
from deployground.deploytarget.applicationset import ApplicationSetTarget
from deployground.index import StackIndex
from deployground.metadata import to_annotations
from tests.deploytarget.test_applicationset import (
    NAMES,
    REPO,
    TRIGGERS,
    make_config,
    record,
    resolved,
)

CFG = DeployTargetCfg(
    type="application", repo="deploys", pathTemplate="{env}/{stack}/{component}/app.yaml"
)
CLUSTERS = {"dev": "dev-cluster", "prod": "prod-eks"}


def make_files(env="dev"):
    return ApplicationTarget(CFG, REPO, CLUSTERS).files_for(
        "demo", env, {n: resolved(n) for n in NAMES}, {n: record(n) for n in NAMES}
    )


def test_application_cr_per_component_has_labels_metadata_and_subscription_annotations():
    changes = make_files()
    assert sorted(c.path for c in changes) == [f"dev/demo/{n}/app.yaml" for n in NAMES]
    by_path = {c.path: yaml.safe_load(c.content) for c in changes}
    app = by_path["dev/demo/api/app.yaml"]
    assert app["apiVersion"] == "argoproj.io/v1alpha1"
    assert app["kind"] == "Application"
    assert "deployground" not in app  # unknown top-level keys would make the CR invalid
    meta = app["metadata"]
    assert meta["name"] == "demo-api"
    assert meta["namespace"] == "argocd"
    assert meta["labels"] == {
        "deployground.io/stack": "demo",
        "deployground.io/component": "api",
        "deployground.io/managed": "true",
    }
    for key, value in to_annotations(record("api")).items():
        assert meta["annotations"][key] == value
    for trigger in TRIGGERS:
        subscribe = f"notifications.argoproj.io/subscribe.{trigger}.deployground"
        assert meta["annotations"][subscribe] == ""
    assert "resources-finalizer.argocd.argoproj.io" in meta["finalizers"]
    spec = app["spec"]
    assert spec["syncPolicy"]["automated"]["prune"] is True
    assert spec["destination"] == {"name": "dev-cluster", "namespace": "demo"}
    sources = spec["sources"]
    assert len(sources) == 1
    chart = sources[0]
    assert chart["repoURL"] == "oci://ghcr.io/rhysmcneill/golden-path-chart"
    assert chart["targetRevision"] == deployground.__version__
    values = yaml.safe_load(chart["helm"]["values"])
    assert values == {
        "image": "ghcr.io/org/api:1.2.3",
        "port": 8080,
        "replicas": 2,
        "ingressHost": "demo-api.home.example",
        "env": {"KEY": "value"},
        "secretRefs": ["db"],
        "imagePullSecrets": ["harbor-pull"],
        "serviceAccountName": "my-irsa-sa",
        "ingressClassName": "tailscale",
    }
    web = by_path["dev/demo/web/app.yaml"]
    web_values = yaml.safe_load(web["spec"]["sources"][0]["helm"]["values"])
    assert web_values == {"image": "ghcr.io/org/web:1.2.3", "replicas": 2}


def test_application_chart_source_uses_git_override():
    cfg = DeployTargetCfg(
        type="application",
        repo="deploys",
        pathTemplate="{env}/{stack}/{component}/app.yaml",
        workloadChart={
            "type": "git",
            "gitRepo": "https://github.com/acme/charts.git",
            "gitRef": "v1.0.0",
            "chartPath": "charts/custom",
        },
    )
    changes = ApplicationTarget(cfg, REPO, CLUSTERS).files_for(
        "demo", "dev", {n: resolved(n) for n in NAMES}, {n: record(n) for n in NAMES}
    )
    app = yaml.safe_load(next(c.content for c in changes if c.path.endswith("api/app.yaml")))
    chart = app["spec"]["sources"][0]
    assert chart["repoURL"] == "https://github.com/acme/charts.git"
    assert chart["targetRevision"] == "v1.0.0"
    assert chart["path"] == "charts/custom"


def test_destination_uses_the_env_cluster():
    app = yaml.safe_load(make_files("prod")[0].content)
    assert app["spec"]["destination"]["name"] == "prod-eks"


def test_unknown_env_raises():
    with pytest.raises(KeyError):
        make_files("staging")


def test_delete_and_bootstrap():
    target = ApplicationTarget(CFG, REPO, CLUSTERS)
    deletes = target.delete_files_for("demo", "dev", NAMES)
    assert sorted(c.path for c in deletes) == [f"dev/demo/{n}/app.yaml" for n in NAMES]
    assert all(c.content is None for c in deletes)
    assert set(target.bootstrap_manifests(make_config())) == {"argocd-notifications-cm.yaml"}


def test_index_rebuilds_from_application_files():
    class Git:
        def list_files(self, repo, prefix):
            return [(c.path, c.content) for c in make_files()]

    index = StackIndex(Git(), CFG, REPO)
    index.rebuild()
    stack = index.get("demo")
    assert stack is not None
    assert stack.components == {n: record(n) for n in NAMES}


def test_factory_selects_by_type():
    assert isinstance(make_deploy_target(CFG, REPO, CLUSTERS), ApplicationTarget)
    appset_cfg = DeployTargetCfg(
        type="applicationset", repo="deploys", pathTemplate=CFG.pathTemplate
    )
    assert isinstance(make_deploy_target(appset_cfg, REPO, CLUSTERS), ApplicationSetTarget)
