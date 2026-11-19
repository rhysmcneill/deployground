"""Dot-path get/set helpers for nested dicts, e.g. ``"image.tag"``."""

import copy
from typing import Any


def get_path(data: dict, path: str) -> Any:
    """Return the value at ``path``, raising ``KeyError`` if any segment is missing."""
    node: Any = data
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            raise KeyError(f"missing {path!r}: no key {segment!r}")
        node = node[segment]
    return node


def set_path(data: dict, path: str, value: Any) -> dict:
    """Return a copy of ``data`` with ``value`` set at ``path``.

    Intermediate dicts are created as needed. ``data`` is not mutated.
    """
    result = copy.deepcopy(data)
    *parents, leaf = path.split(".")
    node = result
    for segment in parents:
        child = node.get(segment)
        if not isinstance(child, dict):
            child = {}
            node[segment] = child
        node = child
    node[leaf] = value
    return result
