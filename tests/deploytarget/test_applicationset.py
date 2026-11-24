from datetime import UTC, datetime

import yaml

import deployground
from deployground.catalog import ResolvedComponent, ResolvedCustomComponent
from deployground.config.models import (
    Config,
    DeployTargetCfg,
    FieldRef,
    RepoRef,
    TokenRef,
    WorkloadChartCfg,
)
from deployground.deploytarget.applicationset import ApplicationSetTarget
from deployground.deploytarget.workloadchart import chart_fields
from deployground.metadata import ComponentRecord, to_annotations, to_metadata_block

REPO = RepoRef(owner="acme", name="deploys", branch="main", authRef=TokenRef(tokenEnv="TOK"))
CFG = DeployTargetCfg(
    type="applicationset", repo="deploys", pathTemplate="{env}/{stack}/{component}/config.yaml"
)
EXPIRES = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
NAMES = ["api", "web", "worker"]
TRIGGERS = ("on-deployed", "on-health-degraded", "on-sync-failed")
SYSTEM_PATH = "dev/demo/system/config.yaml"


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
        catalog="demo",
        component=name,
        owner="U012ABC",
        owner_email="a@example.com",
        env="dev",
        expires_at=EXPIRES,
        version="1.2.3",
        thread_ts="1.1",
        channel="C1",
    )


def custom_resolved(name: str) -> ResolvedCustomComponent:
    return ResolvedCustomComponent(
        chart=WorkloadChartCfg(
            type="oci", repo="ghcr.io/acme", chart=f"{name}-chart", version="2.0.0"
        ),
        values={"replicas": 2, "image": {"repository": f"ghcr.io/acme/{name}", "tag": "v2"}},
        imageRef=FieldRef(path="image.tag"),
        ingressRef=None,
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
            "catalog": {},
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
    expected = sorted([f"dev/demo/{n}/config.yaml" for n in NAMES] + [SYSTEM_PATH])
    assert sorted(c.path for c in changes) == expected
    assert all(c.repo == REPO and c.content is not None for c in changes)


def test_files_for_also_writes_the_reserved_system_file():
    changes = make_files()
    assert len(changes) == len(NAMES) + 1
    system_changes = [c for c in changes if c.path == SYSTEM_PATH]
    assert len(system_changes) == 1
    data = yaml.safe_load(system_changes[0].content)
    assert data["namespaceOnly"] is True
    block = data["deployground"]
    assert block["component"] == "system"
    assert block["stack"] == "demo"
    other = yaml.safe_load(
        next(c.content for c in changes if c.path != SYSTEM_PATH)
    )["deployground"]
    assert block["owner"] == other["owner"]
    assert block["env"] == other["env"]
    # The reserved `system` file is matched by the same generator glob as every other
    # component file, so it needs the same `chart.*` keys filled in too, resolved from the
    # target's own default workloadChart (golden-path, in this test's CFG).
    assert block["chart"] == {
        "repoURL": "ghcr.io/rhysmcneill",
        "chart": "golden-path-chart",
        "targetRevision": deployground.__version__,
        "path": "",
    }


def test_files_for_golden_path_component_carries_its_own_chart_fields():
    content = yaml.safe_load(
        next(c.content for c in make_files() if c.path == "dev/demo/api/config.yaml")
    )
    assert content["deployground"]["chart"] == {
        "repoURL": "ghcr.io/rhysmcneill",
        "chart": "golden-path-chart",
        "targetRevision": deployground.__version__,
        "path": "",
    }


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
    assert block == {
        **to_metadata_block(record("api")),
        "chart": chart_fields(CFG.workloadChart),
    }
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


def test_files_defaults_ingress_host_to_stack_when_only_class_is_set():
    comp = resolved("api").model_copy(update={"ingressHost": None, "ingressClassName": "nginx"})
    changes = ApplicationSetTarget(CFG, REPO).files_for(
        "demo", "dev", {"api": comp}, {"api": record("api")}
    )
    data = yaml.safe_load(changes[0].content)
    assert data["ingressHost"] == "demo"
    assert data["ingressClassName"] == "nginx"


def test_delete_files_targets_all_components():
    changes = ApplicationSetTarget(CFG, REPO).delete_files_for("demo", "dev", NAMES)
    expected = sorted([f"dev/demo/{n}/config.yaml" for n in NAMES] + [SYSTEM_PATH])
    assert sorted(c.path for c in changes) == expected
    assert all(c.content is None and c.repo == REPO for c in changes)


def test_delete_files_also_removes_the_reserved_system_file():
    # "system" is never in component_names (StackRecord.components excludes it), but
    # delete_files_for must still emit its removal unconditionally.
    changes = ApplicationSetTarget(CFG, REPO).delete_files_for("demo", "dev", NAMES)
    assert len(changes) == len(NAMES) + 1
    system_changes = [c for c in changes if c.path == SYSTEM_PATH]
    assert len(system_changes) == 1
    assert system_changes[0].content is None
    assert system_changes[0].repo == REPO


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
    assert gen["repoURL"] == "git@github.com:acme/deploys.git"
    assert gen["revision"] == "main"
    assert gen["files"] == [{"path": "dev/*/*/config.yaml"}]

    tmpl = appset["spec"]["template"]
    assert tmpl["metadata"]["labels"] == {
        "deployground.io/stack": "{{deployground.stack}}",
        "deployground.io/component": "{{deployground.component}}",
        "deployground.io/managed": "true",
    }
    annotations = tmpl["metadata"]["annotations"]
    # "overrides" is a dict-valued annotation (JSON-encoded), not a templatable Go-template
    # ref like the rest -- the ApplicationSet template only forwards the scalar fields.
    for key in to_annotations(record("api")):
        if key == "deployground.io/overrides":
            continue
        assert key in annotations
    assert annotations["deployground.io/owner-email"] == "{{deployground.ownerEmail}}"
    assert annotations["deployground.io/expires-at"] == "{{deployground.expiresAt}}"
    assert annotations["deployground.io/thread-ts"] == "{{deployground.threadTs}}"
    for trigger in TRIGGERS:
        assert annotations[f"notifications.argoproj.io/subscribe.{trigger}.deployground"] == ""

    assert tmpl["spec"]["syncPolicy"]["automated"]["prune"] is True
    # No CreateNamespace/managedNamespaceMetadata: the shared template can't vary
    # syncPolicy per component, so namespace ownership stays with `system`'s own
    # chart-rendered namespace.yaml instead of racing every component for it.
    assert "syncOptions" not in tmpl["spec"]["syncPolicy"]
    assert "managedNamespaceMetadata" not in tmpl["spec"]["syncPolicy"]
    assert "resources-finalizer.argocd.argoproj.io" in tmpl["metadata"]["finalizers"]

    sources = tmpl["spec"]["sources"]
    assert len(sources) == 2
    chart, values_ref = sources
    assert chart["repoURL"] == "{{deployground.chart.repoURL}}"
    assert chart["chart"] == "{{deployground.chart.chart}}"
    assert chart["targetRevision"] == "{{deployground.chart.targetRevision}}"
    assert chart["path"] == "{{deployground.chart.path}}"
    assert chart["helm"]["valueFiles"] == ["$values/{{path}}/{{path.filename}}"]
    assert values_ref["repoURL"] == "git@github.com:acme/deploys.git"
    assert values_ref["targetRevision"] == "main"
    assert values_ref["ref"] == "values"


def test_bootstrap_applicationset_chart_source_uses_git_override():
    # The `workloadChart` config no longer bakes anything into the bootstrap template --
    # it only affects what's resolved per-file at write time (see test 1 in
    # test_files_for_*_writes_the_reserved_system_file / custom-chart-component tests).
    # This test now documents that a git-type override still yields the same four
    # placeholders at bootstrap time, regardless of the override's own values.
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
    assert chart["repoURL"] == "{{deployground.chart.repoURL}}"
    assert chart["chart"] == "{{deployground.chart.chart}}"
    assert chart["targetRevision"] == "{{deployground.chart.targetRevision}}"
    assert chart["path"] == "{{deployground.chart.path}}"


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
    # A pinned version, like the git override above, no longer affects the bootstrap
    # template -- it only shows up in a generated file's deployground.chart.targetRevision
    # at write time.
    assert chart["repoURL"] == "{{deployground.chart.repoURL}}"
    assert chart["chart"] == "{{deployground.chart.chart}}"
    assert chart["targetRevision"] == "{{deployground.chart.targetRevision}}"
    assert chart["path"] == "{{deployground.chart.path}}"

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


def test_files_for_custom_chart_component_writes_values_and_uses_its_own_chart():
    comp = custom_resolved("myservice")
    changes = ApplicationSetTarget(CFG, REPO).files_for(
        "demo", "dev", {"myservice": comp}, {"myservice": record("myservice")}
    )
    content = yaml.safe_load(next(c.content for c in changes if "myservice" in c.path))
    assert content["replicas"] == 2  # from comp.values, not a golden-path field
    assert content["image"] == {"repository": "ghcr.io/acme/myservice", "tag": "v2"}
    assert content["deployground"]["component"] == "myservice"
    assert content["deployground"]["chart"] == {
        "repoURL": "ghcr.io/acme",
        "chart": "myservice-chart",
        "targetRevision": "2.0.0",
        "path": "",
    }


def test_files_for_golden_path_and_custom_chart_components_coexist():
    components = {"api": resolved("api"), "myservice": custom_resolved("myservice")}
    records = {"api": record("api"), "myservice": record("myservice")}
    changes = ApplicationSetTarget(CFG, REPO).files_for("demo", "dev", components, records)
    by_path = {c.path: yaml.safe_load(c.content) for c in changes}
    api = by_path["dev/demo/api/config.yaml"]
    assert api["image"] == "ghcr.io/org/api:1.2.3"
    assert api["port"] == 8080
    myservice = by_path["dev/demo/myservice/config.yaml"]
    assert myservice["replicas"] == 2
    assert myservice["image"] == {"repository": "ghcr.io/acme/myservice", "tag": "v2"}
    assert myservice["deployground"]["component"] == "myservice"


def test_chart_fields_for_git_type_config_populates_three_keys_and_blanks_chart():
    cfg = WorkloadChartCfg(
        type="git",
        gitRepo="https://github.com/acme/charts.git",
        gitRef="v1.0.0",
        chartPath="charts/custom",
    )
    assert chart_fields(cfg) == {
        "repoURL": "https://github.com/acme/charts.git",
        "targetRevision": "v1.0.0",
        "path": "charts/custom",
        "chart": "",
    }


def test_bootstrap_applicationset_targets_cluster_by_server_when_cluster_is_a_url():
    # The common zero-setup case: no Argo CD Cluster registration needed for the local
    # cluster, unlike a registered cluster *name* (the other branch, above).
    cfg = make_config()
    cfg.environments["dev"].cluster = "https://kubernetes.default.svc"
    manifests = ApplicationSetTarget(CFG, REPO).bootstrap_manifests(cfg)
    dev = yaml.safe_load(manifests["applicationset-dev.yaml"])
    destination = dev["spec"]["template"]["spec"]["destination"]
    assert destination["server"] == "https://kubernetes.default.svc"
    assert "name" not in destination
