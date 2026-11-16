"""Deploy target abstraction: how component files and bootstrap manifests reach the GitOps repo."""

from typing import Protocol

from deployground.catalog import ResolvedComponent
from deployground.config.models import Config
from deployground.git.base import FileChange
from deployground.metadata import ComponentRecord


class DeployTarget(Protocol):
    def files_for(
        self,
        stack: str,
        env: str,
        components: dict[str, ResolvedComponent],
        records: dict[str, ComponentRecord],
    ) -> list[FileChange]:
        """File changes that create or update one stack's component files."""
        ...

    def delete_files_for(
        self, stack: str, env: str, component_names: list[str]
    ) -> list[FileChange]:
        """File changes (``content=None``) that remove the named components' files."""
        ...

    def bootstrap_manifests(self, config: Config) -> dict[str, str]:
        """Named YAML documents the operator applies once to wire the target up."""
        ...
