from typing import Protocol


class MembershipResolver(Protocol):
    """Resolves a Slack user's group/channel memberships (cached by the implementation)."""

    def usergroups_of(self, user: str) -> set[str]: ...

    def channels_of(self, user: str) -> set[str]: ...


class AuthzProvider(Protocol):
    def allowed_environments(self, user: str) -> set[str]: ...

    def is_admin(self, user: str) -> bool: ...

    def can_manage(self, user: str, stack_owner: str) -> bool: ...
