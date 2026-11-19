import re
from datetime import UTC, datetime

import pytest

from deployground.metadata import (
    LABEL_COMPONENT,
    LABEL_MANAGED,
    LABEL_STACK,
    RESERVED_COMPONENT,
    ComponentRecord,
    InvalidMetadata,
    from_annotations,
    parse_component_file,
    parse_metadata_block,
    to_annotations,
    to_metadata_block,
)


def test_reserved_component_is_a_valid_k8s_label_value():
    assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", RESERVED_COMPONENT)


def _record(
    expires_at: datetime | None = datetime(2026, 11, 13, 9, 0, tzinfo=UTC),
    catalog: str = "api",
) -> ComponentRecord:
    return ComponentRecord(
        stack="api",
        catalog=catalog,
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


def test_to_metadata_block_includes_catalog():
    record = _record(catalog="homepage-test")
    block = to_metadata_block(ComponentRecord(**{**record.__dict__, "stack": "pr-123"}))
    assert block["catalog"] == "homepage-test"
    assert block["stack"] == "pr-123"


def test_block_roundtrips_catalog():
    record = _record(catalog="homepage-test")
    record = ComponentRecord(**{**record.__dict__, "stack": "pr-123"})
    assert parse_metadata_block({"deployground": to_metadata_block(record)}) == record


def test_block_without_catalog_defaults_to_stack():
    record = _record()
    block = to_metadata_block(record)
    del block["catalog"]
    parsed = parse_metadata_block({"deployground": block})
    assert parsed.catalog == block["stack"]
    assert parsed.catalog == record.stack


def test_expires_at_is_rfc3339():
    block = to_metadata_block(_record())
    assert block["expiresAt"] == "2026-11-13T09:00:00Z"


def test_to_annotations_has_expected_keys():
    annotations = to_annotations(_record())
    assert set(annotations) == {
        "deployground.io/stack",
        "deployground.io/catalog",
        "deployground.io/component",
        "deployground.io/owner",
        "deployground.io/owner-email",
        "deployground.io/env",
        "deployground.io/expires-at",
        "deployground.io/version",
        "deployground.io/thread-ts",
        "deployground.io/channel",
        "deployground.io/overrides",
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


def test_block_roundtrips_non_empty_overrides():
    overrides = {"image.tag": "v9", "replicas": "5"}
    record = ComponentRecord(**{**_record().__dict__, "overrides": overrides})
    assert parse_metadata_block({"deployground": to_metadata_block(record)}) == record


def test_annotations_roundtrip_non_empty_overrides():
    overrides = {"image.tag": "v9", "replicas": "5"}
    record = ComponentRecord(**{**_record().__dict__, "overrides": overrides})
    assert from_annotations(to_annotations(record)) == record


def test_from_annotations_defaults_overrides_to_empty_when_annotation_absent():
    record = _record()
    annotations = to_annotations(record)
    del annotations["deployground.io/overrides"]
    parsed = from_annotations(annotations)
    assert parsed.overrides == {}


def test_from_annotations_rejects_malformed_overrides_json():
    """A corrupt overrides annotation must raise `InvalidMetadata`, not a bare
    `json.JSONDecodeError` -- `StackIndex.rebuild()` only catches `InvalidMetadata`/
    `yaml.YAMLError` per file so one bad file doesn't crash the whole rebuild."""
    annotations = to_annotations(_record())
    annotations["deployground.io/overrides"] = "{not valid json"
    with pytest.raises(InvalidMetadata):
        from_annotations(annotations)


def test_annotations_roundtrip_component_record():
    for record in (_record(), _record(expires_at=None)):
        assert from_annotations(to_annotations(record)) == record


def test_annotations_roundtrip_catalog():
    record = _record(catalog="homepage-test")
    record = ComponentRecord(**{**record.__dict__, "stack": "pr-123"})
    assert from_annotations(to_annotations(record)) == record


def test_from_annotations_without_catalog_defaults_to_stack():
    record = _record()
    annotations = to_annotations(record)
    del annotations["deployground.io/catalog"]
    parsed = from_annotations(annotations)
    assert parsed.catalog == record.stack


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.pop("deployground.io/owner"),
        lambda a: a.update({"deployground.io/expires-at": "not-a-date"}),
        lambda a: a.update({"deployground.io/expires-at": "2026-11-13T09:00:00"}),
    ],
)
def test_from_annotations_rejects_bad_input(mutate):
    annotations = to_annotations(_record())
    mutate(annotations)
    with pytest.raises(InvalidMetadata):
        from_annotations(annotations)


def test_parse_component_file_accepts_both_shapes():
    record = _record()
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "x", "annotations": to_annotations(record)},
    }
    assert parse_component_file(app) == record
    assert parse_component_file({"deployground": to_metadata_block(record)}) == record
    for bad in (None, [], {"kind": "Application"}, {"kind": "Application", "metadata": []}):
        with pytest.raises(InvalidMetadata):
            parse_component_file(bad)
