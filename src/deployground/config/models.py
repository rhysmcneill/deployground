"""Typed configuration models and cross-reference validation.

Config files use camelCase keys, so field names here are camelCase on purpose.
Secrets never appear in config: only references to environment variables.
"""

import re
from datetime import timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from deployground import dotpath
from deployground.metadata import RESERVED_COMPONENT

_DURATION_RE = re.compile(r"^(\d+)([smh])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours"}


def parse_duration(text: str) -> timedelta | None:
    """Parse "15s" / "1m" / "24h" into a timedelta. "none" means no expiry (None)."""
    if text == "none":
        return None
    match = _DURATION_RE.match(text)
    if not match:
        raise ValueError(f"invalid duration {text!r}: expected e.g. '15s', '1m', '24h' or 'none'")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


def _check_duration(text: str) -> str:
    parse_duration(text)
    return text


Duration = Annotated[str, AfterValidator(_check_duration)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenRef(_Model):
    """Reference to an environment variable holding a token. Never the secret itself."""

    tokenEnv: str


class RepoRef(_Model):
    owner: str
    name: str
    branch: str
    authRef: TokenRef


class TtlPolicy(_Model):
    default: Duration | None = None
    max: Duration | None = None


class Environment(_Model):
    cluster: str
    repo: str
    autoMerge: bool
    ttl: TtlPolicy
    requiresApproval: bool


class WorkloadChartCfg(_Model):
    """Source of the workload chart that renders each component's manifests.

    Fully optional: a `deployTarget` with no `workloadChart` key still validates and gets
    the default OCI chart (Deployground's own published `golden-path-chart`), pinned to the
    running bot's own version at render time when `version` is unset — keeping the bot and
    its default chart in lockstep across releases.
    """

    type: Literal["oci", "git"] = "oci"
    # oci
    repo: str | None = "ghcr.io/rhysmcneill"  # registry/org; chart name is appended
    chart: str | None = "golden-path-chart"
    version: str | None = None  # targetRevision; None -> deployground.__version__ at render time
    # git (mirrors the existing colocated-in-target-repo behavior, for operators who want
    # their own chart via git instead of OCI)
    gitRepo: str | None = None  # full git URL
    gitRef: str | None = None
    chartPath: str | None = None

    @model_validator(mode="after")
    def _git_requires_repo_and_path(self) -> Self:
        if self.type == "git" and not (self.gitRepo and self.chartPath):
            raise ValueError(
                "workloadChart type 'git' requires both 'gitRepo' and 'chartPath' to be set"
            )
        return self


class FieldRef(_Model):
    """Pointer to a value inside a custom-chart component's `values`, by dot-path."""

    path: str


class OverridableField(_Model):
    """A `values` field a custom-chart component exposes for per-deploy override."""

    path: str
    label: str


class Component(_Model):
    # Golden-path fields. Required for golden-path components; left unset (None) for
    # custom-chart components, which use `imageRef`/`ingressRef` into `values` instead.
    image: str | None = None
    defaultVersion: str | None = None
    port: int | None = None
    replicas: int = 1
    ingressHost: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    secretRefs: list[str] = Field(default_factory=list)
    # Names of pre-existing `kubernetes.io/dockerconfigjson` Secrets (operator-managed, e.g.
    # via `kubectl create secret docker-registry`), wired onto the pod spec so kubelet can
    # pull images from a private registry. This is the ONLY one of the two fields below that
    # affects image pulls.
    imagePullSecrets: list[str] = Field(default_factory=list)
    # Name of a pre-existing ServiceAccount (operator-managed, e.g. via Terraform/IRSA
    # tooling) the pod runs under, e.g. for IRSA access to AWS APIs at runtime. This does
    # NOT help pull private images: kubelet pulls the image before the pod (and its
    # ServiceAccount) exists, so this is a pod-runtime mechanism only, not a pull mechanism.
    serviceAccountName: str | None = None
    # Ingress class for the component's Ingress (e.g. "tailscale", "nginx"); None uses the
    # cluster's default IngressClass. Purely a routing choice — unrelated to the two fields
    # above.
    ingressClassName: str | None = None

    # Custom-chart fields. `chart` selects an arbitrary Helm chart (instead of the built-in
    # golden-path chart) and `values` supplies its Helm values. `imageRef`/`ingressRef` point,
    # by dot-path into `values`, at the fields that would otherwise be the golden-path's typed
    # `image`/`ingressHost` equivalents. `overridable` lists `values` fields exposed for
    # per-deploy override (e.g. in Slack forms).
    chart: WorkloadChartCfg | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    imageRef: FieldRef | None = None
    ingressRef: FieldRef | None = None
    overridable: list[OverridableField] = Field(default_factory=list)

    @property
    def is_custom_chart(self) -> bool:
        return self.chart is not None

    @model_validator(mode="after")
    def _golden_path_xor_chart(self) -> Self:
        if self.chart is not None and self.image is not None:
            raise ValueError("component cannot set both 'chart' and golden-path 'image'")
        if self.chart is None and self.image is None:
            raise ValueError("component must set either 'image' (golden-path) or 'chart'")
        if self.chart is not None and self.imageRef is None:
            raise ValueError("imageRef is required when chart is set")
        if self.chart is None and self.defaultVersion is None:
            raise ValueError("defaultVersion is required for golden-path components")
        if self.chart is not None and self.chart.type == "oci":
            missing = {"repo", "chart", "version"} - self.chart.model_fields_set
            if missing:
                raise ValueError(
                    f"custom chart requires explicit {sorted(missing)} (oci charts can't rely "
                    "on the golden-path chart's own defaults)"
                )
        return self


class CatalogEntry(_Model):
    components: dict[str, Component]

    @model_validator(mode="after")
    def _no_reserved_component_name(self) -> Self:
        if RESERVED_COMPONENT in self.components:
            raise ValueError(f"component name {RESERVED_COMPONENT!r} is reserved")
        return self


class CapabilityCfg(_Model):
    enabled: bool = True
    allowedRegistries: list[str] = Field(default_factory=list)
    ingressDomains: list[str] = Field(default_factory=list)


class AccessRule(_Model):
    user: str | None = None
    group: str | None = None
    channel: str | None = None
    environments: list[str] = Field(default_factory=lambda: ["*"])
    admin: bool = False

    @model_validator(mode="after")
    def _exactly_one_principal(self) -> Self:
        principals = [p for p in (self.user, self.group, self.channel) if p is not None]
        if len(principals) != 1:
            raise ValueError("access rule needs exactly one of user, group, channel")
        return self


class Access(_Model):
    rules: list[AccessRule] = Field(default_factory=list)


class DeployTargetCfg(_Model):
    type: Literal["applicationset", "application"]
    repo: str
    pathTemplate: str
    workloadChart: WorkloadChartCfg = Field(default_factory=WorkloadChartCfg)


class StatusSourceCfg(_Model):
    type: Literal["argocd-notifications", "argocd-api"]
    webhookPath: str | None = None
    # Externally-reachable base URL of the bot's status webhook (scheme + host [+ port], NO
    # path), e.g. the in-cluster Service DNS `http://deployground.deployground.svc.cluster.local`,
    # or an ingress URL when Argo runs in another cluster. `webhookPath` is appended to it.
    # Defaults to the in-cluster Service DNS for the default release name/namespace.
    webhookUrl: str | None = None
    url: str | None = None
    tokenRef: TokenRef | None = None
    pollInterval: Duration | None = None
    # Env var holding the shared secret the notifications webhook must present.
    secretRef: TokenRef | None = None


class Lifecycle(_Model):
    warnBefore: Duration = "1h"
    reaperInterval: Duration = "1m"
    maxLivePerUser: int = 10


class GitCfg(_Model):
    provider: Literal["github"]
    repos: dict[str, RepoRef]


class Config(_Model):
    git: GitCfg
    environments: dict[str, Environment]
    catalog: dict[str, CatalogEntry]
    capabilities: dict[str, CapabilityCfg] = Field(default_factory=dict)
    access: Access = Field(default_factory=Access)
    deployTarget: DeployTargetCfg
    statusSource: StatusSourceCfg
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)

    def validate_refs(self) -> None:
        """Cross-check references between sections; raise ValueError naming each problem."""
        errors: list[str] = []

        for env_name, env in self.environments.items():
            if env.repo not in self.git.repos:
                errors.append(f"environment {env_name!r} references unknown repo {env.repo!r}")

        if self.deployTarget.repo not in self.git.repos:
            errors.append(f"deployTarget references unknown repo {self.deployTarget.repo!r}")

        allowed = [
            registry
            for cap in self.capabilities.values()
            if cap.enabled
            for registry in cap.allowedRegistries
        ]
        for catalog_name, entry in self.catalog.items():
            for comp_name, comp in entry.components.items():
                if comp.is_custom_chart:
                    try:
                        image = dotpath.get_path(comp.values, comp.imageRef.path)
                    except KeyError:
                        errors.append(
                            f"catalog entry {catalog_name!r} component {comp_name!r}: "
                            f"imageRef path {comp.imageRef.path!r} not found in values"
                        )
                        continue
                else:
                    image = comp.image
                if not any(_in_registry(image, r) for r in allowed):
                    errors.append(
                        f"catalog entry {catalog_name!r} component {comp_name!r} "
                        f"image {image!r} is not under any allowed registry {allowed!r}"
                    )

        for i, rule in enumerate(self.access.rules):
            for env_name in rule.environments:
                if env_name != "*" and env_name not in self.environments:
                    errors.append(f"access rule {i} references unknown environment {env_name!r}")

        if errors:
            raise ValueError("; ".join(errors))


def _in_registry(image: str, registry: str) -> bool:
    registry = registry.rstrip("/")
    return image == registry or image.startswith(registry + "/")
