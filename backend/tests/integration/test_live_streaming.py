"""Paid verification of one real visible DeepSeek answer stream."""

import json
from time import perf_counter

import pytest

from backend.bootstrap import build_harness
from backend.config.settings import load_key, load_settings
from backend.contracts.events.streaming import visible_text
from backend.contracts.modules.loop import RunInput
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink

pytestmark = pytest.mark.live


class TimedSink(MemoryEventSink):
    def __init__(self):
        super().__init__()
        self.started = perf_counter()
        self.first_delta_seconds: float | None = None

    def emit_stream(self, event):
        super().emit_stream(event)
        if event.type == "delta" and self.first_delta_seconds is None:
            self.first_delta_seconds = perf_counter() - self.started


async def test_live_deepseek_streaming_answer():
    settings = load_settings()
    sink = TimedSink()
    started = perf_counter()
    async with DeepSeekModel(settings, load_key(settings), sink) as model:
        harness = build_harness(
            model=model,
            settings=settings,
            events=sink,
            streams=sink,
        )
        result = await harness.run(
            RunInput(text="用一句中文说明 Python 列表与元组在可修改性上的区别。")
        )
    elapsed = perf_counter() - started
    deltas = [event.text or "" for event in sink.stream_events if event.type == "delta"]
    assert result.status == "completed", result.model_dump()
    assert deltas and visible_text(sink.stream_events) == result.answer
    assert sink.first_delta_seconds is not None
    print(
        json.dumps(
            {
                "status": result.status,
                "delta_count": len(deltas),
                "first_delta_seconds": round(sink.first_delta_seconds, 3),
                "total_seconds": round(elapsed, 3),
                "answer_chars": len(result.answer or ""),
            },
            ensure_ascii=False,
        )
    )
