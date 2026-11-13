"""Typed configuration models and cross-reference validation.

Config files use camelCase keys, so field names here are camelCase on purpose.
Secrets never appear in config: only references to environment variables.
"""

import re
from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

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


class Component(_Model):
    image: str
    defaultVersion: str
    port: int | None = None
    replicas: int = 1
    ingressHost: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    secretRefs: list[str] = Field(default_factory=list)


class StackDef(_Model):
    components: dict[str, Component]


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


class StatusSourceCfg(_Model):
    type: Literal["argocd-notifications", "argocd-api"]
    webhookPath: str | None = None
    url: str | None = None
    tokenRef: TokenRef | None = None
    pollInterval: Duration | None = None


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
    stacks: dict[str, StackDef]
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
        for stack_name, stack in self.stacks.items():
            for comp_name, comp in stack.components.items():
                if not any(_in_registry(comp.image, r) for r in allowed):
                    errors.append(
                        f"stack {stack_name!r} component {comp_name!r} image {comp.image!r} "
                        f"is not under any allowed registry {allowed!r}"
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
