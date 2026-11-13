"""Lifecycle orchestration: deploy, extend and destroy stacks with guardrails.

Ties authz, the capability, git and the in-memory index together. Every operation opens
exactly ONE PR; the index is only touched after the PR was opened successfully.

Approval flow (``Capability.needs_approval``) is intentionally NOT wired here; Task 17
surfaces it before calling ``deploy``.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from deployground.authz.base import AuthzProvider
from deployground.capability.base import Capability, RenderContext, RenderInputs
from deployground.config.models import Environment, Lifecycle, RepoRef, parse_duration
from deployground.git.base import GitClient, PrResult
from deployground.index import StackIndex
from deployground.metadata import ComponentRecord, StackRecord

GLOBAL_DEFAULT_TTL = "24h"


class Denied(Exception):
    """A guardrail or permission refused the request. The message is user-presentable."""


@dataclass(frozen=True)
class DeployResult:
    pr_url: str
    pr_number: int
    merged: bool
    expires_at: datetime | None
    stack_record: StackRecord


class LifecycleService:
    """``clock`` returns an aware UTC datetime (injectable for tests)."""

    def __init__(
        self,
        authz: AuthzProvider,
        capability: Capability,
        git: GitClient,
        index: StackIndex,
        environments: dict[str, Environment],
        repos: dict[str, RepoRef],
        lifecycle_cfg: Lifecycle,
        render_ctx: RenderContext,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._authz = authz
        self._capability = capability
        self._git = git
        self._index = index
        self._environments = environments
        self._repos = repos
        self._lifecycle_cfg = lifecycle_cfg
        self._ctx = render_ctx
        self._clock = clock or (lambda: datetime.now(UTC))

    def deploy(
        self,
        user: str,
        owner_email: str,
        stack: str,
        env: str,
        overrides: dict[str, str],
        ttl: str | None,
        thread_ts: str,
        channel: str,
    ) -> DeployResult:
        if env not in self._environments or env not in self._authz.allowed_environments(user):
            raise Denied(f"You are not allowed to deploy to {env!r}.")
        env_cfg = self._environments[env]
        limit = self._lifecycle_cfg.maxLivePerUser
        if self._index.count_live_for(user) >= limit:
            raise Denied(f"You already have {limit} live stacks (the maximum). Destroy one first.")

        ttl_text, ttl_delta = self._resolve_ttl(user, ttl, env_cfg)
        now = self._clock()
        expires_at = None if ttl_delta is None else now + ttl_delta

        inputs = RenderInputs(
            stack=stack,
            env=env,
            overrides=overrides,
            ttl=ttl_text,
            owner=user,
            owner_email=owner_email,
            thread_ts=thread_ts,
            channel=channel,
            expires_at=expires_at,
        )
        changes = self._capability.render(inputs, self._ctx)
        pr = self._open_pr("deploy", stack, env, env_cfg, f"Deploy {stack} to {env}", changes)

        # Same construction as the capability: catalog resolution + inputs, not parsed files.
        resolved = self._ctx.catalog.resolve_versions(stack, overrides)
        record = StackRecord(
            name=stack,
            env=env,
            owner=user,
            expires_at=expires_at,
            thread_ts=thread_ts,
            channel=channel,
            components={
                name: ComponentRecord(
                    stack=stack,
                    component=name,
                    owner=user,
                    owner_email=owner_email,
                    env=env,
                    expires_at=expires_at,
                    version=comp.version,
                    thread_ts=thread_ts,
                    channel=channel,
                )
                for name, comp in resolved.items()
            },
        )
        self._index.upsert(record)
        return DeployResult(
            pr_url=pr.url,
            pr_number=pr.number,
            merged=pr.merged,
            expires_at=expires_at,
            stack_record=record,
        )

    def extend(self, user: str, stack: str, by: str) -> None:
        """Push expiry out by ``by`` measured from the CURRENT expiry (not from now)."""
        record = self._managed(user, stack)
        if record.expires_at is None:
            raise Denied(f"Stack {stack!r} has no expiry, so there is nothing to extend.")
        try:
            delta = parse_duration(by)
        except ValueError as exc:
            raise Denied(str(exc)) from exc
        if delta is None:
            raise Denied("Cannot extend by 'none'.")
        new_expiry = record.expires_at + delta

        components = {
            name: replace(comp, expires_at=new_expiry) for name, comp in record.components.items()
        }
        resolved = self._ctx.catalog.resolve_versions(
            stack, {name: comp.version for name, comp in components.items()}
        )
        # Only components still present in the catalog are regenerated.
        resolved = {name: resolved[name] for name in components if name in resolved}
        changes = self._ctx.deploy_target.files_for(stack, record.env, resolved, components)
        env_cfg = self._environments[record.env]
        self._open_pr(
            "extend", stack, record.env, env_cfg, f"Extend {stack} in {record.env} by {by}", changes
        )
        self._index.upsert(replace(record, expires_at=new_expiry, components=components))

    def destroy(self, user: str, stack: str) -> None:
        record = self._managed(user, stack)
        changes = self._capability.destroy(record, self._ctx)
        env_cfg = self._environments[record.env]
        self._open_pr(
            "destroy", stack, record.env, env_cfg, f"Destroy {stack} in {record.env}", changes
        )
        self._index.remove(stack)

    def _managed(self, user: str, stack: str) -> StackRecord:
        record = self._index.get(stack)
        if record is None:
            raise Denied(f"Stack {stack!r} was not found.")
        if not self._authz.can_manage(user, record.owner):
            raise Denied(f"You are not allowed to manage stack {stack!r}.")
        return record

    def _resolve_ttl(
        self, user: str, requested: str | None, env_cfg: Environment
    ) -> tuple[str, timedelta | None]:
        """User ttl, else env default, else global 24h. "none" from the USER is admin-only
        (an env default of "none" is operator config and allowed). A finite ttl is capped
        at env ``ttl.max`` (min, not an error); a max of "none" imposes no cap."""
        text = requested or env_cfg.ttl.default or GLOBAL_DEFAULT_TTL
        try:
            delta = parse_duration(text)
        except ValueError as exc:
            raise Denied(str(exc)) from exc
        if delta is None:
            if requested and not self._authz.is_admin(user):
                raise Denied("Only admins can deploy without an expiry (ttl 'none').")
            return text, None
        if env_cfg.ttl.max is not None:
            max_delta = parse_duration(env_cfg.ttl.max)
            if max_delta is not None and delta > max_delta:
                return env_cfg.ttl.max, max_delta
        return text, delta

    def _open_pr(
        self,
        action: str,
        stack: str,
        env: str,
        env_cfg: Environment,
        title: str,
        changes: list,
    ) -> PrResult:
        branch = f"deployground/{action}-{env}-{stack}-{uuid.uuid4().hex[:8]}"
        return self._git.open_pr(
            self._repos[env_cfg.repo],
            branch,
            title,
            changes,
            env_cfg.autoMerge,
        )
