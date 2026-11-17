from pathlib import Path

import yaml

from deployground.cli import main
from deployground.config.loader import load_config
from deployground.config.models import Config

ROOT = Path(__file__).parent.parent
EXAMPLE = ROOT / "examples" / "config.yaml"
CHART_VALUES = ROOT / "helm" / "charts" / "deployground" / "values.yaml"


def test_bootstrap_emits_manifests_for_config(capsys):
    assert main(["bootstrap", "--config", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "# --- applicationset-homelab.yaml ---" in out
    assert "# --- argocd-notifications-cm.yaml ---" in out
    docs = [d for d in yaml.safe_load_all(out) if d]
    assert "ApplicationSet" in {d["kind"] for d in docs}
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    assert cm["metadata"]["name"] == "argocd-notifications-cm"


def test_bootstrap_only_applicationset_omits_notifications_cm(capsys):
    assert main(["bootstrap", "--config", str(EXAMPLE), "--only", "applicationset"]) == 0
    out = capsys.readouterr().out
    assert "# --- applicationset-homelab.yaml ---" in out
    assert "argocd-notifications-cm" not in out
    docs = [d for d in yaml.safe_load_all(out) if d]
    assert {d["kind"] for d in docs} == {"ApplicationSet"}


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


def test_chart_default_config_validates():
    """The Helm chart's default `config:` block must itself be a valid Config.

    Catches drift like a stale/renamed top-level key (e.g. the pre-refactor `stacks`)
    that `extra="forbid"` would otherwise only surface as a crash-looping pod.
    """
    values = yaml.safe_load(CHART_VALUES.read_text())
    cfg = Config.model_validate(values["config"])
    assert cfg.catalog == {}


def test_slack_manifest_has_required_settings():
    manifest = yaml.safe_load((ROOT / "deploy" / "slack-app-manifest.yaml").read_text())
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert manifest["settings"]["interactivity"]["is_enabled"] is True
    assert manifest["features"]["app_home"]["home_tab_enabled"] is True
    # The bot DMs the user (channel=<user id>) whenever a button click has no channel
    # context -- every Home-tab-initiated action. Without this, those calls 400 with
    # "messages_tab_disabled" and Home tab buttons appear to silently do nothing.
    assert manifest["features"]["app_home"]["messages_tab_enabled"] is True
    assert "app_home_opened" in manifest["settings"]["event_subscriptions"]["bot_events"]
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
