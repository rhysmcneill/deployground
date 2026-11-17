"""Alternative deploy target: one native Argo `Application` manifest per component.

The Application carries the selector labels, the `deployground.io/*` metadata annotations
and the notification subscriptions directly, with the workload values inline under
`spec.source.helm.values`. `StackIndex` rebuilds from these manifests by parsing the
annotations back (see `metadata.parse_component_file`); no `deployground:` key is added
at the top level because that would make the manifest invalid.
"""

from typing import Any

import yaml

from deployground.catalog import ResolvedComponent
from deployground.config.models import Config, DeployTargetCfg, RepoRef
from deployground.deploytarget.notifications import notifications_cm, subscribe_annotations
from deployground.deploytarget.workloadchart import chart_source
from deployground.git.base import FileChange
from deployground.metadata import (
    LABEL_COMPONENT,
    LABEL_MANAGED,
    LABEL_STACK,
    ComponentRecord,
    to_annotations,
)

FINALIZER = "resources-finalizer.argocd.argoproj.io"


class ApplicationTarget:
    def __init__(
        self, cfg: DeployTargetCfg, repo_ref: RepoRef, env_clusters: dict[str, str]
    ) -> None:
        """`env_clusters` maps environment name -> its configured ArgoCD cluster name."""
        self._cfg = cfg
        self._repo_ref = repo_ref
        self._env_clusters = env_clusters

    def _path(self, stack: str, env: str, component: str) -> str:
        return self._cfg.pathTemplate.format(env=env, stack=stack, component=component)

    def files_for(
        self,
        stack: str,
        env: str,
        components: dict[str, ResolvedComponent],
        records: dict[str, ComponentRecord],
    ) -> list[FileChange]:
        cluster = self._env_clusters[env]
        return [
            FileChange(
                repo=self._repo_ref,
                path=self._path(stack, env, name),
                content=yaml.safe_dump(
                    self._application(stack, name, cluster, comp, records[name]), sort_keys=False
                ),
            )
            for name, comp in components.items()
        ]

    def delete_files_for(
        self, stack: str, env: str, component_names: list[str]
    ) -> list[FileChange]:
        return [
            FileChange(repo=self._repo_ref, path=self._path(stack, env, name), content=None)
            for name in component_names
        ]

    def bootstrap_manifests(self, config: Config) -> dict[str, str]:
        # No ApplicationSet: each Application is committed directly.
        cm = yaml.safe_dump(notifications_cm(config), sort_keys=False)
        return {"argocd-notifications-cm.yaml": cm}

    def _application(
        self,
        stack: str,
        component: str,
        cluster: str,
        comp: ResolvedComponent,
        record: ComponentRecord,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {"image": f"{comp.image}:{comp.version}"}
        if comp.port is not None:
            values["port"] = comp.port
        values["replicas"] = comp.replicas
        if comp.ingressHost is not None:
            values["ingressHost"] = comp.ingressHost
        if comp.env:
            values["env"] = dict(comp.env)
        if comp.secretRefs:
            values["secretRefs"] = list(comp.secretRefs)
        if comp.imagePullSecrets:
            values["imagePullSecrets"] = list(comp.imagePullSecrets)
        if comp.serviceAccountName:
            values["serviceAccountName"] = comp.serviceAccountName
        if comp.ingressClassName:
            values["ingressClassName"] = comp.ingressClassName
        # Literal manifest (no Go-template vars): the chart source is resolved once here
        # from config, same defaulting as the ApplicationSet target. Values are already
        # known in full, so they stay inline on the chart source's `helm.values` rather
        # than a second `ref: values` file source — there is no separate values file for
        # this target to point at (the component file *is* this whole Application CR).
        source = chart_source(self._cfg.workloadChart)
        source["helm"] = {"values": yaml.safe_dump(values, sort_keys=False)}
        return {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": f"{stack}-{component}",
                "namespace": "argocd",
                "labels": {
                    LABEL_STACK: stack,
                    LABEL_COMPONENT: component,
                    LABEL_MANAGED: "true",
                },
                "annotations": {**to_annotations(record), **subscribe_annotations()},
                "finalizers": [FINALIZER],
            },
            "spec": {
                "project": "default",
                "sources": [source],
                "destination": {"name": cluster, "namespace": stack},
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                    "syncOptions": ["CreateNamespace=true"],
                },
            },
        }
