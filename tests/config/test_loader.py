from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from deployground.config.loader import ConfigWatcher, load_config

from .test_models import VALID


def write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def test_env_override_beats_file(tmp_path):
    data = deepcopy(VALID)
    data["lifecycle"] = {"maxLivePerUser": 3}
    path = write(tmp_path / "config.yaml", data)

    config = load_config(path, {"DEPLOYGROUND_LIFECYCLE__MAXLIVEPERUSER": "5"})

    assert config.lifecycle.maxLivePerUser == 5
    assert load_config(path, {}).lifecycle.maxLivePerUser == 3


def test_invalid_reload_keeps_previous(tmp_path, caplog):
    path = write(tmp_path / "config.yaml", VALID)
    watcher = ConfigWatcher(path, {})
    before = watcher.current()

    broken = deepcopy(VALID)
    broken["environments"]["homelab"]["repo"] = "missing"
    write(path, broken)

    result = watcher.reload()

    assert result is before
    assert watcher.current() is before
    assert "missing" in caplog.text


def test_env_override_resolves_nested_names_case_insensitively(tmp_path):
    path = write(tmp_path / "config.yaml", VALID)

    config = load_config(
        path,
        {
            "DEPLOYGROUND_ENVIRONMENTS__HOMELAB__AUTOMERGE": "false",
            "DEPLOYGROUND_GIT__REPOS__GITOPS__BRANCH": "release",
            "UNRELATED": "x",
        },
    )

    assert config.environments["homelab"].autoMerge is False
    assert config.git.repos["gitops"].branch == "release"


def test_startup_invalid_raises(tmp_path):
    broken = deepcopy(VALID)
    broken["environments"]["homelab"]["repo"] = "missing"
    path = write(tmp_path / "config.yaml", broken)

    with pytest.raises(ValueError):
        load_config(path, {})
