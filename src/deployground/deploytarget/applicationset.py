"""Default deploy target: one config file per component, picked up by an ApplicationSet.

Each component file holds the workload fields the golden-path chart consumes plus the
durable `deployground:` metadata block. An ArgoCD ApplicationSet with a Git file
generator turns every file into an Application, stamping the selector labels and
metadata annotations from the file's `deployground:` block.
"""

from dataclasses import replace
from typing import Any

import yaml

from deployground.catalog import ResolvedComponent, ResolvedCustomComponent, resolve_ingress_host
from deployground.config.models import Config, DeployTargetCfg, RepoRef
from deployground.deploytarget.notifications import notifications_cm, subscribe_annotations
from deployground.deploytarget.workloadchart import chart_fields
from deployground.git.base import FileChange
from deployground.metadata import (
    ANNOTATION_PREFIX,
    BLOCK_KEY,
    LABEL_COMPONENT,
    LABEL_MANAGED,
    LABEL_STACK,
    RESERVED_COMPONENT,
    ComponentRecord,
    to_metadata_block,
)

FINALIZER = "resources-finalizer.argocd.argoproj.io"

# annotation suffix -> key in the file's `deployground:` block
_ANNOTATION_FIELDS = {
    "stack": "stack",
    "catalog": "catalog",
    "component": "component",
    "owner": "owner",
    "owner-email": "ownerEmail",
    "env": "env",
    "expires-at": "expiresAt",
    "version": "version",
    "thread-ts": "threadTs",
    "channel": "channel",
}


def _ref(key: str) -> str:
    """Generator placeholder for a key of the component file's `deployground:` block."""
    return "{{" + f"{BLOCK_KEY}.{key}" + "}}"


def _is_url(cluster: str) -> bool:
    return cluster.startswith(("http://", "https://"))


class ApplicationSetTarget:
    def __init__(self, cfg: DeployTargetCfg, repo_ref: RepoRef) -> None:
        self._cfg = cfg
        self._repo_ref = repo_ref

    def _path(self, stack: str, env: str, component: str) -> str:
        return self._cfg.pathTemplate.format(env=env, stack=stack, component=component)

    def files_for(
        self,
        stack: str,
        env: str,
        components: dict[str, ResolvedComponent | ResolvedCustomComponent],
        records: dict[str, ComponentRecord],
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        for name, comp in components.items():
            if isinstance(comp, ResolvedComponent):
                data: dict[str, Any] = {"image": f"{comp.image}:{comp.version}"}
                if comp.port is not None:
                    data["port"] = comp.port
                data["replicas"] = comp.replicas
                ingress_host = resolve_ingress_host(
                    comp.ingressHost, comp.ingressClassName, stack
                )
                if ingress_host is not None:
                    data["ingressHost"] = ingress_host
                if comp.env:
                    data["env"] = dict(comp.env)
                if comp.secretRefs:
                    data["secretRefs"] = list(comp.secretRefs)
                if comp.imagePullSecrets:
                    data["imagePullSecrets"] = list(comp.imagePullSecrets)
                if comp.serviceAccountName:
                    data["serviceAccountName"] = comp.serviceAccountName
                if comp.ingressClassName:
                    data["ingressClassName"] = comp.ingressClassName
            else:
                data = dict(comp.values)
            block = to_metadata_block(records[name])
            if isinstance(comp, ResolvedComponent):
                block["chart"] = chart_fields(self._cfg.workloadChart)
            else:
                block["chart"] = chart_fields(comp.chart)
            data[BLOCK_KEY] = block
            changes.append(
                FileChange(
                    repo=self._repo_ref,
                    path=self._path(stack, env, name),
                    content=yaml.safe_dump(data, sort_keys=False),
                )
            )
        if records:
            base = next(iter(records.values()))
            block = to_metadata_block(
                replace(base, component=RESERVED_COMPONENT, version="", overrides={})
            )
            block["chart"] = chart_fields(self._cfg.workloadChart)
            changes.append(
                FileChange(
                    repo=self._repo_ref,
                    path=self._path(stack, env, RESERVED_COMPONENT),
                    content=yaml.safe_dump(
                        {"namespaceOnly": True, BLOCK_KEY: block}, sort_keys=False
                    ),
                )
            )
        return changes

    def delete_files_for(
        self, stack: str, env: str, component_names: list[str]
    ) -> list[FileChange]:
        changes = [
            FileChange(repo=self._repo_ref, path=self._path(stack, env, name), content=None)
            for name in component_names
        ]
        changes.append(
            FileChange(
                repo=self._repo_ref,
                path=self._path(stack, env, RESERVED_COMPONENT),
                content=None,
            )
        )
        return changes

    def bootstrap_manifests(self, config: Config) -> dict[str, str]:
        # One ApplicationSet per environment: each targets that env's cluster and only
        # watches that env's subtree of the owned path.
        manifests = {
            f"applicationset-{env_name}.yaml": yaml.safe_dump(
                self._applicationset(env_name, env.cluster), sort_keys=False
            )
            for env_name, env in config.environments.items()
        }
        manifests["argocd-notifications-cm.yaml"] = yaml.safe_dump(
            notifications_cm(config), sort_keys=False
        )
        return manifests

    def _applicationset(self, env_name: str, cluster: str) -> dict[str, Any]:
        repo_url = f"git@github.com:{self._repo_ref.owner}/{self._repo_ref.name}.git"
        # `{env}/{stack}/{component}/config.yaml` -> `dev/*/*/config.yaml`
        glob = self._path("*", env_name, "*")

        labels = {
            LABEL_STACK: _ref("stack"),
            LABEL_COMPONENT: _ref("component"),
            LABEL_MANAGED: "true",
        }
        annotations = {
            f"{ANNOTATION_PREFIX}{suffix}": _ref(key) for suffix, key in _ANNOTATION_FIELDS.items()
        }
        annotations.update(subscribe_annotations())

        # The git "files" generator exposes the matched file's directory as {{path}} (not
        # {{path.path}} -- that isn't a real generator variable here, so it was left as a
        # literal, unsubstituted string and helm then failed to find the literal path
        # "{{path.path}}/config.yaml").
        source = {
            "repoURL": _ref("chart.repoURL"),
            "chart": _ref("chart.chart"),
            "path": _ref("chart.path"),
            "targetRevision": _ref("chart.targetRevision"),
            "helm": {"valueFiles": ["$values/{{path}}/{{path.filename}}"]},
        }
        values_source = {
            "repoURL": repo_url,
            "targetRevision": self._repo_ref.branch,
            "ref": "values",
        }

        return {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {"name": f"deployground-{env_name}", "namespace": "argocd"},
            "spec": {
                "generators": [
                    {
                        "git": {
                            "repoURL": repo_url,
                            "revision": self._repo_ref.branch,
                            "files": [{"path": glob}],
                        }
                    }
                ],
                "template": {
                    "metadata": {
                        "name": f"{_ref('stack')}-{_ref('component')}",
                        "labels": labels,
                        "annotations": annotations,
                        "finalizers": [FINALIZER],
                    },
                    "spec": {
                        "project": "default",
                        "sources": [source, values_source],
                        # The env's configured ArgoCD destination; the stack is the namespace.
                        # A URL targets the cluster directly (zero setup: the well-known
                        # https://kubernetes.default.svc needs no Argo CD Cluster
                        # registration), so most single-cluster adopters never need to
                        # register anything. Anything else is looked up as a registered
                        # cluster *name*, for teams that manage several clusters from one
                        # Argo CD.
                        "destination": {
                            ("server" if _is_url(cluster) else "name"): cluster,
                            "namespace": _ref("stack"),
                        },
                        # No CreateNamespace/managedNamespaceMetadata here: this template is
                        # shared by every component's generated Application, including the
                        # reserved `system` one. The git-files generator can't vary syncPolicy
                        # per matched file, so giving every component a namespace-creation
                        # option would mean several Applications race to create/label the same
                        # namespace -- one sync's render can decide it no longer "owns" the
                        # namespace and prune it out from under the running stack. The
                        # `system` component's golden-path-chart renders the namespace
                        # explicitly (see its `namespaceOnly` values and namespace.yaml
                        # template) and is the sole owner of the stack's namespace lifecycle;
                        # other components simply sync into a namespace `system` already
                        # created, retrying via selfHeal if their first sync lands first.
                        "syncPolicy": {"automated": {"prune": True, "selfHeal": True}},
                    },
                },
            },
        }
