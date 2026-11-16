"""Real Slack ``MembershipResolver``: usergroup and channel memberships, cached ~5 minutes.

Failures propagate; ``AllowlistAuthz`` already treats a failed lookup as "no memberships"
(default deny), so nothing here swallows errors. A failed refresh does not poison the cache.

Group principals in access rules may be written as a usergroup id (``S0123``) or its handle,
so both are returned. Channel principals are channel ids.
"""

import threading
import time
from collections.abc import Callable
from typing import Any

CACHE_TTL_SECONDS = 300.0
_PAGE_LIMIT = 200


class SlackMembershipResolver:
    def __init__(
        self,
        client: Any,
        ttl: float = CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        # usergroup id/handle -> member user ids, with the time it was fetched.
        self._groups: tuple[float, dict[str, set[str]]] | None = None
        self._channels: dict[str, tuple[float, set[str]]] = {}

    def usergroups_of(self, user: str) -> set[str]:
        with self._lock:
            now = self._clock()
            if self._groups is None or now - self._groups[0] >= self._ttl:
                self._groups = (now, self._fetch_groups())
            return {name for name, members in self._groups[1].items() if user in members}

    def channels_of(self, user: str) -> set[str]:
        with self._lock:
            now = self._clock()
            cached = self._channels.get(user)
            if cached is None or now - cached[0] >= self._ttl:
                cached = self._channels[user] = (now, self._fetch_channels(user))
            return set(cached[1])

    def _fetch_groups(self) -> dict[str, set[str]]:
        members_by_name: dict[str, set[str]] = {}
        for group in self._client.usergroups_list().get("usergroups", []):
            members = set(self._client.usergroups_users_list(usergroup=group["id"])["users"])
            members_by_name[group["id"]] = members
            if group.get("handle"):
                members_by_name[group["handle"]] = members
        return members_by_name

    def _fetch_channels(self, user: str) -> set[str]:
        channels: set[str] = set()
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "user": user,
                "types": "public_channel,private_channel",
                "limit": _PAGE_LIMIT,
            }
            if cursor:
                kwargs["cursor"] = cursor
            response = self._client.users_conversations(**kwargs)
            channels.update(c["id"] for c in response.get("channels", []))
            cursor = (response.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return channels
