import json
from uuid import uuid4

import pytest

from backend.bootstrap import build_harness
from backend.config.settings import load_key, load_settings
from backend.contracts.events.streaming import visible_text
from backend.contracts.modules.loop import RunInput
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.modules.perception.service import TextPerception
from backend.tests.support import FixtureLookup

pytestmark = pytest.mark.live


@pytest.mark.parametrize("with_tool", [False, True])
async def test_real_model_loop(with_tool):
    settings = load_settings()
    sink = MemoryEventSink()
    receipt = uuid4().hex
    fixture = FixtureLookup(receipt)
    text = (
        "请调用 lookup_test_record 查询 record_id 为 sample 的测试记录，逐字给出记录中 receipt 的值。不要猜测。"
        if with_tool
        else "用一句中文说明 Python 列表与元组在可修改性上的区别。"
    )
    tools = {fixture.spec.name: (fixture.spec, fixture)} if with_tool else None
    contexts = []

    class ObservedPerception(TextPerception):
        async def handle(self, request):
            response = await super().handle(request)
            if response.status == "ok":
                contexts.append(response.output.model_copy(deep=True))
            return response

    async with DeepSeekModel(settings, load_key(settings), sink) as model:
        harness = build_harness(
            model=model, settings=settings, events=sink, streams=sink, tools=tools
        )
        harness.modules["perception"] = ObservedPerception(settings, model)
        result = await harness.run(RunInput(text=text))
    assert result.status == "completed", result.model_dump()
    assert result.answer and result.answer.strip()
    deltas = [event.text or "" for event in sink.stream_events if event.type == "delta"]
    assert deltas and visible_text(sink.stream_events) == result.answer
    calls = [event for event in sink.events if event.phase == "model"]
    if with_tool:
        assert len(fixture.calls) == 1 and len(calls) >= 2
        assert receipt in result.answer
        assert len(contexts) >= 2
        items = contexts[1].items
        assert (
            sum(
                item.source == "action"
                and item.type == "tool_result"
                and item.body["result"]["receipt"] == receipt
                for item in items
            )
            == 1
        )
        assert any(item.source == "reflection" for item in items)
        assert sum(item.id == "current-user" for item in items) == 1
    else:
        assert fixture.calls == []
        assert "列表" in result.answer and "元组" in result.answer
    print(
        json.dumps(
            {
                "case": "tool_loop" if with_tool else "direct_answer",
                "status": result.status,
                "seconds": round(result.elapsed_seconds, 3),
                "model_calls": len(calls),
                "perception_calls": len(contexts),
                "tokens": sum(e.total_tokens or 0 for e in calls),
                "tokens_complete": all(e.total_tokens is not None for e in calls),
            },
            ensure_ascii=False,
        )
    )
