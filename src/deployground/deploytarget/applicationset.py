"""Default deploy target: one config file per component, picked up by an ApplicationSet.

Each component file holds the workload fields the golden-path chart consumes plus the
durable `deployground:` metadata block. An ArgoCD ApplicationSet with a Git file
generator turns every file into an Application, stamping the selector labels and
metadata annotations from the file's `deployground:` block.
"""

from typing import Any

import yaml

from deployground.catalog import ResolvedComponent
from deployground.config.models import Config, DeployTargetCfg, RepoRef
from deployground.deploytarget.notifications import notifications_cm, subscribe_annotations
from deployground.git.base import FileChange
from deployground.metadata import (
    ANNOTATION_PREFIX,
    BLOCK_KEY,
    LABEL_COMPONENT,
    LABEL_MANAGED,
    LABEL_STACK,
    ComponentRecord,
    to_metadata_block,
)

FINALIZER = "resources-finalizer.argocd.argoproj.io"

# annotation suffix -> key in the file's `deployground:` block
_ANNOTATION_FIELDS = {
    "stack": "stack",
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
        components: dict[str, ResolvedComponent],
        records: dict[str, ComponentRecord],
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        for name, comp in components.items():
            data: dict[str, Any] = {"image": f"{comp.image}:{comp.version}"}
            if comp.port is not None:
                data["port"] = comp.port
            data["replicas"] = comp.replicas
            if comp.ingressHost is not None:
                data["ingressHost"] = comp.ingressHost
            if comp.env:
                data["env"] = dict(comp.env)
            if comp.secretRefs:
                data["secretRefs"] = list(comp.secretRefs)
            data[BLOCK_KEY] = to_metadata_block(records[name])
            changes.append(
                FileChange(
                    repo=self._repo_ref,
                    path=self._path(stack, env, name),
                    content=yaml.safe_dump(data, sort_keys=False),
                )
            )
        return changes

    def delete_files_for(
        self, stack: str, env: str, component_names: list[str]
    ) -> list[FileChange]:
        return [
            FileChange(repo=self._repo_ref, path=self._path(stack, env, name), content=None)
            for name in component_names
        ]

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
        repo_url = f"https://github.com/{self._repo_ref.owner}/{self._repo_ref.name}.git"
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
                        "source": {
                            "repoURL": repo_url,
                            "targetRevision": self._repo_ref.branch,
                            "path": "golden-path",
                            "helm": {"valueFiles": ["../{{path.path}}/{{path.filename}}"]},
                        },
                        # The env's configured ArgoCD cluster; the stack is the namespace.
                        "destination": {"name": cluster, "namespace": _ref("stack")},
                        "syncPolicy": {
                            "automated": {"prune": True, "selfHeal": True},
                            "syncOptions": ["CreateNamespace=true"],
                        },
                    },
                },
            },
        }
