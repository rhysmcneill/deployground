"""Capability abstraction: a unit of stack functionality the bot can render, destroy and gate.

Step 1 deliberately omits ``form_schema()`` and ``status()`` from the protocol: with a
single capability the deploy form is built directly by the form engine, and status comes
from the separate pluggable ``StatusSource``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from deployground.catalog import Catalog
from deployground.config.models import CapabilityCfg, Environment, TtlPolicy
from deployground.deploytarget.base import DeployTarget
from deployground.git.base import FileChange
from deployground.metadata import StackRecord


class GuardrailViolation(ValueError):
    """Raised when a render would breach a capability guardrail (registry, ingress domain)."""


@dataclass(frozen=True)
class RenderInputs:
    """Validated form values for one deploy. ``expires_at`` is computed by the lifecycle
    service (which owns the clock) from ``ttl``; the capability only stamps it into records
    (``None`` means the stack never expires)."""

    catalog: str
    stack: str
    env: str
    overrides: dict[str, dict[str, str]]  # component name -> {path: value}
    ttl: str
    owner: str
    owner_email: str
    thread_ts: str
    channel: str
    expires_at: datetime | None


@dataclass(frozen=True)
class RenderContext:
    """Collaborators and config a capability needs. ``environments`` is the config's
    environment map (for approval and TTL policy lookup)."""

    catalog: Catalog
    deploy_target: DeployTarget
    capability_cfg: CapabilityCfg
    environments: dict[str, Environment]


class Capability(Protocol):
    id: str
    title: str

    def render(self, inputs: RenderInputs, ctx: RenderContext) -> list[FileChange]:
        """File changes that create or update the stack."""
        ...

    def destroy(self, stack: StackRecord, ctx: RenderContext) -> list[FileChange]:
        """File changes (``content=None``) that remove every component of ``stack``."""
        ...

    def needs_approval(self, inputs: RenderInputs, env: Environment) -> bool:
        """Whether a deploy into ``env`` (the environment's config) needs approval."""
        ...

    def ttl_policy(self, env: str, ctx: RenderContext) -> TtlPolicy:
        """The TTL policy (default/max) that applies to deploys into ``env``."""
        ...
