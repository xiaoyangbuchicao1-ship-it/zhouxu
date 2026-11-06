"""DeepSeek modes and independent protocol adapter isolation."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from backend.config.relay import RelayConfig
from backend.config.settings import load_settings
from backend.contracts.modules.perception import ContextItem, ContextLink
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.model.responses import ResponsesModel, ResponsesTransport
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.integration.test_adapters import request as model_request
from backend.tests.support import relay_settings


@pytest.mark.parametrize("mode", ["none", "low", "high"])
@pytest.mark.parametrize("role", ["main", "review"])
async def test_role_selection_uses_config_and_only_matching_credentials(
    monkeypatch, mode, role
):
    cfg = load_settings()
    cfg.model.thinking_mode = mode
    reads = []

    def read_ds(_):
        reads.append("deepseek")
        return SecretStr("ds-synthetic")

    class Model:
        def __init__(self, settings, key, events, *, transport=None):
            self.settings, self.key, self.transport = settings, key, transport

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            if self.transport:
                await self.transport.aclose()

    monkeypatch.setattr("backend.infrastructure.model.factory.load_key", read_ds)
    monkeypatch.setattr("backend.infrastructure.model.factory.DeepSeekModel", Model)
    async with open_model(settings=cfg, events=MemoryEventSink(), role=role) as model:
        assert reads == ["deepseek"]
        assert model.settings.model.decision_protocol == ("explicit" if role == "main" else "text")
        assert model.settings.model.thinking_mode == (mode if role == "main" else "none")
        assert model.transport is None and model.settings.model.name == cfg.model.name
    assert cfg.model.thinking_mode == mode
    assert cfg.model.endpoint == "https://api.deepseek.com/chat/completions"


async def test_unknown_mode_is_not_silently_replaced(monkeypatch):
    cfg = load_settings()
    cfg.model.thinking_mode = "unknown"

    def unexpected(_):
        raise AssertionError("must not read credentials")

    monkeypatch.setattr("backend.infrastructure.model.factory.load_key", unexpected)
    with pytest.raises(PortError) as caught:
        async with open_model(settings=cfg, events=MemoryEventSink(), role="main"):
            pass
    assert caught.value.error.code == "model_profile_invalid"


def followup(run_id):
    r = model_request()
    r.run_id = run_id
    r.input.perceived.items[0].body["goal"] = run_id
    r.input.perceived.items += [
        ContextItem(
            id="call-record",
            type="tool_call",
            source="reasoning",
            kind="proposal",
            role="assistant",
            body={
                "calls": [
                    {"action_id": "same-call", "tool_name": "lookup", "arguments": {}}
                ]
            },
        ),
        ContextItem(
            id="result-record",
            type="tool_result",
            source="action",
            kind="result",
            role="tool",
            links=[ContextLink(relation="responds_to", target_id="call-record")],
            body={
                "action_id": "same-call",
                "tool_name": "lookup",
                "result": {"ok": True},
            },
        ),
    ]
    return r


async def test_concurrent_runs_with_same_provider_call_id_keep_own_continuation():
    cfg = relay_settings()
    cfg.model.endpoint = "https://relay.test/v1/responses"
    cfg.model.allowed_hosts = ["relay.test"]
    relay = RelayConfig(cfg.model.endpoint, "test", "low", SecretStr("synthetic"))

    async def respond(request):
        body = json.loads(request.content)
        run_id = next(m["content"] for m in body["input"] if m.get("role") == "user")
        await asyncio.sleep(0)
        if any(i.get("type") == "function_call_output" for i in body["input"]):
            assert [
                i["encrypted_content"]
                for i in body["input"]
                if i.get("type") == "reasoning"
            ] == [run_id]
            output = [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ]
        else:
            output = [
                {
                    "type": "reasoning",
                    "id": "rs",
                    "summary": [],
                    "encrypted_content": run_id,
                },
                {
                    "type": "function_call",
                    "call_id": "same-call",
                    "name": "lookup",
                    "arguments": "{}",
                },
            ]
        return httpx.Response(200, json={"status": "completed", "output": output})

    transport = ResponsesTransport(
        relay, transport=httpx.MockTransport(respond), max_cached_runs=4
    )
    async with ResponsesModel(
        cfg, relay.key, MemoryEventSink(), transport=transport
    ) as model:
        first = []
        for run_id in ["run-a", "run-b"]:
            r = model_request()
            r.run_id = run_id
            r.input.perceived.items[0].body["goal"] = run_id
            first.append(r)
        await asyncio.gather(*(model.decide(r) for r in first))
        assert set(transport.contexts) == {"run-a", "run-b"}
        await asyncio.gather(
            *(model.decide(followup(run_id)) for run_id in ["run-a", "run-b"])
        )
        assert transport.contexts == {} and transport.calls == []


async def test_cancelled_response_releases_run_state():
    cfg = relay_settings()
    cfg.model.endpoint = "https://relay.test/v1/responses"
    cfg.model.allowed_hosts = ["relay.test"]
    relay = RelayConfig(cfg.model.endpoint, "test", "low", SecretStr("synthetic"))

    async def cancelled(_):
        raise asyncio.CancelledError

    transport = ResponsesTransport(relay, transport=httpx.MockTransport(cancelled))
    transport.contexts["test"] = {"old": []}
    async with ResponsesModel(
        cfg, relay.key, MemoryEventSink(), transport=transport
    ) as model:
        with pytest.raises(asyncio.CancelledError):
            await model.decide(model_request())
        assert "test" not in transport.contexts


async def test_relay_streaming_fallback_does_not_fake_deltas():
    cfg = relay_settings()
    cfg.model.endpoint = "https://relay.test/v1/responses"
    cfg.model.allowed_hosts = ["relay.test"]
    relay = RelayConfig(cfg.model.endpoint, "test", "low", SecretStr("synthetic"))

    async def respond(_):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "完整回答"}],
                    }
                ],
            },
        )

    transport = ResponsesTransport(relay, transport=httpx.MockTransport(respond))
    async with ResponsesModel(
        cfg, relay.key, MemoryEventSink(), transport=transport
    ) as model:
        deltas: list[str] = []

        async def collect(piece: str) -> None:
            deltas.append(piece)

        decision = await model.decide_stream(model_request(), collect)

    assert decision.answer == "完整回答"
    assert deltas == []


def test_abandoned_tool_runs_have_bounded_continuation_storage():
    relay = RelayConfig(
        "https://relay.test/v1/responses", "test", "low", SecretStr("synthetic")
    )
    transport = ResponsesTransport(relay, max_cached_runs=2)
    for run_id in ["a", "b", "c"]:
        token = transport.scope.set(run_id)
        transport.reasoning_for_call["call"] = []
        transport.scope.reset(token)
    assert list(transport.contexts) == ["b", "c"]
