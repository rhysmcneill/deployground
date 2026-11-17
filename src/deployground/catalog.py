"""Stack catalog: lookup of configured stacks and per-component version resolution."""

from collections.abc import Callable

from deployground.config.models import Component, Config, StackDef


def resolve_ingress_host(host: str | None, ingress_class: str | None, stack: str) -> str | None:
    """Resolve a component's effective ingress host for one live deploy.

    Neither set: private, no Ingress. Only ingressClassName set: defaults to the bare
    stack name (collision-free, e.g. Tailscale's short label). ingressHost set: used as
    configured, with an optional {stack} placeholder for multi-deploy catalog stacks.
    """
    if host is None:
        return stack if ingress_class is not None else None
    return host.format(stack=stack)


class UnknownStack(KeyError):
    """Raised when a stack name is not present in the configuration."""

    def __init__(self, stack: str) -> None:
        self.stack = stack
        super().__init__(f"unknown stack {stack!r}")

    def __str__(self) -> str:
        return self.args[0]


class UnknownComponent(KeyError):
    """Raised when a version override names a component not in the stack."""

    def __init__(self, stack: str, component: str) -> None:
        self.stack = stack
        self.component = component
        super().__init__(f"unknown component {component!r} in stack {stack!r}")

    def __str__(self) -> str:
        return self.args[0]


class ResolvedComponent(Component):
    """A component with the concrete version chosen for this deploy."""

    version: str


class Catalog:
    def __init__(self, config_provider: Callable[[], Config]) -> None:
        self._config_provider = config_provider

    def list_stacks(self) -> list[str]:
        return sorted(self._config_provider().stacks)

    def get(self, stack: str) -> StackDef:
        stacks = self._config_provider().stacks
        if stack not in stacks:
            raise UnknownStack(stack)
        return stacks[stack]

    def resolve_versions(
        self, stack: str, overrides: dict[str, str]
    ) -> dict[str, ResolvedComponent]:
        stack_def = self.get(stack)
        for name in overrides:
            if name not in stack_def.components:
                raise UnknownComponent(stack, name)
        resolved: dict[str, ResolvedComponent] = {}
        for name, comp in stack_def.components.items():
            version = overrides.get(name, comp.defaultVersion)
            resolved[name] = ResolvedComponent(**comp.model_dump(), version=version)
        return resolved
