"""Context visibility and native model protocol regressions, without network."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from backend.config.settings import load_settings
from backend.contracts.modules.loop import (
    ActionResult,
    RunInput,
)
from backend.contracts.modules.perception import ContextItem
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.modules.perception.inventory import organize_context
from backend.modules.perception.service import TextPerception
from backend.tests.integration.test_adapters import request as model_request
from backend.tests.support import context_model
from backend.tests.unit.test_perception import request as perception_request


def resource(*, coverage=None, version="v1", units=None):
    return ContextItem(
        id="material:doc",
        type="resource",
        source="user.material",
        kind="material.reading_view",
        role="user",
        body={
            "material_id": "doc",
            "name": "记录.txt",
            "parse_version": version,
            "parse_status": "available",
            "complete": True,
            "text_char_count": 6,
            "units": units
            if units is not None
            else [{"unit_id": "u", "text": "abcdef"}],
            "coverage": coverage
            if coverage is not None
            else [{"unit_id": "u", "char_start": 0, "char_end": 6}],
            "truncated": False,
        },
    )


async def perceive(*records, candidates=None):
    settings = load_settings()
    return await TextPerception(settings, context_model(settings)).handle(
        perception_request(*records, candidates=candidates or [resource()])
    )


async def test_inventory_describes_actual_source_and_is_in_complete_request_budget():
    response = await perceive()
    assert response.status == "ok"
    view = response.output
    index = next(i for i in view.items if i.id == "context-inventory")
    material = index.body["materials"][0]
    assert material["full_text_visible"] is True
    assert material["visible_text_chars"] == 6
    assert index.body["task_completion"] == "not_asserted"
    settings = load_settings()
    estimate = context_model(settings).estimate_context(view.items, [], run_id="run")
    assert estimate.tokens == view.assembly.used_tokens


async def test_inventory_does_not_count_empty_reference_or_ledger_as_visible_text():
    source = resource(units=[], coverage=[])
    source.body["body_in_tool_history"] = True
    response = await perceive(candidates=[source])
    index = next(i for i in response.output.items if i.id == "context-inventory")
    assert index.body["materials"][0]["visible_text_chars"] == 0
    assert index.body["materials"][0]["full_text_visible"] is False


async def test_native_plain_answer_is_a_valid_streamed_delivery():
    settings = load_settings()
    settings.model.decision_protocol = "explicit"
    chunks = [
        {"choices": [{"delta": {"content": "已有"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "足够信息。"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    payload = (
        "\n\n".join("data: " + json.dumps(c, ensure_ascii=False) for c in chunks)
        + "\n\ndata: [DONE]\n\n"
    )
    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=payload.encode(),
            )
        ),
    ) as model:
        received = []

        async def delta(text):
            received.append(text)

        result = await model.decide_stream(model_request(), delta)
    assert result.kind == "answer" and result.answer == "已有足够信息。"
    assert "".join(received) == result.answer and len(received) > 1


@pytest.mark.parametrize("mode", ["none", "low", "high"])
async def test_three_modes_use_supported_tool_choice_and_explicit_effort(mode):
    settings = load_settings()
    settings.model.thinking_mode = mode
    async with DeepSeekModel(
        settings, SecretStr("offline"), MemoryEventSink()
    ) as model:
        packet = model._payload(model_request())
    assert packet["tool_choice"] == "auto"
    assert packet["thinking"]["type"] == ("disabled" if mode == "none" else "enabled")
    assert packet.get("reasoning_effort") == (None if mode == "none" else mode)


async def test_reasoning_only_truncation_is_not_invalid_response_or_retryable():
    settings = load_settings()
    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {
                                "content": None,
                                "reasoning_content": "private fixture",
                            },
                        }
                    ]
                },
            )
        ),
    ) as model:
        from backend.contracts.schemas.core import PortError

        with pytest.raises(PortError) as caught:
            await model.decide(model_request())
    assert caught.value.error.code == "model_output_incomplete"
    assert not caught.value.error.retryable


def test_coverage_union_does_not_hide_gaps_or_mix_versions():
    settings = load_settings()
    source = resource(
        units=[{"unit_id": "u", "text": "ab"}],
        coverage=[{"unit_id": "u", "char_start": 0, "char_end": 2}],
    )
    user = ContextItem(
        id="current-user", type="message", source="user", body={"goal": "整理这份记录"}
    )
    receipt = ContextItem(
        id="receipt",
        type="tool_result",
        source="action",
        role="tool",
        body={
            "tool_name": "read_material",
            "action_id": "read",
            "result": {
                "material_id": "doc",
                "parse_version": "v1",
                "units": [{"unit_id": "u", "text": "def"}],
                "coverage": [{"unit_id": "u", "char_start": 3, "char_end": 6}],
            },
        },
    )

    def view():
        return organize_context([user, source, receipt], settings.context_policy)[
            -1
        ].body["materials"][0]

    assert view()["visible_text_chars"] == 5 and not view()["full_text_visible"]
    receipt.body["result"]["coverage"][0]["char_start"] = 1
    receipt.body["result"]["units"][0]["text"] = "bcdef"
    assert view()["visible_text_chars"] == 6 and view()["full_text_visible"]
    receipt.body["result"]["parse_version"] = "v0"
    assert view()["visible_text_chars"] == 2 and not view()["full_text_visible"]


async def test_budget_projection_recomputes_index_instead_of_claiming_full_body():
    from backend.contracts.model_context import DeploymentLimits

    settings = load_settings()
    settings.deployment_limits[settings.model.name] = DeploymentLimits(
        total_tokens=32000
    )
    source = resource(
        units=[{"unit_id": "u", "text": "x" * 100000}],
        coverage=[{"unit_id": "u", "char_start": 0, "char_end": 100000}],
    )
    source.body["text_char_count"] = 100000
    response = await TextPerception(settings, context_model(settings)).handle(
        perception_request(candidates=[source])
    )
    assert response.status == "ok" and not response.output.needs_compaction
    index = response.output.items[-1].body["materials"][0]
    assert index["visible_text_chars"] == 0 and not index["full_text_visible"]
    assert (
        response.output.assembly.used_tokens <= response.output.assembly.budget.warning
    )


def followup(decision, *, run_id="test"):
    request = model_request()
    request.run_id = request.input.perceived.run_id = run_id
    request.call_id = "second"
    action = decision.action_requests[0]
    request.input.perceived.items += [
        ContextItem(
            id="proposal",
            type="tool_call",
            source="reasoning",
            role="assistant",
            body={"calls": [action.model_dump()]},
        ),
        ContextItem(
            id="result",
            type="tool_result",
            source="action",
            role="tool",
            links=[{"relation": "responds_to", "target_id": "proposal"}],
            body=ActionResult(
                tool_name=action.tool_name,
                action_id=action.action_id,
                result={"value": "14:30"},
            ).model_dump(),
        ),
    ]
    return request


async def test_tool_reasoning_is_budgeted_private_and_restorable_without_replaying_tool():
    settings = load_settings()
    settings.model.thinking_mode = "low"
    private = "opaque-protocol-fixture" * 100

    def respond(request):
        packet = json.loads(request.content)
        if any(m["role"] == "tool" for m in packet["messages"]):
            assistant = next(m for m in packet["messages"] if m.get("tool_calls"))
            assert assistant["reasoning_content"] == private
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": "14:30",
                                "reasoning_content": "opaque-final",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "reasoning_content": private,
                            "tool_calls": [
                                {
                                    "id": "same-id",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": "{}"},
                                }
                            ],
                        },
                    }
                ]
            },
        )

    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(respond),
    ) as first:
        decision = await first.decide(model_request())
        next_request = followup(decision)
        private_state = first.export_run_state("test")
        assert "reasoning_content" not in json.dumps(first.trace_messages(next_request))
        packet = first._payload(next_request)
        full_size = first.budget.estimate(packet).tokens
        packet["messages"] = first.trace_messages(next_request)
        assert full_size > first.budget.estimate(packet).tokens + len(private)
    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(respond),
    ) as second:
        second.restore_run_state("test", private_state)
        assert (await second.decide(next_request)).answer == "14:30"
        second.close_run("test")
        assert second.export_run_state("test") == {}


async def test_wait_checkpoint_keeps_protocol_state_out_of_public_result():
    from backend.bootstrap import build_harness

    settings = load_settings()
    settings.model.thinking_mode = "low"
    packets = []

    def respond(request):
        packet = json.loads(request.content)
        packets.append(packet)
        if len(packets) == 1:
            message = {
                "content": None,
                "reasoning_content": "private-wait-frame",
                "tool_calls": [
                    {
                        "id": "wait",
                        "type": "function",
                        "function": {
                            "name": "request_user_input",
                            "arguments": '{"questions":["实收多少件？"]}',
                        },
                    }
                ],
            }
        else:
            assert any(
                m.get("reasoning_content") == "private-wait-frame"
                for m in packet["messages"]
            )
            message = {
                "content": "实收82件。",
                "reasoning_content": "private-final-frame",
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls" if len(packets) == 1 else "stop",
                        "message": message,
                    }
                ]
            },
        )

    # The same production executor may be recreated after process restart.
    async def execute(recovery=None):
        events = MemoryEventSink()
        async with DeepSeekModel(
            settings,
            SecretStr("offline"),
            events,
            transport=httpx.MockTransport(respond),
        ) as model:
            run = await build_harness(
                model=model, settings=settings, events=events, streams=events
            ).run(
                RunInput(
                    text="核对实收件数",
                    facts={"user_answers": {"a": "82件"}} if recovery else {},
                ),
                run_id="resume",
                session_id="s",
                recovery=recovery,
            )
            assert model.export_run_state("resume") == {}
            assert "private-wait-frame" not in run.model_dump_json()
            assert "private-wait-frame" not in json.dumps(
                [e.model_dump(mode="json") for e in events.stream_events],
                ensure_ascii=False,
            )
            return run

    first = await execute()
    assert first.status == "needs_input" and first.execution_state["model_continuation"]
    second = await execute(first.execution_state)
    assert second.status == "completed" and second.answer == "实收82件。"


async def test_compaction_has_its_own_output_budget_and_disables_thinking():
    settings = load_settings()
    settings.model.thinking_mode = "high"
    async with DeepSeekModel(
        settings, SecretStr("offline"), MemoryEventSink()
    ) as model:
        request = model_request()
        request.input.purpose = "compaction"
        packet = model._payload(request)
    assert packet["max_tokens"] == settings.context_policy.summary_output_tokens
    assert packet["thinking"]["type"] == "disabled" and "reasoning_effort" not in packet


async def test_concurrent_runs_with_identical_tool_ids_cannot_share_reasoning():
    settings = load_settings()
    settings.model.thinking_mode = "low"

    async def respond(request):
        packet = json.loads(request.content)
        goal = next(
            m["content"]
            for m in packet["messages"]
            if m.get("name") == "current_user_request"
        )
        await asyncio.sleep(0)
        if any(m["role"] == "tool" for m in packet["messages"]):
            assert (
                next(
                    m["reasoning_content"]
                    for m in packet["messages"]
                    if m.get("tool_calls")
                )
                == "private-" + goal
            )
            message = {"content": "done"}
        else:
            message = {
                "content": None,
                "reasoning_content": "private-" + goal,
                "tool_calls": [
                    {
                        "id": "same-provider-id",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls"
                        if message.get("tool_calls")
                        else "stop",
                        "message": message,
                    }
                ]
            },
        )

    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(respond),
    ) as model:

        async def execute(name):
            request = model_request()
            request.run_id = request.input.perceived.run_id = name
            request.input.perceived.items[0].body["goal"] = name
            decision = await model.decide(request)
            request = followup(decision, run_id=name)
            request.input.perceived.items[0].body["goal"] = name
            assert (await model.decide(request)).answer == "done"

        await asyncio.gather(execute("run-a"), execute("run-b"))


@pytest.mark.parametrize("mode", ["none", "low", "high"])
async def test_streaming_thinking_is_not_a_public_delta(mode):
    settings = load_settings()
    settings.model.thinking_mode = mode
    values = [
        {
            "choices": [
                {
                    "delta": {"reasoning_content": "private-stream-fixture"},
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"delta": {"content": "实际"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "结果"}, "finish_reason": "stop"}]},
    ]
    encoded = (
        "\n\n".join("data: " + json.dumps(x, ensure_ascii=False) for x in values)
        + "\n\ndata: [DONE]\n\n"
    )
    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=encoded.encode(),
            )
        ),
    ) as model:
        parts = []

        async def delta(text):
            parts.append(text)

        result = await model.decide_stream(model_request(), delta)
    assert result.answer == "实际结果" and "".join(parts) == "实际结果"
