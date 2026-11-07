"""Offline tests for the visible answer stream contract."""

import json

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.application.maritime.citations import CitationStream
from backend.config.settings import load_settings
from backend.contracts.events.streaming import StreamEvent, visible_text
from backend.contracts.modules.loop import DecisionProposal, RunInput
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus
from backend.infrastructure.tools.maritime import CorpusTool
from backend.contracts.schemas.core import Error
from backend.interfaces.api.app import _public_error, _public_error_message
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import HarnessControlService
from backend.tests.support import ScriptedModel


class StreamingScriptedModel(ScriptedModel):
    def __init__(self, text: str):
        super().__init__()
        self.text = text

    async def decide_stream(self, request, on_delta):
        for piece in (self.text[:2], self.text[2:]):
            await on_delta(piece)
        return DecisionProposal(kind="answer", answer=self.text)


class CompletedExecutor:
    async def execute(self, run, control):
        from backend.contracts.modules.loop import RunResult

        return RunResult(run_id=run.run_id, status="completed", answer="最终答案", elapsed_seconds=0)


async def test_harness_emits_progress_deltas_and_authoritative_done():
    settings = load_settings()
    events = MemoryEventSink()
    harness = build_harness(
        model=StreamingScriptedModel("海事答案"),
        settings=settings,
        events=events,
        streams=events,
    )
    result = await harness.run(RunInput(text="问题"))

    assert result.status == "completed"
    assert result.answer == "海事答案"
    stream = events.stream_events
    assert any(event.type == "progress" for event in stream)
    assert "".join(event.text or "" for event in stream if event.type == "delta") == result.answer
    done = [event for event in stream if event.type == "done"]
    assert len(done) == 1 and done[0].text == result.answer
    assert [event.sequence for event in stream] == list(range(1, len(stream) + 1))


async def test_deepseek_sse_forwards_visible_text_and_buffers_tool_calls():
    frames = [
        {"choices": [{"delta": {"content": "第一段"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "答案"}, "finish_reason": "stop"}]},
    ]
    sse = "\n\n".join(f"data: {json.dumps(item, ensure_ascii=False)}" for item in frames)
    sse += "\n\ndata: [DONE]\n"
    settings = load_settings()
    sink = MemoryEventSink()

    async def handler(request: httpx.Request):
        return httpx.Response(
            200,
            text=sse,
            headers={"content-type": "text/event-stream"},
        )

    from backend.tests.integration.test_adapters import request as model_request

    async with DeepSeekModel(
        settings,
        SecretStr("test-only"),
        sink,
        transport=httpx.MockTransport(handler),
    ) as model:
        pieces: list[str] = []

        async def collect(piece: str) -> None:
            pieces.append(piece)

        decision = await model.decide_stream(model_request(), collect)

    assert decision.kind == "answer"
    assert decision.answer == "第一段答案"
    assert pieces == ["第一段", "答案"]


def test_citation_stream_buffers_split_markers_and_drops_unresolved(tmp_path):
    corpus = MaritimeCorpus(tmp_path / "corpus.sqlite3")
    snapshot = corpus.remember_source(
        {"id": "law-1", "kind": "laws", "title": "测试法规", "text": "完整原文"}
    )
    stream = CitationStream(corpus)
    chunks = [
        "依据 [[ZX:",
        snapshot[:5],
        snapshot[5:16],
        "]]，继续。",
        " 无效 [[ZX:aaaaaaaaaaaaaaaa]]。",
    ]
    visible = "".join(stream.push(chunk) for chunk in chunks)
    visible += stream.push(final=True)

    assert "[[ZX:" not in visible
    assert "无效" in visible
    assert "[核对原文](/api/maritime/sources/laws/law-1" in visible


async def test_harness_stream_never_exposes_maritime_marker(tmp_path):
    corpus = MaritimeCorpus(tmp_path / "corpus.sqlite3")
    snapshot = corpus.remember_source(
        {"id": "law-1", "kind": "laws", "title": "测试法规", "text": "完整原文"}
    )
    tool = CorpusTool(corpus)
    settings = load_settings()
    events = MemoryEventSink()
    model = StreamingScriptedModel(f"依据 [[ZX:{snapshot[:16]}]]。")
    harness = build_harness(
        model=model,
        settings=settings,
        events=events,
        streams=events,
        tools={tool.spec.name: (tool.spec, tool)},
    )

    result = await harness.run(RunInput(text="请给出依据"))
    stream_text = "".join(
        event.text or "" for event in events.stream_events if event.type == "delta"
    )

    assert result.status == "completed"
    assert "[[ZX:" not in stream_text
    assert "[[ZX:" not in (result.answer or "")
    assert "[核对原文](/api/maritime/sources/laws/law-1" in stream_text
    assert visible_text(events.stream_events) == result.answer


async def test_failed_unresolved_citation_keeps_public_partial_without_marker():
    settings = load_settings()
    events = MemoryEventSink()
    tool = CorpusTool()
    model = StreamingScriptedModel("依据 [[ZX:aaaaaaaaaaaaaaaa]]。")
    harness = build_harness(
        model=model,
        settings=settings,
        events=events,
        streams=events,
        tools={tool.spec.name: (tool.spec, tool)},
    )

    result = await harness.run(RunInput(text="请给出依据"))
    stream_text = visible_text(events.stream_events)

    assert result.status == "failed"
    assert "[[ZX:" not in stream_text
    assert "[[ZX:" not in (result.answer or "")


def test_workbench_error_boundary_hides_scheduler_diagnostics():
    error = Error(
        code="retry_limit",
        message="已到达本次运行的修正或重试上限。",
        retryable=True,
    )

    public = _public_error(error)
    assert public["message"] == "本次处理没有完成，已保留当前内容。你可以继续处理，或重新开始。"
    assert "修正" not in public["message"]
    assert "重试" not in public["message"]
    assert _public_error_message("retry_limit") == public["message"]


def test_ndjson_api_exposes_stream_events_alongside_lifecycle():
    app = create_app(control_service=HarnessControlService(CompletedExecutor()))
    with TestClient(app) as client:
        submitted = client.post("/harness/sessions/local/runs", json={"text": "问题"})
        run_id = submitted.json()["dispatch"]["run_id"]
        hub = app.state.event_hub
        hub.emit_stream(StreamEvent(run_id=run_id, type="progress", label="正在处理"))
        hub.emit_stream(StreamEvent(run_id=run_id, type="delta", text="最终"))
        response = client.get(
            "/harness/sessions/local/events", params={"run_id": run_id}
        )
        lines = [json.loads(line) for line in response.text.splitlines() if line]

    assert any(item["type"] == "lifecycle" for item in lines)
    stream = [item["event"] for item in lines if item["type"] == "stream"]
    assert [item["type"] for item in stream] == ["progress", "delta"]
    assert [item["sequence"] for item in stream] == [1, 2]


def test_ndjson_api_replays_only_events_after_run_cursor():
    app = create_app(control_service=HarnessControlService(CompletedExecutor()))
    with TestClient(app) as client:
        submitted = client.post("/harness/sessions/local/runs", json={"text": "问题"})
        run_id = submitted.json()["dispatch"]["run_id"]
        hub = app.state.event_hub
        hub.emit_stream(StreamEvent(run_id=run_id, type="progress", label="阶段一"))
        hub.emit_stream(StreamEvent(run_id=run_id, type="delta", text="阶段二"))
        hub.emit_stream(StreamEvent(run_id=run_id, type="done", text="完整答案"))

        response = client.get(
            "/harness/sessions/local/events",
            params={"run_id": run_id, "after_sequence": 1},
        )
        lines = [json.loads(line) for line in response.text.splitlines() if line]

    stream = [item["event"] for item in lines if item["type"] == "stream"]
    assert [item["sequence"] for item in stream] == [2, 3]
    assert stream[-1]["type"] == "done"
