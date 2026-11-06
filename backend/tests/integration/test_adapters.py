import json

import httpx
import pytest
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import (
    ActionResult,
    PerceivedContext,
    ReasoningInput,
    ReflectionResult,
    RunInput,
)
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse, PortError
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.interfaces.api.app import create_app
from backend.tests.support import FixtureLookup


def request():
    return ModuleRequest(
        run_id="test",
        call_id="model",
        input=ReasoningInput(
            perceived=PerceivedContext(
                run_id="test",
                items=[
                    ContextItem(
                        id="current-user",
                        type="message",
                        source="user",
                        kind="user.request",
                        role="user",
                        body={"goal": "test", "facts": {}, "constraints": []},
                    )
                ],
                assembly=ContextAssembly(
                    budget_chars=24000,
                    used_chars=100,
                ),
            )
        ),
    )


async def test_health_shell_does_not_require_credentials():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        assert (
            await client.get(load_settings().application["health_path"])
        ).json() == {"status": "ok"}
        assert (await client.post("/api/chat", json={})).status_code == 404


async def test_model_accepts_missing_optional_usage():
    sink = MemoryEventSink()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "answer"}}]
            },
        )
    )
    async with DeepSeekModel(
        load_settings(), SecretStr("test-only"), sink, transport=transport
    ) as model:
        assert (await model.decide(request())).answer == "answer"
    assert sink.events[0].total_tokens is None


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "model_rejected", False),
        (429, "model_unavailable", True),
        (503, "model_unavailable", True),
        (302, "model_rejected", False),
    ],
)
async def test_model_errors_and_redirects_never_expose_response_body(
    status, code, retryable
):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            status,
            text="sensitive-body",
            headers={"Location": "https://unrelated.invalid"},
        )
    )
    async with DeepSeekModel(
        load_settings(), SecretStr("test-only"), MemoryEventSink(), transport=transport
    ) as model:
        with pytest.raises(PortError) as caught:
            await model.decide(request())
    assert caught.value.error.code == code and caught.value.error.retryable == retryable
    assert "sensitive-body" not in caught.value.error.message


async def test_truncated_model_answer_is_not_delivered_as_complete():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"content": "partial"}}
                ]
            },
        )
    )
    async with DeepSeekModel(
        load_settings(), SecretStr("test-only"), MemoryEventSink(), transport=transport
    ) as model:
        decision = await model.decide(request())
    assert decision.incomplete and decision.finish_reason == "length"
    assert decision.answer == "partial"


def test_unapproved_model_host_rejected_before_network():
    settings = load_settings()
    settings.model.endpoint = "https://unrelated.invalid/chat"
    with pytest.raises(PortError) as caught:
        DeepSeekModel(settings, SecretStr("test-only"), MemoryEventSink())
    assert caught.value.error.code == "invalid_endpoint"


async def test_organized_context_is_encoded_once_with_complete_tool_pairs():
    packets = []

    class Lookup(FixtureLookup):
        async def execute(self, request):
            return ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                result={"receipt": request.arguments["record_id"]},
            )

    class Review:
        async def handle(self, request):
            return ModuleResponse(
                status="ok",
                output=ReflectionResult(
                    decision="continue"
                    if request.input.proposed.kind == "action"
                    else "complete",
                    issues=["review-data-without-summary"],
                ),
            )

    def respond(request):
        packet = json.loads(request.content)
        packets.append(packet)
        if len(packets) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": name,
                                        "type": "function",
                                        "function": {
                                            "name": "lookup_test_record",
                                            "arguments": json.dumps(
                                                {"record_id": name}
                                            ),
                                        },
                                    }
                                    for name in ("first-receipt", "second-receipt")
                                ],
                            },
                        }
                    ]
                },
            )
        inventory = next(m for m in packet["messages"] if m.get("name") == "context_inventory")
        assert json.loads(inventory["content"])["observations_count"] == 2
        messages = [m for m in packet["messages"] if m.get("name") != "context_inventory"]
        assert [message["role"] for message in messages] == [
            "system",
            "user",
            "user",
            "assistant",
            "tool",
            "tool",
            "user",
        ]
        state = json.loads(messages[1]["content"])
        assert state["source"] == "memory"
        assert len(state["steps"]) == 2 and all(step["status"] == "completed" for step in state["steps"])
        question, _, metadata = messages[2]["content"].partition("\n")
        assert question == "unique-current-question"
        task = json.loads(metadata)
        assert "records" not in task and task["facts"] == {"claim": "user-supplied"}
        requested_ids = [call["id"] for call in messages[3]["tool_calls"]]
        results = messages[4:6]
        assert requested_ids == [message["tool_call_id"] for message in results]
        assert [
            json.loads(message["content"])["receipt"] for message in results
        ] == requested_ids
        review = json.loads(messages[6]["content"])
        assert review["source"] == "reflection" and review["updates"]["review"][
            "issues"
        ] == ["review-data-without-summary"]
        assert review["summary"] == ""
        assert (
            sum("unique-current-question" in (m.get("content") or "") for m in messages)
            == 1
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "both received"}}
                ]
            },
        )

    tool = Lookup("unused")
    settings, events = load_settings(), MemoryEventSink()
    async with DeepSeekModel(
        settings, SecretStr("test-only"), events, transport=httpx.MockTransport(respond)
    ) as model:
        harness = build_harness(
            model=model,
            settings=settings,
            events=events,
            tools={tool.spec.name: (tool.spec, tool)},
        )
        harness.modules["reflection"] = Review()
        result = await harness.run(
            RunInput(text="unique-current-question", facts={"claim": "user-supplied"})
        )
    assert result.status == "completed" and result.answer == "both received"
    assert len(packets) == 2
