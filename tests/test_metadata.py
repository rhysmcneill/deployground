from datetime import UTC, datetime

import pytest

from deployground.metadata import (
    LABEL_COMPONENT,
    LABEL_MANAGED,
    LABEL_STACK,
    ComponentRecord,
    InvalidMetadata,
    parse_metadata_block,
    to_annotations,
    to_metadata_block,
)


def _record(
    expires_at: datetime | None = datetime(2026, 11, 13, 9, 0, tzinfo=UTC),
) -> ComponentRecord:
    return ComponentRecord(
        stack="api",
        component="api-server",
        owner="rhys",
        owner_email="rhys@example.com",
        env="homelab",
        expires_at=expires_at,
        version="1.2.3",
        thread_ts="1731402000.000100",
        channel="C0123456",
    )


def test_block_roundtrips_component_record():
    record = _record()
    assert parse_metadata_block({"deployground": to_metadata_block(record)}) == record


def test_block_roundtrips_component_record_without_expiry():
    record = _record(expires_at=None)
    block = to_metadata_block(record)
    assert block["expiresAt"] is None
    assert parse_metadata_block({"deployground": block}) == record


def test_expires_at_is_rfc3339():
    block = to_metadata_block(_record())
    assert block["expiresAt"] == "2026-11-13T09:00:00Z"


def test_to_annotations_has_expected_keys():
    annotations = to_annotations(_record())
    assert set(annotations) == {
        "deployground.io/stack",
        "deployground.io/component",
        "deployground.io/owner",
        "deployground.io/owner-email",
        "deployground.io/env",
        "deployground.io/expires-at",
        "deployground.io/version",
        "deployground.io/thread-ts",
        "deployground.io/channel",
    }
    assert all(isinstance(value, str) for value in annotations.values())


def test_label_constants():
    assert LABEL_STACK == "deployground.io/stack"
    assert LABEL_COMPONENT == "deployground.io/component"
    assert LABEL_MANAGED == "deployground.io/managed"


@pytest.mark.parametrize("bad", [{}, {"deployground": "oops"}, {"deployground": {"stack": "api"}}])
def test_parse_rejects_missing_or_malformed_block(bad):
    with pytest.raises(InvalidMetadata):
        parse_metadata_block(bad)


def test_invalid_metadata_is_value_error():
    assert issubclass(InvalidMetadata, ValueError)


def test_block_ignores_unknown_keys():
    record = _record()
    block = to_metadata_block(record) | {"futureField": "later-version"}
    assert parse_metadata_block({"deployground": block}) == record


def test_expires_at_accepts_unquoted_yaml_timestamp():
    # PyYAML safe_load turns an unquoted `2026-11-13T09:00:00Z` into an aware datetime.
    block = to_metadata_block(_record()) | {"expiresAt": datetime(2026, 11, 13, 9, 0, tzinfo=UTC)}
    assert parse_metadata_block({"deployground": block}) == _record()


def test_expires_at_rejects_naive_datetime():
    block = to_metadata_block(_record()) | {"expiresAt": datetime(2026, 11, 13, 9, 0)}
    with pytest.raises(InvalidMetadata):
        parse_metadata_block({"deployground": block})
