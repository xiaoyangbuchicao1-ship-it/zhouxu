"""Current production Responses transport: budget, protocol and secret boundaries."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.config.relay import RelayConfig, read_config
from backend.infrastructure.model.budget import ModelBudget
from backend.infrastructure.model.responses import ResponsesTransport
from backend.tests.support import relay_settings


def config():
    return RelayConfig(
        "https://example.test/v1/responses",
        "test-model",
        "high",
        SecretStr("synthetic"),
    )


def make_transport(*, transport=None):
    selected = config()
    adapter = ResponsesTransport(selected, transport=transport, collect_metrics=True)
    settings = relay_settings(selected.model)
    settings.model.endpoint = selected.endpoint
    adapter.budget = ModelBudget(settings)
    return adapter


def packet():
    return {
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "data"},
        ],
        "max_tokens": 1200,
    }


def test_translation_preserves_roles_tools_pairs_and_json_review():
    adapter = make_transport()
    data = packet()
    data["messages"] += [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call", "function": {"name": "lookup", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call", "content": "actual result"},
    ]
    data["tools"] = [
        {
            "type": "function",
            "function": {"name": "lookup", "parameters": {"type": "object"}},
        }
    ]
    data["response_format"] = {"type": "json_object"}
    translated = adapter.translate(data)
    assert translated["input"][:2] == data["messages"][:2]
    assert (
        translated["input"][2]["call_id"] == translated["input"][3]["call_id"] == "call"
    )
    assert translated["tools"][0]["strict"] is False
    assert translated["text"]["format"] == {"type": "json_object"}
    assert "thinking" not in translated and "temperature" not in translated
    assert translated["store"] is False and "previous_response_id" not in translated


def test_translation_preserves_explicit_required_tool_selection():
    adapter = make_transport()
    data = packet()
    data["tools"] = [{"type": "function", "function": {"name": "deliver_response", "parameters": {"type": "object"}}}]
    data["tool_choice"] = "required"
    assert adapter.translate(data)["tool_choice"] == "required"


@pytest.mark.parametrize(
    "status,finish", [("completed", "stop"), ("incomplete", "length")]
)
def test_incomplete_output_never_looks_complete(status, finish):
    adapter = make_transport()
    result = adapter.convert(
        {
            "status": status,
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "answer"}],
                }
            ],
        }
    )
    assert result["choices"][0]["finish_reason"] == finish


def test_reasoning_continuation_is_kept_in_memory_and_call_ids_are_preserved():
    adapter = make_transport()
    reasoning = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "synthetic-encrypted",
    }
    result = adapter.convert(
        {
            "status": "completed",
            "output": [
                reasoning,
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
            ],
        }
    )
    message = result["choices"][0]["message"]
    p = packet()
    p["messages"] += [
        message,
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]
    translated = adapter.translate(p)
    assert translated["input"][2] == reasoning
    assert translated["input"][3]["call_id"] == "call_1"
    assert adapter.calls == []
    adapter.reset_context()
    assert adapter.reasoning_for_call == {}


async def test_request_uses_only_selected_endpoint_and_does_not_echo_error_body():
    seen = []

    def respond(request):
        seen.append(request)
        assert request.url == httpx.URL(config().endpoint)
        return httpx.Response(401, json={"error": "private gateway detail"})

    transport = make_transport(transport=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(
            config().endpoint,
            json=packet(),
            headers={"Authorization": "Bearer synthetic"},
        )
        assert "private gateway detail" not in response.text
        with pytest.raises(ValueError, match="target_mismatch"):
            await client.post(
                "https://other.test/responses",
                json=packet(),
                headers={"Authorization": "Bearer synthetic"},
            )
    assert len(seen) == 1 and "Authorization" not in json.dumps(transport.calls)


async def test_response_usage_and_function_call_reach_existing_adapter():
    def respond(request):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": "{}",
                    }
                ],
                "usage": {
                    "total_tokens": 31,
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
            },
        )

    transport = make_transport(transport=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        r = await client.post(
            config().endpoint,
            json=packet(),
            headers={"Authorization": "Bearer synthetic"},
        )
    assert r.json()["choices"][0]["message"]["tool_calls"][0]["id"] == "call_1"
    assert r.json()["usage"]["total_tokens"] == 31
    assert transport.calls[0]["reasoning_tokens"] == 5


def test_config_is_data_and_secret_repr_is_redacted(tmp_path):
    p = tmp_path / "config.txt"
    p.write_text(
        'sk-synthetic-test\nmodel="test-model"\nmodel_reasoning_effort="high"\nbase_url="https://example.test/v1"\nwire_api="responses"\n',
        encoding="utf-8",
    )
    value = read_config(p)
    assert value.endpoint == "https://example.test/v1/responses"
    assert "sk-synthetic-test" not in repr(value)
