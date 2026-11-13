"""The image-deploy capability: deploy a catalog stack's component images to an environment."""

from deployground.capability.base import GuardrailViolation, RenderContext, RenderInputs
from deployground.catalog import ResolvedComponent
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
        components = ctx.catalog.resolve_versions(inputs.stack, inputs.overrides)
        self._check_guardrails(components, ctx)
        records = {
            name: ComponentRecord(
                stack=inputs.stack,
                component=name,
                owner=inputs.owner,
                owner_email=inputs.owner_email,
                env=inputs.env,
                expires_at=inputs.expires_at,
                version=comp.version,
                thread_ts=inputs.thread_ts,
                channel=inputs.channel,
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
    def _check_guardrails(components: dict[str, ResolvedComponent], ctx: RenderContext) -> None:
        cfg = ctx.capability_cfg
        for name, comp in components.items():
            if not any(_in_registry(comp.image, r) for r in cfg.allowedRegistries):
                raise GuardrailViolation(
                    f"component {name!r} image {comp.image!r} is not under any allowed "
                    f"registry {cfg.allowedRegistries!r}"
                )
            if comp.ingressHost and not any(
                _in_domain(comp.ingressHost, d) for d in cfg.ingressDomains
            ):
                raise GuardrailViolation(
                    f"component {name!r} ingress host {comp.ingressHost!r} is not under any "
                    f"allowed ingress domain {cfg.ingressDomains!r}"
                )
