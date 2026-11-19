import pytest

from deployground.dotpath import get_path, set_path


def test_get_path_reads_nested_value():
    assert get_path({"image": {"tag": "v1"}}, "image.tag") == "v1"


def test_get_path_raises_on_missing_segment():
    with pytest.raises(KeyError, match="image.tag"):
        get_path({"image": {}}, "image.tag")


def test_set_path_creates_intermediate_dicts_without_mutating_input():
    original = {"a": 1}
    result = set_path(original, "b.c", "v")
    assert result == {"a": 1, "b": {"c": "v"}}
    assert original == {"a": 1}


def test_set_path_overwrites_existing_leaf():
    assert set_path({"a": {"b": 1}}, "a.b", 2) == {"a": {"b": 2}}
