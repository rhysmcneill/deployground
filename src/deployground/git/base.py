"""Git hosting abstraction: the narrow surface the rest of Deployground needs."""

from dataclasses import dataclass
from typing import Protocol

from deployground.config.models import RepoRef


@dataclass(frozen=True)
class FileChange:
    """One file change. ``content=None`` means delete the file."""

    repo: RepoRef
    path: str
    content: str | None


@dataclass(frozen=True)
class PrResult:
    url: str
    number: int
    merged: bool


class GitClient(Protocol):
    """Synchronous git client. Opening a PR is a user-blocking action, so no async."""

    def open_pr(
        self,
        repo: RepoRef,
        branch: str,
        title: str,
        changes: list[FileChange],
        auto_merge: bool,
    ) -> PrResult:
        """Apply all changes on a new ``branch`` cut from the repo's base branch and open ONE PR."""
        ...

    def list_files(self, repo: RepoRef, path_prefix: str) -> list[tuple[str, str]]:
        """Return (path, content) for every file under ``path_prefix`` on the base branch."""
        ...
