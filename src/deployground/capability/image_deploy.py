"""The image-deploy capability: deploy a catalog stack's component images to an environment."""

from deployground import dotpath
from deployground.capability.base import GuardrailViolation, RenderContext, RenderInputs
from deployground.catalog import (
    ResolvedComponent,
    ResolvedCustomComponent,
    chart_pin,
    seed_chart_version_override,
)
from deployground.config.models import Environment, TtlPolicy
from deployground.git.base import FileChange
from deployground.metadata import ComponentRecord, StackRecord


def _in_registry(image: str, registry: str) -> bool:
    registry = registry.rstrip("/")
    return image == registry or image.startswith(registry + "/")


def _in_domain(host: str, domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    host = host.lower()
    return host == domain or host.endswith("." + domain)


class ImageDeployCapability:
    id = "image-deploy"
    title = "Deploy images"

    def render(self, inputs: RenderInputs, ctx: RenderContext) -> list[FileChange]:
        components = ctx.catalog.resolve_versions(inputs.catalog, inputs.overrides)
        self._check_guardrails(components, ctx)
        records = {
            name: ComponentRecord(
                catalog=inputs.catalog,
                stack=inputs.stack,
                component=name,
                owner=inputs.owner,
                owner_email=inputs.owner_email,
                env=inputs.env,
                expires_at=inputs.expires_at,
                version=(
                    comp.version if isinstance(comp, ResolvedComponent) else chart_pin(comp.chart)
                ),
                thread_ts=inputs.thread_ts,
                channel=inputs.channel,
                overrides=seed_chart_version_override(comp, inputs.overrides.get(name, {})),
            )
            for name, comp in components.items()
        }
        return ctx.deploy_target.files_for(inputs.stack, inputs.env, components, records)

    def destroy(self, stack: StackRecord, ctx: RenderContext) -> list[FileChange]:
        return ctx.deploy_target.delete_files_for(stack.name, stack.env, list(stack.components))

    def needs_approval(self, inputs: RenderInputs, env: Environment) -> bool:
        return env.requiresApproval

    def ttl_policy(self, env: str, ctx: RenderContext) -> TtlPolicy:
        return ctx.environments[env].ttl

    @staticmethod
    def _check_guardrails(
        components: dict[str, ResolvedComponent | ResolvedCustomComponent], ctx: RenderContext
    ) -> None:
        cfg = ctx.capability_cfg
        for name, comp in components.items():
            if isinstance(comp, ResolvedComponent):
                image = comp.image
                ingress_host = comp.ingressHost
            else:
                image = dotpath.get_path(comp.values, comp.imageRef.path)
                if not isinstance(image, str):
                    raise GuardrailViolation(
                        f"component {name!r} imageRef path {comp.imageRef.path!r} resolved to "
                        f"a non-string value {image!r}"
                    )
                ingress_host = (
                    dotpath.get_path(comp.values, comp.ingressRef.path)
                    if comp.ingressRef is not None
                    else None
                )
            if not any(_in_registry(image, r) for r in cfg.allowedRegistries):
                raise GuardrailViolation(
                    f"component {name!r} image {image!r} is not under any allowed "
                    f"registry {cfg.allowedRegistries!r}"
                )
            if ingress_host and not any(_in_domain(ingress_host, d) for d in cfg.ingressDomains):
                raise GuardrailViolation(
                    f"component {name!r} ingress host {ingress_host!r} is not under any "
                    f"allowed ingress domain {cfg.ingressDomains!r}"
                )
