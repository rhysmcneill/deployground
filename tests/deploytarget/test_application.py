import pytest
import yaml

import deployground
from deployground.config.models import DeployTargetCfg
from deployground.deploytarget import make_deploy_target
from deployground.deploytarget.application import ApplicationTarget
from deployground.deploytarget.applicationset import ApplicationSetTarget
from deployground.deploytarget.workloadchart import chart_source
from deployground.index import StackIndex
from deployground.metadata import to_annotations
from tests.deploytarget.test_applicationset import (
    NAMES,
    REPO,
    TRIGGERS,
    custom_resolved,
    make_config,
    record,
    resolved,
)

CFG = DeployTargetCfg(
    type="application", repo="deploys", pathTemplate="{env}/{stack}/{component}/app.yaml"
)
CLUSTERS = {"dev": "dev-cluster", "prod": "prod-eks"}
SYSTEM_PATH = "dev/demo/system/app.yaml"


def make_files(env="dev"):
    return ApplicationTarget(CFG, REPO, CLUSTERS).files_for(
        "demo", env, {n: resolved(n) for n in NAMES}, {n: record(n) for n in NAMES}
    )


def test_application_cr_per_component_has_labels_metadata_and_subscription_annotations():
    changes = make_files()
    expected = sorted([f"dev/demo/{n}/app.yaml" for n in NAMES] + [SYSTEM_PATH])
    assert sorted(c.path for c in changes) == expected
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
    assert spec["syncPolicy"]["managedNamespaceMetadata"]["labels"] == {
        "deployground.io/managed": "true"
    }
    assert spec["destination"] == {"name": "dev-cluster", "namespace": "demo"}
    sources = spec["sources"]
    assert len(sources) == 1
    chart = sources[0]
    assert chart["repoURL"] == "ghcr.io/rhysmcneill"
    assert chart["chart"] == "golden-path-chart"
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


def test_application_defaults_ingress_host_to_stack_when_only_class_is_set():
    comp = resolved("api").model_copy(update={"ingressHost": None, "ingressClassName": "nginx"})
    changes = ApplicationTarget(CFG, REPO, CLUSTERS).files_for(
        "demo", "dev", {"api": comp}, {"api": record("api")}
    )
    app = yaml.safe_load(changes[0].content)
    values = yaml.safe_load(app["spec"]["sources"][0]["helm"]["values"])
    assert values["ingressHost"] == "demo"
    assert values["ingressClassName"] == "nginx"


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
    expected = sorted([f"dev/demo/{n}/app.yaml" for n in NAMES] + [SYSTEM_PATH])
    assert sorted(c.path for c in deletes) == expected
    assert all(c.content is None for c in deletes)
    assert set(target.bootstrap_manifests(make_config())) == {"argocd-notifications-cm.yaml"}


def test_files_for_also_writes_the_reserved_system_application():
    changes = make_files()
    system_changes = [c for c in changes if c.path == SYSTEM_PATH]
    assert len(system_changes) == 1
    app = yaml.safe_load(system_changes[0].content)
    assert app["kind"] == "Application"
    assert app["metadata"]["name"] == "demo-system"
    values = yaml.safe_load(app["spec"]["sources"][0]["helm"]["values"])
    assert values == {"namespaceOnly": True}
    annotations = app["metadata"]["annotations"]
    assert annotations["deployground.io/component"] == "system"
    assert annotations["deployground.io/stack"] == "demo"
    assert annotations["deployground.io/owner"] == record("api").owner
    assert annotations["deployground.io/version"] == ""


def test_delete_files_also_removes_the_reserved_system_application():
    # "system" is never in component_names (StackRecord.components excludes it), but
    # delete_files_for must still emit its removal unconditionally.
    changes = ApplicationTarget(CFG, REPO, CLUSTERS).delete_files_for("demo", "dev", NAMES)
    system_changes = [c for c in changes if c.path == SYSTEM_PATH]
    assert len(system_changes) == 1
    assert system_changes[0].content is None
    assert system_changes[0].repo == REPO


def test_index_rebuilds_from_application_files():
    class Git:
        def list_files(self, repo, prefix):
            return [(c.path, c.content) for c in make_files()]

    index = StackIndex(Git(), CFG, REPO)
    index.rebuild()
    stack = index.get("demo")
    assert stack is not None
    assert stack.components == {n: record(n) for n in NAMES}


def test_application_custom_chart_component_uses_its_own_chart_and_values():
    comp = custom_resolved("myservice")
    changes = ApplicationTarget(CFG, REPO, CLUSTERS).files_for(
        "demo", "dev", {"myservice": comp}, {"myservice": record("myservice")}
    )
    app = yaml.safe_load(next(c.content for c in changes if "myservice" in c.path))
    source = app["spec"]["sources"][0]
    expected_source = chart_source(comp.chart)
    for key, value in expected_source.items():
        assert source[key] == value
    values = yaml.safe_load(source["helm"]["values"])
    assert values == comp.values


def test_application_golden_path_and_custom_chart_components_coexist():
    components = {"api": resolved("api"), "myservice": custom_resolved("myservice")}
    records = {"api": record("api"), "myservice": record("myservice")}
    changes = ApplicationTarget(CFG, REPO, CLUSTERS).files_for("demo", "dev", components, records)
    by_path = {c.path: yaml.safe_load(c.content) for c in changes}
    api = by_path["dev/demo/api/app.yaml"]
    api_values = yaml.safe_load(api["spec"]["sources"][0]["helm"]["values"])
    assert api_values["image"] == "ghcr.io/org/api:1.2.3"
    myservice = by_path["dev/demo/myservice/app.yaml"]
    myservice_source = myservice["spec"]["sources"][0]
    assert myservice_source["chart"] == components["myservice"].chart.chart
    myservice_values = yaml.safe_load(myservice_source["helm"]["values"])
    assert myservice_values == components["myservice"].values


def test_factory_selects_by_type():
    assert isinstance(make_deploy_target(CFG, REPO, CLUSTERS), ApplicationTarget)
    appset_cfg = DeployTargetCfg(
        type="applicationset", repo="deploys", pathTemplate=CFG.pathTemplate
    )
    assert isinstance(make_deploy_target(appset_cfg, REPO, CLUSTERS), ApplicationSetTarget)
