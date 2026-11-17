"""GitHub implementation of GitClient, built on ghapi.

Scope for step 1: no retries and no rate-limit handling. Any API error propagates
to the caller. The token is read from the environment variable named by
``repo.authRef.tokenEnv`` at call time and is never stored or logged.
"""

import base64
import os
import re
import secrets
from collections.abc import Mapping
from typing import Any

from fasttransport.errors import APIError
from ghapi.all import GhApi

from deployground.config.models import RepoRef
from deployground.git.base import FileChange, PrResult


def branch_name(title: str) -> str:
    """Build a PR branch name: ``deployground/<slug>-<6 hex chars>``.

    The slug is the title lowercased with runs of non-alphanumerics collapsed to
    single dashes (max 40 chars); the random suffix avoids collisions when the
    same title is used twice. Callers pass the result as ``open_pr(branch=...)``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "change"
    return f"deployground/{slug}-{secrets.token_hex(3)}"


class GitHubClient:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    def _api(self, repo: RepoRef) -> Any:
        """Network seam: tests replace this to return a fake ghapi-shaped object."""
        token = self._env.get(repo.authRef.tokenEnv)
        if not token:
            raise RuntimeError(f"environment variable {repo.authRef.tokenEnv} is not set")
        # sync=True: GhApi defaults to an async client (ops return awaitables); this
        # client is used from plain synchronous code throughout.
        return GhApi(owner=repo.owner, repo=repo.name, token=token, sync=True)

    def open_pr(
        self,
        repo: RepoRef,
        branch: str,
        title: str,
        changes: list[FileChange],
        auto_merge: bool,
    ) -> PrResult:
        api = self._api(repo)
        base_sha = api.git.get_ref(ref=f"heads/{repo.branch}").object.sha
        api.git.create_ref(ref=f"refs/heads/{branch}", sha=base_sha)

        for change in changes:
            existing = self._existing_sha(api, change.path, branch)
            if change.content is None:
                if existing is None:
                    continue  # already absent; nothing to delete
                api.repos.delete_file(
                    path=change.path,
                    message=f"Delete {change.path}",
                    sha=existing,
                    branch=branch,
                )
            else:
                kwargs: dict[str, Any] = {}
                if existing is not None:
                    kwargs["sha"] = existing
                api.repos.create_or_update_file_contents(
                    path=change.path,
                    message=f"Update {change.path}",
                    content=base64.b64encode(change.content.encode()).decode(),
                    branch=branch,
                    **kwargs,
                )

        pr = api.pulls.create(head=branch, base=repo.branch, title=title)
        merged = False
        if auto_merge:
            api.pulls.merge(pull_number=pr.number)
            merged = True
        return PrResult(url=pr.html_url, number=pr.number, merged=merged)

    def list_files(self, repo: RepoRef, path_prefix: str) -> list[tuple[str, str]]:
        api = self._api(repo)
        tree = api.git.get_tree(tree_sha=repo.branch, recursive="1")
        truncated = (
            tree.get("truncated") if isinstance(tree, dict) else getattr(tree, "truncated", False)
        )
        if truncated:
            # A partial listing would silently drop live stacks from the index (and the reaper).
            raise RuntimeError(
                f"git tree listing truncated for {repo.owner}/{repo.name} under "
                f"{path_prefix!r}: too many files to list in one request"
            )
        result: list[tuple[str, str]] = []
        for entry in tree.tree:
            if entry.type != "blob" or not entry.path.startswith(path_prefix):
                continue
            blob = api.git.get_blob(file_sha=entry.sha)
            result.append((entry.path, base64.b64decode(blob.content).decode()))
        return result

    @staticmethod
    def _existing_sha(api: Any, path: str, ref: str) -> str | None:
        try:
            return api.repos.get_content(path=path, ref=ref).sha
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise
