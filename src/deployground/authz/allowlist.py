import logging
from collections.abc import Callable, Iterable

from deployground.authz.base import MembershipResolver
from deployground.config.models import AccessRule

log = logging.getLogger(__name__)


class AllowlistAuthz:
    """Default-deny allowlist authorization.

    A rule matches when its principal (user / group / channel) matches the
    requester. Permissions are the union over matching rules. If a membership
    lookup fails, memberships are treated as empty (deny), never propagated.
    """

    def __init__(
        self,
        rules: Iterable[AccessRule],
        resolver: MembershipResolver,
        all_envs: Iterable[str],
    ) -> None:
        self._rules = list(rules)
        self._resolver = resolver
        self._all_envs = frozenset(all_envs)

    def _safe_lookup(self, kind: str, fn: Callable[[str], set[str]], user: str) -> set[str]:
        try:
            return set(fn(user))
        except Exception:
            log.warning(
                "membership lookup failed (%s) for %s; denying group/channel grants",
                kind,
                user,
                exc_info=True,
            )
            return set()

    def _matching_rules(self, user: str) -> list[AccessRule]:
        groups: set[str] | None = None
        channels: set[str] | None = None
        matched: list[AccessRule] = []
        for rule in self._rules:
            if rule.user is not None:
                if rule.user == user:
                    matched.append(rule)
            elif rule.group is not None:
                if groups is None:
                    groups = self._safe_lookup("usergroups", self._resolver.usergroups_of, user)
                if rule.group in groups:
                    matched.append(rule)
            elif rule.channel is not None:
                if channels is None:
                    channels = self._safe_lookup("channels", self._resolver.channels_of, user)
                if rule.channel in channels:
                    matched.append(rule)
        return matched

    def allowed_environments(self, user: str) -> set[str]:
        envs: set[str] = set()
        for rule in self._matching_rules(user):
            for env in rule.environments:
                if env == "*":
                    envs |= self._all_envs
                else:
                    envs.add(env)
        return envs

    def is_admin(self, user: str) -> bool:
        return any(rule.admin for rule in self._matching_rules(user))

    def can_manage(self, user: str, stack_owner: str) -> bool:
        return user == stack_owner or self.is_admin(user)
