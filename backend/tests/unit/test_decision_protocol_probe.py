"""Protocol experiments must preserve text and never confuse partial with final."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import RunInput
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.benchmarks.decision_protocol_probe import (
    CONFIG,
    ArgumentText,
    ProbeModel,
)


@pytest.mark.parametrize("ascii_escaped", [False, True])
@pytest.mark.parametrize(
    "text", ["海风与青帆😀", '第一行\n第二行："引号"与\\路径', "{不应成为协议}，末尾"]
)
def test_partial_json_text_is_monotonic_across_every_character_boundary(
    ascii_escaped, text
):
    encoded = json.dumps({"text": text}, ensure_ascii=ascii_escaped)
    reader, pieces = ArgumentText(), []
    for end in range(1, len(encoded) + 1):
        pieces.append(reader.push(encoded[:end]))
    assert "".join(pieces) == text


def wire(events):
    return (
        "\n\n".join("data: " + json.dumps(e, ensure_ascii=False) for e in events)
        + "\n\ndata: [DONE]\n\n"
    ).encode()


def delta(tool, args, *, finish=None):
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call",
                            "function": {"name": tool, "arguments": args},
                        }
                    ]
                },
                "finish_reason": finish,
            }
        ]
    }


async def test_real_encoder_projects_tool_argument_stream_to_plain_text():
    settings, sink = load_settings(), MemoryEventSink()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected = '您好，\n请核对"海风"的资料😀。'
    encoded = json.dumps({"text": expected}, ensure_ascii=True)

    def respond(request):
        packet = json.loads(request.content)
        assert packet["tool_choice"] == "required"
        assert config["finish_tool"] in [t["function"]["name"] for t in packet["tools"]]
        events = [delta(config["finish_tool"], "")]
        events += [delta("", encoded[i : i + 3]) for i in range(0, len(encoded), 3)]
        events += [
            {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"total_tokens": 123},
            }
        ]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=wire(events)
        )

    async with ProbeModel(
        settings,
        SecretStr("offline"),
        sink,
        mode="closed",
        config=config,
        transport=httpx.MockTransport(respond),
    ) as model:
        result = await build_harness(
            model=model, settings=settings, events=sink, streams=sink
        ).run(RunInput(text="draft"))
        chunks = model.calls[0]["visible_chunks"]
    assert result.status == "completed" and result.answer == expected
    assert len(chunks) > 1 and "".join(chunks) == expected
    assert not any('"text":' in p or config["finish_tool"] in p for p in chunks)


async def test_explicit_wait_uses_existing_needs_input_without_streaming_json():
    settings, sink = load_settings(), MemoryEventSink()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    content = wire(
        [
            delta(
                settings.model.clarification_tool,
                json.dumps({"questions": ["请提供原始记录。"]}),
                finish="tool_calls",
            )
        ]
    )
    async with ProbeModel(
        settings,
        SecretStr("offline"),
        sink,
        mode="closed",
        config=config,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        ),
    ) as model:
        result = await build_harness(
            model=model, settings=settings, events=sink, streams=sink
        ).run(RunInput(text="verify"))
    assert result.status == "needs_input" and result.missing_inputs == [
        "请提供原始记录。"
    ]
    assert not any(e.type == "delta" for e in sink.stream_events)


async def test_unfinished_argument_text_is_preserved_but_not_marked_complete():
    settings, sink = load_settings(), MemoryEventSink()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    content = wire(
        [
            delta(config["finish_tool"], '{"text":"已经生成的部分'),
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        ]
    )
    async with ProbeModel(
        settings,
        SecretStr("offline"),
        sink,
        mode="closed",
        config=config,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        ),
    ) as model:
        result = await build_harness(
            model=model, settings=settings, events=sink, streams=sink
        ).run(RunInput(text="write"))
    assert result.status == "failed" and result.answer == "已经生成的部分"
    assert not any(e.type == "done" for e in sink.stream_events)


@pytest.mark.parametrize("kind", ["plain", "mixed"])
async def test_closed_protocol_does_not_accept_implicit_or_mixed_completion(kind):
    settings, sink = load_settings(), MemoryEventSink()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    message = {"content": "请提供信息"}
    if kind == "mixed":
        message = {
            "tool_calls": [
                {
                    "id": "finish",
                    "function": {
                        "name": config["finish_tool"],
                        "arguments": '{"text":"done"}',
                    },
                },
                {
                    "id": "read",
                    "function": {"name": "read_material", "arguments": "{}"},
                },
            ]
        }
    async with ProbeModel(
        settings, SecretStr("offline"), sink, mode="closed", config=config
    ) as model:
        with pytest.raises(ValueError):
            model.parse_decision(message, "tool_calls" if kind == "mixed" else "stop")


async def test_exact_live_response_with_one_question_string_can_be_normalized():
    settings, sink = load_settings(), MemoryEventSink()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    question = "请提供能证明该笔货损赔款是否到账的原始凭证，例如银行流水（显示入账记录）或付款方出具的付款回单/到账通知。您可提供截图或文件内容，我据此帮您核对。"
    message = {
        "content": None,
        "tool_calls": [
            {
                "id": "recorded-call",
                "type": "function",
                "function": {
                    "name": settings.model.clarification_tool,
                    "arguments": json.dumps(
                        {"questions": question}, ensure_ascii=False
                    ),
                },
            }
        ],
    }
    from backend.infrastructure.model.deepseek import DeepSeekModel

    async with ProbeModel(
        settings, SecretStr("offline"), sink, mode="closed", config=config
    ) as model:
        baseline = DeepSeekModel._decision_from_message(model, message, "tool_calls")
        assert baseline.kind == "needs_input" and baseline.missing_inputs == [question]
        decision = model.parse_decision(message, "tool_calls")
    assert decision.kind == "needs_input" and decision.missing_inputs == [question]


@pytest.mark.parametrize("questions", [None, 42, {"text": "question"}, [], [1], [""]])
async def test_structural_tolerance_does_not_guess_other_question_shapes(questions):
    settings, sink = load_settings(), MemoryEventSink()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    message = {
        "tool_calls": [
            {
                "id": "q",
                "function": {
                    "name": settings.model.clarification_tool,
                    "arguments": json.dumps({"questions": questions}),
                },
            }
        ]
    }
    async with ProbeModel(
        settings, SecretStr("offline"), sink, mode="closed", config=config
    ) as model:
        with pytest.raises(ValueError):
            model.parse_decision(message, "tool_calls")


async def test_truncated_wait_is_not_accepted_as_an_authoritative_decision():
    settings, sink = load_settings(), MemoryEventSink()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    message = {
        "tool_calls": [
            {
                "id": "q",
                "function": {
                    "name": settings.model.clarification_tool,
                    "arguments": json.dumps({"questions": ["请提供凭证。"]}),
                },
            }
        ]
    }
    async with ProbeModel(
        settings, SecretStr("offline"), sink, mode="closed", config=config
    ) as model:
        with pytest.raises(ValueError):
            model.parse_decision(message, "length")
