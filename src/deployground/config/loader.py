"""Layered config loading and safe hot-reload.

Layers, lowest to highest precedence:

1. Built-in defaults: whatever the Pydantic models in ``models.py`` default to.
   The YAML file must supply the sections the models require.
2. The YAML file.
3. Environment-variable overrides.

After layering the result is validated, including ``Config.validate_refs()``.

Environment overrides use the prefix ``DEPLOYGROUND_`` followed by a path whose
segments are separated by ``__``. Segments are matched case-insensitively
against existing keys in the file and against model field names, so
``DEPLOYGROUND_LIFECYCLE__MAXLIVEPERUSER=5`` sets ``lifecycle.maxLivePerUser``.
Segments that match nothing are used verbatim (lowercased), so they either
create a new key or fail model validation. Values are parsed as YAML scalars,
so ``true``, ``5`` and plain strings all work.
"""

import logging
import threading
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from deployground.config.models import Config

logger = logging.getLogger(__name__)

ENV_PREFIX = "DEPLOYGROUND_"
_SEPARATOR = "__"


def _model_of(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _dict_value_model(annotation: Any) -> type[BaseModel] | None:
    if typing.get_origin(annotation) is dict:
        return _model_of(typing.get_args(annotation)[1])
    return None


def _resolve_key(segment: str, existing: Mapping[str, Any], model: type[BaseModel] | None) -> str:
    wanted = segment.lower()
    for key in existing:
        if key.lower() == wanted:
            return key
    if model is not None:
        for name in model.model_fields:
            if name.lower() == wanted:
                return name
    return wanted


def _apply_override(data: dict[str, Any], segments: list[str], value: Any) -> None:
    # `fields` is the model whose field names the current node's keys are, if known.
    # `entries` is the model of the entries of the current node when it is a
    # dict[str, Model], whose own keys are free-form names.
    fields: type[BaseModel] | None = Config
    entries: type[BaseModel] | None = None
    node = data
    for segment in segments[:-1]:
        key = _resolve_key(segment, node, fields)
        child = node.get(key)
        if not isinstance(child, dict):
            child = node[key] = {}
        if entries is not None:
            fields, entries = entries, None
        else:
            field = fields.model_fields.get(key) if fields else None
            annotation = field.annotation if field else None
            entries = _dict_value_model(annotation)
            fields = None if entries else _model_of(annotation)
        node = child
    node[_resolve_key(segments[-1], node, fields)] = value


def load_config(path: Path, env: Mapping[str, str]) -> Config:
    """Load, layer and validate config. Raises on any invalid result."""
    raw = yaml.safe_load(path.read_text())
    data: dict[str, Any] = raw if raw is not None else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")

    for name, text in env.items():
        if not name.upper().startswith(ENV_PREFIX):
            continue
        segments = name[len(ENV_PREFIX) :].split(_SEPARATOR)
        if not all(segments):
            raise ValueError(f"malformed override variable {name!r}")
        _apply_override(data, segments, yaml.safe_load(text))

    config = Config.model_validate(data)
    config.validate_refs()
    return config


class ConfigWatcher:
    """Holds the current Config; callers trigger ``reload()``."""

    def __init__(self, path: Path, env: Mapping[str, str]) -> None:
        self._path = path
        self._env = env
        self._lock = threading.Lock()
        self._config = load_config(path, env)

    def current(self) -> Config:
        with self._lock:
            return self._config

    def reload(self) -> Config:
        """Re-read the config. On failure keep and return the previous Config."""
        try:
            new = load_config(self._path, self._env)
        except Exception:
            logger.exception("config reload failed; keeping previous config")
            return self.current()
        with self._lock:
            self._config = new
        return new
