from deployground.catalog import Catalog
from deployground.config.models import Config
from deployground.slack.forms import (
    ACTION_ID,
    STATIC_SELECT_LIMIT,
    DeployInputs,
    build_deploy_modal,
    build_override_blocks,
    parse_submission,
)

CFG = {
    "git": {
        "provider": "github",
        "repos": {
            "gitops": {
                "owner": "o",
                "name": "g",
                "branch": "main",
                "authRef": {"tokenEnv": "T"},
            }
        },
    },
    "environments": {
        e: {
            "cluster": e,
            "repo": "gitops",
            "autoMerge": True,
            "ttl": {"default": "24h", "max": "168h"},
            "requiresApproval": False,
        }
        for e in ("dev", "staging", "prod")
    },
    "catalog": {
        "shop": {
            "components": {
                "web": {"image": "i/web", "defaultVersion": "v2.0.0"},
                "worker": {"image": "i/worker", "defaultVersion": "v3.0.0"},
            }
        },
        "api": {"components": {"api": {"image": "i/api", "defaultVersion": "v1.0.0"}}},
    },
    "deployTarget": {
        "type": "applicationset",
        "repo": "gitops",
        "pathTemplate": "{env}/{stack}/{component}/config.yaml",
    },
    "statusSource": {"type": "argocd-notifications", "webhookPath": "/argocd/notifications"},
}


class FakeAuthz:
    def __init__(self, envs):
        self.envs = envs

    def allowed_environments(self, user):
        return set(self.envs)

    def is_admin(self, user):
        return False

    def can_manage(self, user, stack_owner):
        return False


def catalog(n_extra=0):
    cfg = dict(CFG)
    entries = dict(CFG["catalog"])
    for i in range(n_extra):
        entries[f"s{i:03d}"] = {"components": {"c": {"image": "i/c", "defaultVersion": "v1"}}}
    cfg["catalog"] = entries
    return Catalog(lambda: Config.model_validate(cfg))


def block(view, block_id):
    return next(b for b in view["blocks"] if b.get("block_id") == block_id)


def _text(value):
    return {ACTION_ID: {"type": "plain_text_input", "value": value}}


def state(catalog="shop", env="dev", ttl="24h", overrides=None, name="pr-123"):
    s = {}
    if catalog is not None:
        s["catalog"] = {ACTION_ID: {"type": "static_select", "selected_option": {"value": catalog}}}
    if env is not None:
        s["env"] = {ACTION_ID: {"type": "static_select", "selected_option": {"value": env}}}
    s["ttl"] = _text(ttl)
    s["stack"] = _text(name)
    for comp_name, v in (overrides or {}).items():
        s[f"override::{comp_name}"] = _text(v)
    return s


def test_modal_lists_only_allowed_envs():
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"prod", "dev"}))
    assert view["callback_id"] == "deploy_submit"
    opts = block(view, "env")["element"]["options"]
    assert [o["value"] for o in opts] == ["dev", "prod"]


def test_catalog_picker_uses_external_select_over_100():
    small = block(build_deploy_modal("U1", catalog(), FakeAuthz({"dev"})), "catalog")
    assert small["element"]["type"] == "static_select"
    big = block(
        build_deploy_modal("U1", catalog(STATIC_SELECT_LIMIT), FakeAuthz({"dev"})), "catalog"
    )
    assert big["element"]["type"] == "external_select"


def test_stack_name_is_a_plain_text_input_with_hint():
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"dev"}))
    stack_block = block(view, "stack")
    assert stack_block["element"]["type"] == "plain_text_input"
    assert "hint" in stack_block


def test_override_picker_and_blocks_sit_between_catalog_and_stack_name():
    prefill = DeployInputs(
        catalog="shop", stack="pr-123", env="dev", overrides={"web": "v9"}, ttl="2h"
    )
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"dev"}), prefill)
    block_ids = [b.get("block_id") for b in view["blocks"] if b.get("block_id")]
    assert block_ids == ["catalog", "override_components", "override::web", "stack", "env", "ttl"]


def test_override_blocks_only_for_selected_prefilled_to_default():
    entry = catalog().get("shop")
    blocks = build_override_blocks(entry, ["worker"])
    assert [b["block_id"] for b in blocks] == ["override::worker"]
    assert blocks[0]["element"]["initial_value"] == "v3.0.0"
    assert blocks[0]["element"]["action_id"] == ACTION_ID


def test_expires_in_defaults_to_24h():
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"dev"}))
    assert block(view, "ttl")["element"]["initial_value"] == "24h"


def test_prefill_populates_fields():
    prefill = DeployInputs(
        catalog="shop", stack="pr-123", env="prod", overrides={"web": "v9"}, ttl="2h"
    )
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"dev", "prod"}), prefill)
    assert block(view, "catalog")["element"]["initial_option"]["value"] == "shop"
    assert block(view, "stack")["element"]["initial_value"] == "pr-123"
    assert block(view, "env")["element"]["initial_option"]["value"] == "prod"
    assert block(view, "ttl")["element"]["initial_value"] == "2h"
    picked = block(view, "override_components")["element"]["initial_options"]
    assert [o["value"] for o in picked] == ["web"]
    assert block(view, "override::web")["element"]["initial_value"] == "v9"


def test_parse_submission_extracts_overrides_and_ttl():
    inputs, errors = parse_submission(state(ttl=" 2h ", overrides={"web": " v9 "}))
    assert errors == {}
    assert inputs == DeployInputs(
        catalog="shop", stack="pr-123", env="dev", overrides={"web": "v9"}, ttl="2h"
    )
    inputs, _ = parse_submission(state(ttl="none"))
    assert inputs.ttl == "none" and inputs.overrides == {}


def test_parse_submission_returns_inline_errors_for_bad_input():
    bad = state(catalog=None, env=None, ttl="soon", overrides={"web": "  "})
    inputs, errors = parse_submission(bad)
    assert inputs is None
    assert set(errors) == {"catalog", "env", "ttl", "override::web"}
    _, errors = parse_submission(state(ttl=""))
    assert set(errors) == {"ttl"}


def test_parse_submission_rejects_invalid_stack_name():
    inputs, errors = parse_submission(state(name="Not Valid!"))
    assert inputs is None
    assert "stack" in errors


def test_parse_submission_accepts_valid_stack_name():
    inputs, errors = parse_submission(state(name="pr-123"))
    assert errors == {}
    assert inputs.stack == "pr-123"
    assert inputs.catalog == "shop"


def test_parse_submission_rejects_empty_stack_name():
    inputs, errors = parse_submission(state(name=""))
    assert inputs is None
    assert "stack" in errors


def test_empty_envs_view_has_no_inputs_and_no_submit():
    view = build_deploy_modal("U1", catalog(), FakeAuthz(set()))
    assert not [b for b in view["blocks"] if b["type"] == "input"]
    assert "submit" not in view


def test_catalog_and_override_picker_dispatch_actions():
    prefill = DeployInputs(
        catalog="shop", stack="pr-123", env="dev", overrides={"web": "v9"}, ttl="2h"
    )
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"dev"}), prefill)
    assert block(view, "catalog")["dispatch_action"] is True
    assert block(view, "override_components")["dispatch_action"] is True
