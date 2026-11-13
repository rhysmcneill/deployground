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
    "stacks": {
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
    stacks = dict(CFG["stacks"])
    for i in range(n_extra):
        stacks[f"s{i:03d}"] = {"components": {"c": {"image": "i/c", "defaultVersion": "v1"}}}
    cfg["stacks"] = stacks
    return Catalog(lambda: Config.model_validate(cfg))


def block(view, block_id):
    return next(b for b in view["blocks"] if b.get("block_id") == block_id)


def state(stack="shop", env="dev", ttl="24h", overrides=None):
    s = {}
    if stack is not None:
        s["stack"] = {ACTION_ID: {"type": "static_select", "selected_option": {"value": stack}}}
    if env is not None:
        s["env"] = {ACTION_ID: {"type": "static_select", "selected_option": {"value": env}}}
    s["ttl"] = {ACTION_ID: {"type": "plain_text_input", "value": ttl}}
    for name, v in (overrides or {}).items():
        s[f"override::{name}"] = {ACTION_ID: {"type": "plain_text_input", "value": v}}
    return s


def test_modal_lists_only_allowed_envs():
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"prod", "dev"}))
    assert view["callback_id"] == "deploy_submit"
    opts = block(view, "env")["element"]["options"]
    assert [o["value"] for o in opts] == ["dev", "prod"]


def test_stack_picker_uses_external_select_over_100():
    small = block(build_deploy_modal("U1", catalog(), FakeAuthz({"dev"})), "stack")
    assert small["element"]["type"] == "static_select"
    big = block(build_deploy_modal("U1", catalog(STATIC_SELECT_LIMIT), FakeAuthz({"dev"})), "stack")
    assert big["element"]["type"] == "external_select"


def test_override_blocks_only_for_selected_prefilled_to_default():
    stack = catalog().get("shop")
    blocks = build_override_blocks(stack, ["worker"])
    assert [b["block_id"] for b in blocks] == ["override::worker"]
    assert blocks[0]["element"]["initial_value"] == "v3.0.0"
    assert blocks[0]["element"]["action_id"] == ACTION_ID


def test_expires_in_defaults_to_24h():
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"dev"}))
    assert block(view, "ttl")["element"]["initial_value"] == "24h"


def test_prefill_populates_fields():
    prefill = DeployInputs("shop", "prod", {"web": "v9"}, "2h")
    view = build_deploy_modal("U1", catalog(), FakeAuthz({"dev", "prod"}), prefill)
    assert block(view, "stack")["element"]["initial_option"]["value"] == "shop"
    assert block(view, "env")["element"]["initial_option"]["value"] == "prod"
    assert block(view, "ttl")["element"]["initial_value"] == "2h"
    picked = block(view, "override_components")["element"]["initial_options"]
    assert [o["value"] for o in picked] == ["web"]
    assert block(view, "override::web")["element"]["initial_value"] == "v9"


def test_parse_submission_extracts_overrides_and_ttl():
    inputs, errors = parse_submission(state(ttl=" 2h ", overrides={"web": " v9 "}))
    assert errors == {}
    assert inputs == DeployInputs("shop", "dev", {"web": "v9"}, "2h")
    inputs, _ = parse_submission(state(ttl="none"))
    assert inputs.ttl == "none" and inputs.overrides == {}


def test_parse_submission_returns_inline_errors_for_bad_input():
    bad = state(stack=None, env=None, ttl="soon", overrides={"web": "  "})
    inputs, errors = parse_submission(bad)
    assert inputs is None
    assert set(errors) == {"stack", "env", "ttl", "override::web"}
    _, errors = parse_submission(state(ttl=""))
    assert set(errors) == {"ttl"}
