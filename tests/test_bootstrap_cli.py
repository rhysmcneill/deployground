from pathlib import Path

import yaml

from deployground.cli import main
from deployground.config.loader import load_config

ROOT = Path(__file__).parent.parent
EXAMPLE = ROOT / "examples" / "config.yaml"


def test_bootstrap_emits_manifests_for_config(capsys):
    assert main(["bootstrap", "--config", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "# --- applicationset-homelab.yaml ---" in out
    assert "# --- argocd-notifications-cm.yaml ---" in out
    docs = [d for d in yaml.safe_load_all(out) if d]
    assert "ApplicationSet" in {d["kind"] for d in docs}
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    assert cm["metadata"]["name"] == "argocd-notifications-cm"


def test_bootstrap_bad_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("git: {}\n")
    assert main(["bootstrap", "--config", str(bad)]) == 2
    assert "Invalid configuration" in capsys.readouterr().err


def test_bootstrap_missing_file_exits_2(tmp_path, capsys):
    assert main(["bootstrap", "--config", str(tmp_path / "nope.yaml")]) == 2
    assert "Invalid configuration" in capsys.readouterr().err


def test_example_config_passes_load_config():
    cfg = load_config(EXAMPLE, {})
    assert set(cfg.environments) == {"homelab", "prod"}


def test_slack_manifest_has_required_settings():
    manifest = yaml.safe_load((ROOT / "deploy" / "slack-app-manifest.yaml").read_text())
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert manifest["settings"]["interactivity"]["is_enabled"] is True
    assert manifest["features"]["app_home"]["home_tab_enabled"] is True
    shortcuts = manifest["features"]["shortcuts"]
    assert any(s["callback_id"] == "open_deploy_form" and s["type"] == "global" for s in shortcuts)
    scopes = set(manifest["oauth_config"]["scopes"]["bot"])
    assert {
        "chat:write",
        "commands",
        "usergroups:read",
        "users:read",
        "users:read.email",
        "im:write",
        "channels:read",
        "groups:read",
    } <= scopes
