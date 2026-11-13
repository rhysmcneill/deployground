"""Stack and component metadata, stored in the `deployground:` block of each component file.

Block keys are camelCase (`ownerEmail`, `expiresAt`, `threadTs`); the Python record
fields are snake_case. `expiresAt` is always present in the block: an RFC3339 UTC
timestamp (e.g. `2026-11-13T09:00:00Z`), or `null` when the stack never expires
(`ttl: none`). Parsing rejects a block with missing or wrongly typed fields, raising
`InvalidMetadata` (a `ValueError`) so callers can skip bad files. Unknown keys inside
the block are ignored, so newer writers stay readable by older orchestrators.
An unquoted YAML timestamp (already a datetime after `safe_load`) is accepted as
`expiresAt` as long as it is timezone-aware.

Annotations use the same fields with kebab-case keys under `deployground.io/`.
All annotation values are strings; a missing expiry is the string `"none"`.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

BLOCK_KEY = "deployground"
ANNOTATION_PREFIX = "deployground.io/"

LABEL_STACK = "deployground.io/stack"
LABEL_COMPONENT = "deployground.io/component"
LABEL_MANAGED = "deployground.io/managed"

NO_EXPIRY = "none"


class InvalidMetadata(ValueError):
    """Raised when a `deployground:` block is missing or malformed."""


@dataclass(frozen=True)
class ComponentRecord:
    stack: str
    component: str
    owner: str
    owner_email: str
    env: str
    expires_at: datetime | None
    version: str
    thread_ts: str
    channel: str


@dataclass
class StackRecord:
    name: str
    env: str
    owner: str
    expires_at: datetime | None
    thread_ts: str
    channel: str
    components: dict[str, ComponentRecord] = field(default_factory=dict)


_BLOCK_KEYS = frozenset(
    {
        "stack",
        "component",
        "owner",
        "ownerEmail",
        "env",
        "expiresAt",
        "version",
        "threadTs",
        "channel",
    }
)


def _format_expiry(expires_at: datetime | None) -> str | None:
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    return expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_expiry(value: str | datetime) -> datetime:
    """Accept an RFC3339 string or an aware datetime (PyYAML resolves unquoted timestamps)."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise InvalidMetadata(f"expiresAt is not an RFC3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise InvalidMetadata(f"expiresAt must include a timezone: {value!r}")
    return parsed


def to_metadata_block(record: ComponentRecord) -> dict[str, Any]:
    """Serialize a record to the dict stored under a component file's `deployground:` key."""
    return {
        "stack": record.stack,
        "component": record.component,
        "owner": record.owner,
        "ownerEmail": record.owner_email,
        "env": record.env,
        "expiresAt": _format_expiry(record.expires_at),
        "version": record.version,
        "threadTs": record.thread_ts,
        "channel": record.channel,
    }


def parse_metadata_block(file_dict: Any) -> ComponentRecord:
    """Parse the `deployground:` block out of a whole component file's parsed YAML dict."""
    if not isinstance(file_dict, dict) or BLOCK_KEY not in file_dict:
        raise InvalidMetadata(f"missing {BLOCK_KEY!r} block")
    block = file_dict[BLOCK_KEY]
    if not isinstance(block, dict):
        raise InvalidMetadata(f"{BLOCK_KEY!r} block must be a mapping")

    # Unknown keys are ignored so that newer writers stay readable by older orchestrators.
    missing = sorted(_BLOCK_KEYS - block.keys())
    if missing:
        raise InvalidMetadata(f"{BLOCK_KEY!r} block is missing keys: {', '.join(missing)}")

    for key in _BLOCK_KEYS - {"expiresAt"}:
        if not isinstance(block[key], str):
            raise InvalidMetadata(f"{key} must be a string")

    expires_value = block["expiresAt"]
    if expires_value is not None and not isinstance(expires_value, str | datetime):
        raise InvalidMetadata("expiresAt must be a string, a timestamp or null")
    expires_at = None if expires_value is None else _parse_expiry(expires_value)

    return ComponentRecord(
        stack=block["stack"],
        component=block["component"],
        owner=block["owner"],
        owner_email=block["ownerEmail"],
        env=block["env"],
        expires_at=expires_at,
        version=block["version"],
        thread_ts=block["threadTs"],
        channel=block["channel"],
    )


def to_annotations(record: ComponentRecord) -> dict[str, str]:
    """Map a record to `deployground.io/*` annotation keys with string values."""
    expires = _format_expiry(record.expires_at)
    return {
        f"{ANNOTATION_PREFIX}stack": record.stack,
        f"{ANNOTATION_PREFIX}component": record.component,
        f"{ANNOTATION_PREFIX}owner": record.owner,
        f"{ANNOTATION_PREFIX}owner-email": record.owner_email,
        f"{ANNOTATION_PREFIX}env": record.env,
        f"{ANNOTATION_PREFIX}expires-at": NO_EXPIRY if expires is None else expires,
        f"{ANNOTATION_PREFIX}version": record.version,
        f"{ANNOTATION_PREFIX}thread-ts": record.thread_ts,
        f"{ANNOTATION_PREFIX}channel": record.channel,
    }
