"""Stack catalog: lookup of configured stacks and per-component version resolution."""

from collections.abc import Callable

from deployground.config.models import Component, Config, StackDef


def resolve_ingress_host(host: str | None, stack: str) -> str | None:
    """Substitute a literal "{stack}" placeholder in a component's configured
    ``ingressHost`` with the live deploy's own stack name.

    A catalog stack is a reusable template; without this, every deploy of it would get
    the exact same literal ingress host from config, colliding the moment a second
    instance goes live. ``{stack}`` lets operators key the host off the name the deployer
    picked instead -- e.g. ``"{stack}"`` alone for Tailscale's short MagicDNS label, or
    ``"{stack}.apps.example.com"`` for a real DNS ingress.
    """
    return host.format(stack=stack) if host is not None else None


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
