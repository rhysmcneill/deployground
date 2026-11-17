from deployground.metadata import StackRecord
from deployground.slack.progress import (
    MAX_COMPONENT_ROWS,
    OVERALL_EMOJI,
    completion_blocks,
    render_progress,
    update_thread,
)
from deployground.states import CompState, StackState


def _texts(blocks) -> str:
    return str(blocks)


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat_update(self, **kwargs):
        self.calls.append(kwargs)


def _record(channel="C1", ts="1.0") -> StackRecord:
    return StackRecord(
        name="shop", catalog="shop", env="dev", owner="U1", expires_at=None, thread_ts=ts, channel=channel
    )


def test_progress_summarizes_large_stacks():
    states = {f"c{i}": CompState.Progressing for i in range(20)}
    for i in range(7):
        states[f"c{i}"] = CompState.Healthy
    states["c7"] = CompState.Failed
    assert len(states) > MAX_COMPONENT_ROWS
    text = _texts(render_progress("shop", states, StackState.Deploying))
    assert "7/20 Healthy, 1 Failed, 12 Progressing" in text
    assert "c3" not in text


def test_progress_renders_destroying_state():
    blocks = render_progress("shop", {"web": CompState.Progressing}, StackState.Destroying)
    text = _texts(blocks)
    assert OVERALL_EMOJI[StackState.Destroying] in text
    assert "Destroying" in text
    assert "web" in text


def test_completion_pings_owner():
    blocks = completion_blocks("shop", True, "U123", [("App", "https://a.example")])
    text = _texts(blocks)
    assert "✅ Deployment complete" in text
    assert "<@U123>" in text
    assert "<https://a.example|App>" in text


def test_failure_names_component():
    blocks = completion_blocks(
        "shop", False, "U123", [("Argo", "https://argo.example")], failed_component="web"
    )
    text = _texts(blocks)
    assert "❌" in text and "web failed" in text
    assert "https://argo.example" in text


def test_update_thread_idempotent_by_thread_ts():
    client = FakeClient()
    blocks = render_progress("shop", {"web": CompState.Healthy}, StackState.Running)
    update_thread(client, _record(), blocks)
    update_thread(client, _record(), blocks)
    assert len(client.calls) == 1
    assert client.calls[0]["channel"] == "C1" and client.calls[0]["ts"] == "1.0"
    other = render_progress("shop", {"web": CompState.Failed}, StackState.Failed)
    update_thread(client, _record(), other)
    assert len(client.calls) == 2
