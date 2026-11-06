"""Production protocol tests: controls, streamed text and real Run resumption."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.events.streaming import visible_text
from backend.contracts.modules.loop import RunInput
from backend.contracts.schemas.core import PortError
from backend.harness.lifecycle.executor import HarnessRunExecutor
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.benchmarks.run_reasoning_action import ObservedModel
from backend.tests.integration.test_adapters import request as model_request
from backend.tests.unit.test_decision_protocol_probe import delta, wire


def explicit_settings():
    settings = load_settings()
    settings.model = settings.model.model_copy(
        update={
            "decision_protocol": "explicit",
            "delivery_tool": "deliver_response",
            "delivery_description": "交付本次用户需要的正文。",
        }
    )
    settings.prompts["decision_protocol"] = (
        "交付调用{delivery_tool}，等待调用{clarification_tool}。"
    )
    return settings


def stream_message(name, arguments, finish="tool_calls"):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=wire([delta(name, arguments, finish=finish)]),
    )


async def test_explicit_packet_uses_auto_and_finalization_has_only_controls():
    settings, events = explicit_settings(), MemoryEventSink()
    async with DeepSeekModel(settings, SecretStr("offline"), events) as model:
        request = model_request()
        packet = model._payload(request)
        assert packet["tool_choice"] == "auto"
        assert {t["function"]["name"] for t in packet["tools"]} == {
            settings.model.delivery_tool,
            settings.model.clarification_tool,
        }
        request.input.purpose = "finalization"
        assert len(model._payload(request)["tools"]) == 2
        request.input.purpose = "compaction"
        assert model._payload(request)["tools"] == []
        assert "tool_choice" not in model._payload(request)


async def test_explicit_delivery_streams_plain_text_without_an_action_or_extra_round():
    settings, events = explicit_settings(), MemoryEventSink()
    text = '您好，\n请核对"青屿"资料😀。'
    encoded = json.dumps({"text": text}, ensure_ascii=True)
    packets = []

    def respond(request):
        packets.append(json.loads(request.content))
        chunks = [delta(settings.model.delivery_tool, "")]
        chunks += [delta("", encoded[i : i + 3]) for i in range(0, len(encoded), 3)]
        chunks += [{"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=wire(chunks)
        )

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(respond)
    ) as model:
        run = await build_harness(
            model=model, settings=settings, events=events, streams=events
        ).run(RunInput(text="起草正文"))
    assert run.status == "completed" and run.answer == text
    assert len(packets) == 1 and run.execution_state["tool_results"] == []
    assert visible_text(events.stream_events) == text
    assert sum(e.type == "delta" for e in events.stream_events) > 1


@pytest.mark.parametrize(
    "single_string,question_count", [(True, 1), (False, 1), (False, 2)]
)
async def test_explicit_wait_and_resume_use_existing_scheduler_contract(
    single_string, question_count
):
    settings, events, packets = explicit_settings(), MemoryEventSink(), []

    def respond(request):
        packets.append(json.loads(request.content))
        if len(packets) == 1:
            questions = (
                "请提供原始回单。"
                if single_string
                else ["请提供原始回单。", "请说明金额。"][:question_count]
            )
            return stream_message(
                settings.model.clarification_tool,
                json.dumps({"questions": questions}, ensure_ascii=False),
            )
        assert "回单显示14:30到账" in json.dumps(packets[-1], ensure_ascii=False)
        return stream_message(
            settings.model.delivery_tool, '{"text":"回单显示14:30到账。"}'
        )

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(respond)
    ) as model:
        agent = build_harness(
            model=model, settings=settings, events=events, streams=events
        )
        scheduler = InMemoryRunScheduler(HarnessRunExecutor(agent))
        submitted = await scheduler.submit(
            session_id="protocol-session", payload=RunInput(text="核对到账时间")
        )
        await scheduler.wait_for_idle(session_id="protocol-session")
        waiting = await scheduler.get_run(
            session_id="protocol-session", run_id=submitted.run_id
        )
        assert (
            waiting.status == "waiting_user"
            and len(waiting.pending_request_ids) == question_count
        )
        for i, request_id in enumerate(waiting.pending_request_ids):
            await scheduler.resume(
                session_id="protocol-session",
                run_id=submitted.run_id,
                request_id=request_id,
                answer="回单显示14:30到账",
            )
            if i < question_count - 1:
                partial = await scheduler.get_run(
                    session_id="protocol-session", run_id=submitted.run_id
                )
                assert partial.status == "waiting_user" and len(packets) == 1
        await scheduler.wait_for_idle(session_id="protocol-session")
        completed = await scheduler.get_run(
            session_id="protocol-session", run_id=submitted.run_id
        )
    assert completed.status == "completed" and completed.run_id == submitted.run_id
    assert completed.output == "回单显示14:30到账。" and len(packets) == 2
    assert packets[-1]["messages"][-1].get("name") == "latest_user_response"
    assert "回单显示14:30到账" in packets[-1]["messages"][-1]["content"]


async def test_latest_user_projection_keeps_original_data_and_user_role():
    settings = explicit_settings()
    value = model_request()
    value.input.perceived.items[0].body["facts"] = {
        "pieces": 80,
        "user_answers": {"request-1": "补充：82件"},
    }
    original = value.model_dump()
    async with DeepSeekModel(
        settings, SecretStr("offline"), MemoryEventSink()
    ) as model:
        messages = model._messages(value)
    assert value.model_dump() == original
    assert (
        messages[-1]["role"] == "user"
        and messages[-1]["name"] == "latest_user_response"
    )
    assert "补充：82件" in messages[-1]["content"]
    assert (
        "user_answers" not in messages[1]["content"] and "80" in messages[1]["content"]
    )


@pytest.mark.parametrize("failure", ["length", "disconnect"])
async def test_unfinished_delivery_retains_visible_text_without_completing(failure):
    settings, events = explicit_settings(), MemoryEventSink()

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield wire(
                [delta(settings.model.delivery_tool, '{"text":"已取得部分正文')]
            ).split(b"data: [DONE]")[0]
            if failure == "disconnect":
                raise httpx.ReadError("synthetic disconnected stream")
            yield wire([{"choices": [{"delta": {}, "finish_reason": "length"}]}])

    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        events,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Chunks())),
    ) as model:
        run = await build_harness(
            model=model, settings=settings, events=events, streams=events
        ).run(RunInput(text="整理材料"))
    assert run.status == "failed" and run.answer == "已取得部分正文"
    assert not any(e.type == "done" for e in events.stream_events)


@pytest.mark.parametrize(
    "message",
    [
        {
            "tool_calls": [
                {
                    "id": "wait",
                    "function": {"name": "request_user_input", "arguments": "[]"},
                }
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "wait",
                    "function": {
                        "name": "request_user_input",
                        "arguments": '{"questions":42}',
                    },
                }
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "wait",
                    "function": {
                        "name": "request_user_input",
                        "arguments": '{"questions":[""]}',
                    },
                }
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "finish",
                    "function": {
                        "name": "deliver_response",
                        "arguments": '{"text":""}',
                    },
                }
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "finish",
                    "function": {
                        "name": "deliver_response",
                        "arguments": '{"text":"ok","extra":true}',
                    },
                }
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "finish",
                    "function": {
                        "name": "deliver_response",
                        "arguments": '{"text":"ok"}',
                    },
                },
                {
                    "id": "read",
                    "function": {"name": "read_material", "arguments": "{}"},
                },
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "wait",
                    "function": {
                        "name": "request_user_input",
                        "arguments": '{"questions":["which?"]}',
                    },
                },
                {
                    "id": "read",
                    "function": {"name": "read_material", "arguments": "{}"},
                },
            ]
        },
    ],
)
async def test_invalid_control_never_means_completed(message):
    settings = explicit_settings()
    async with DeepSeekModel(
        settings, SecretStr("offline"), MemoryEventSink()
    ) as model:
        with pytest.raises((ValueError, PortError)):
            model._decision_from_message(message, "stop")


@pytest.mark.live
async def test_paid_native_wait_and_resume_keep_one_run():
    settings, events = load_settings(), MemoryEventSink()
    settings.model.thinking_mode = "none"
    async with open_model(settings=settings, events=events, role="main") as model:
        observed = ObservedModel(model)
        agent = build_harness(
            model=observed,
            model_context=model,
            settings=settings,
            events=events,
            streams=events,
        )
        scheduler = InMemoryRunScheduler(HarnessRunExecutor(agent))
        submitted = await scheduler.submit(
            session_id="live-protocol-check",
            payload=RunInput(
                text="我那笔货损赔款实际到账了吗？我还没有提供流水、回单或付款记录。"
            ),
        )
        await scheduler.wait_for_idle(session_id="live-protocol-check")
        waiting = await scheduler.get_run(
            session_id="live-protocol-check", run_id=submitted.run_id
        )
        assert waiting.status == "waiting_user" and waiting.pending_request_ids
        for request_id in waiting.pending_request_ids:
            await scheduler.resume(
                session_id="live-protocol-check",
                run_id=submitted.run_id,
                request_id=request_id,
                answer="补充测试夹具的银行流水原文摘录：甲公司支付人民币100元，唯一对应款项ZX-17，已入账，到账时间14:30。约定金额100元。现在只根据这份摘录回答它记载的到账时间，简短即可。",
            )
        await scheduler.wait_for_idle(session_id="live-protocol-check")
        completed = await scheduler.get_run(
            session_id="live-protocol-check", run_id=submitted.run_id
        )
    evidence = {
        "run_id": completed.run_id,
        "waiting_status": waiting.status,
        "final_status": completed.status,
        "answer": completed.output,
        "same_run": completed.run_id == waiting.run_id,
        "initial_request_count": len(waiting.pending_request_ids),
        "calls": observed.calls,
        "pending_questions": [
            (
                await scheduler.get_input_request(
                    session_id="live-protocol-check", request_id=i
                )
            ).model_dump(mode="json")
            for i in completed.pending_request_ids
        ],
        "main_calls": sum(e.module == "model" for e in events.events),
        "tokens": sum(
            e.total_tokens or 0 for e in events.events if e.module == "model"
        ),
        "fixture": "synthetic bank excerpt; not a real payment verification",
    }
    print("LIVE_RECOVERY " + json.dumps(evidence, ensure_ascii=False))
    assert completed.run_id == submitted.run_id and completed.status == "completed"
    assert "14:30" in completed.output
