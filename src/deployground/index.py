"""In-memory stack index, rebuilt from Git.

Git is the durable source of truth; this is a cache the bot keeps current with
`upsert`/`remove` after its own deploy/destroy PRs. All access is guarded by a lock
because the reaper thread and request handlers touch it concurrently.
"""

import logging
import threading
from datetime import datetime

import yaml

from deployground.config.models import DeployTargetCfg, RepoRef
from deployground.git.base import GitClient
from deployground.metadata import InvalidMetadata, StackRecord, parse_component_file

logger = logging.getLogger(__name__)


def _static_prefix(path_template: str) -> str:
    """The part of the path template before the first `{placeholder}` (often empty)."""
    return path_template.split("{", 1)[0]


class StackIndex:
    def __init__(self, git: GitClient, deploy_target: DeployTargetCfg, repo_ref: RepoRef) -> None:
        self._git = git
        self._repo_ref = repo_ref
        self._prefix = _static_prefix(deploy_target.pathTemplate)
        self._lock = threading.Lock()
        self._stacks: dict[str, StackRecord] = {}

    def rebuild(self) -> None:
        """Re-list the deploy target repo and replace the cache. Bad files are skipped."""
        files = self._git.list_files(self._repo_ref, self._prefix)
        stacks: dict[str, StackRecord] = {}
        for path, content in files:
            try:
                record = parse_component_file(yaml.safe_load(content))
            except (yaml.YAMLError, InvalidMetadata) as exc:
                logger.warning("skipping %s: %s", path, exc)
                continue
            stack = stacks.get(record.stack)
            if stack is None:
                # Env/owner/expiry/thread are uniform per stack: first component seen wins.
                stack = stacks[record.stack] = StackRecord(
                    name=record.stack,
                    catalog=record.catalog,
                    env=record.env,
                    owner=record.owner,
                    expires_at=record.expires_at,
                    thread_ts=record.thread_ts,
                    channel=record.channel,
                )
            stack.components[record.component] = record
        with self._lock:
            self._stacks = stacks

    def get(self, stack: str) -> StackRecord | None:
        with self._lock:
            return self._stacks.get(stack)

    def list_live(self) -> list[StackRecord]:
        with self._lock:
            return list(self._stacks.values())

    def list_by_owner(self, user: str) -> list[StackRecord]:
        with self._lock:
            return [s for s in self._stacks.values() if s.owner == user]

    def list_expiring(self, before: datetime) -> list[StackRecord]:
        with self._lock:
            return [
                s
                for s in self._stacks.values()
                if s.expires_at is not None and s.expires_at <= before
            ]

    def count_live_for(self, user: str) -> int:
        with self._lock:
            return sum(1 for s in self._stacks.values() if s.owner == user)

    def upsert(self, record: StackRecord) -> None:
        with self._lock:
            self._stacks[record.name] = record

    def remove(self, stack: str) -> None:
        with self._lock:
            self._stacks.pop(stack, None)
