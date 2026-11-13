"""Bolt app builder: registers every handler from ``slack.actions`` against a Deps bundle."""

import re
from typing import Any

from slack_bolt import App

from deployground.slack import actions
from deployground.slack.actions import Deps
from deployground.slack.forms import ACTION_ID, CALLBACK_ID, OVERRIDE_PICKER_BLOCK
from deployground.slack.home import OPEN_DEPLOY_FORM


def build_slack_app(deps: Deps, app: App | None = None) -> App:
    app = app or App()

    @app.shortcut(OPEN_DEPLOY_FORM)
    def _shortcut(ack: Any, body: Any, client: Any) -> None:
        actions.handle_open_form(deps, ack, body, client)

    @app.action(OPEN_DEPLOY_FORM)
    def _open(ack: Any, body: Any, client: Any) -> None:
        actions.handle_open_form(deps, ack, body, client)

    @app.action(actions.PREFILL_ACTION)
    def _prefill(ack: Any, body: Any, client: Any) -> None:
        actions.handle_prefill(deps, ack, body, client)

    for block in ("stack", OVERRIDE_PICKER_BLOCK):

        @app.action({"block_id": block, "action_id": ACTION_ID})
        def _change(ack: Any, body: Any, client: Any) -> None:
            actions.handle_modal_change(deps, ack, body, client)

    @app.options({"block_id": "stack", "action_id": ACTION_ID})
    def _options(ack: Any, body: Any) -> None:
        actions.handle_stack_options(deps, ack, body)

    @app.view(CALLBACK_ID)
    def _submit(ack: Any, body: Any, client: Any) -> None:
        actions.handle_submit(deps, ack, body, client)

    @app.action(re.compile(r"^extend::"))
    def _extend(ack: Any, body: Any, client: Any) -> None:
        actions.handle_extend(deps, ack, body, client)

    @app.action(re.compile(r"^keep::"))
    def _keep(ack: Any, body: Any, client: Any) -> None:
        actions.handle_keep(deps, ack, body, client)

    @app.action(re.compile(r"^destroy(_now)?::"))
    def _destroy(ack: Any, body: Any, client: Any) -> None:
        actions.handle_destroy_click(deps, ack, body, client)

    @app.action(re.compile(r"^confirm_destroy::"))
    def _confirm(ack: Any, body: Any, client: Any, respond: Any) -> None:
        actions.handle_confirm_destroy(deps, ack, body, client, respond)

    @app.action(re.compile(r"^cancel_destroy::"))
    def _cancel(ack: Any, body: Any, respond: Any) -> None:
        actions.handle_cancel_destroy(deps, ack, body, respond)

    @app.event("app_home_opened")
    def _home(event: Any, client: Any) -> None:
        actions.handle_home_opened(deps, client, event)

    return app
