"""Offline invariants; no native parser, inference, live model or network call."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.model_context import DeploymentLimits
from backend.contracts.modules.loop import PerceptionInput, RunInput
from backend.contracts.modules.materials import (
    MaterialLocator,
    MaterialManifest,
    MaterialReadInput,
    MaterialSearchInput,
    MaterialUnit,
    ParsedMaterial,
)
from backend.contracts.modules.memory import MemoryRecall
from backend.contracts.modules.perception import ContextItem
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.model.budget import ModelBudget
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.public_text import PublicText
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.infrastructure.parsing.read_view import cursor_for, read_view
from backend.infrastructure.parsing.search_view import search_view
from backend.modules.perception.service import TextPerception


def document(*texts, key="material-a", version="parse-version-a"):
    return ParsedMaterial(
        manifest=MaterialManifest(
            material_id=key,
            name="fixture.docx",
            uri="",
            media_type="text/plain",
            status="available",
            parser="synthetic",
            parse_version=version,
            complete=True,
            unit_count=len(texts),
        ),
        units=[
            MaterialUnit(
                unit_id=f"{key}-block-{i}",
                material_id=key,
                parse_version=version,
                order=i,
                kind="paragraph",
                text=text,
                locator=MaterialLocator(
                    kind="paragraph", value=str(i + 1), label=f"第{i + 1}段"
                ),
            )
            for i, text in enumerate(texts)
        ],
    )


def test_continuation_preserves_every_character_and_reaches_end():
    parsed = document("标题\n", "正文" * 9000, "\n尾部")
    cursor, pieces = None, []
    for _ in range(100):
        output = read_view(
            parsed,
            MaterialReadInput(material_id="material-a", cursor=cursor),
            limit=631,
            overview_limit=4,
        )
        pieces.extend(unit.text for unit in output.units)
        cursor = output.next_cursor
        if cursor is None:
            break
    else:
        pytest.fail("reader failed to make bounded progress")
    assert "".join(pieces) == "".join(unit.text for unit in parsed.units)
    eof = read_view(
        parsed,
        MaterialReadInput(
            material_id="material-a", cursor=cursor_for(parsed, len(parsed.units), 0)
        ),
        limit=20,
        overview_limit=4,
    )
    assert eof.status == "end" and not eof.units


def test_search_returns_tail_hit_and_version_cursor_expires():
    parsed = document("开头" * 6000 + "责任条款命中" + "尾部" * 600)
    matches = search_view(
        {"material-a": parsed}, MaterialSearchInput(query="责任条款命中"),
        load_settings().materials, limit=2000,
    )["matches"]
    output = read_view(
        parsed,
        MaterialReadInput(
            material_id="material-a", unit_id=matches[0]["unit_id"],
            char_offset=matches[0]["char_offset"],
        ),
        limit=800,
        overview_limit=4,
    )
    assert "责任条款命中" in "".join(u.text for u in output.units)
    assert output.coverage[0]["char_start"] > 10000
    parsed.manifest.parse_version = "changed-version"
    expired = read_view(
        parsed,
        MaterialReadInput(material_id="material-a", cursor=output.next_cursor),
        limit=800,
        overview_limit=4,
    )
    assert expired.status == "invalid_cursor"


def test_no_match_is_distinct_from_missing_parse():
    parsed = document("已有正文")
    assert (
        search_view(
            {"material-a": parsed}, MaterialSearchInput(query="没有命中"),
            load_settings().materials, limit=800,
        )["status"]
        == "no_match"
    )
    parsed.manifest.status = "unavailable"
    assert (
        read_view(
            parsed,
            MaterialReadInput(material_id="material-a"),
            limit=800,
            overview_limit=4,
        ).status
        == "unavailable"
    )


def test_run_binding_and_coverage_do_not_leak_or_clear_each_other(tmp_path):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path)
    catalog = MaterialCatalog(settings)
    catalog.bind(document("甲方原文"), run_id="a")
    catalog.bind(document("乙方原文", key="material-b"), run_id="b")
    candidates = catalog.candidates("a")
    assert "甲方原文" in candidates[0].body["units"][0]["text"]
    assert not catalog.coverage_for("a")  # candidate is not a delivered context
    catalog.record_selected(candidates, run_id="a")
    assert catalog.coverage_for("a")
    with pytest.raises(PortError):
        catalog.read(MaterialReadInput(material_id="material-a"), run_id="b", limit=100)
    catalog.close_run("a")
    assert (
        catalog.read(MaterialReadInput(material_id="material-b"), run_id="b", limit=100)
        .units[0]
        .text
        == "乙方原文"
    )


def test_budget_separates_raw_deployment_output_and_policy():
    settings = load_settings()
    baseline = ModelBudget(settings).plan()
    assert (baseline.raw_tokens, baseline.target, baseline.warning, baseline.low) == (
        1000000,
        650000,
        700000,
        600000,
    )
    assert baseline.hard == 1000000 - 8192 - 50000
    settings.deployment_limits[settings.model.name] = DeploymentLimits(
        total_tokens=50000, input_tokens=20000, output_tokens=4000
    )
    constrained = ModelBudget(settings).plan()
    assert constrained.raw_tokens == 1000000 and constrained.effective_tokens == 50000
    assert constrained.output_reserved == 4000 and constrained.hard == 20000
    settings.model.name = "gpt-6.1-sol"
    relay = ModelBudget(settings).plan()
    assert (
        relay.raw_tokens is None
        and relay.assumed_capacity
        and relay.effective_tokens == 32768
    )


def test_tools_and_images_are_in_full_request_estimate():
    budget = ModelBudget(load_settings())
    a = budget.estimate({"messages": [{"role": "user", "content": "问"}]})
    b = budget.estimate(
        {
            "messages": [{"role": "user", "content": "问"}],
            "tools": [{"description": "定义" * 1000}],
        }
    )
    assert b.tokens > a.tokens + 5000
    short = budget.estimate(
        {
            "input": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
            ]
        }
    )
    long = budget.estimate(
        {
            "input": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + "A" * 100000},
                }
            ]
        }
    )
    assert short.tokens == long.tokens and long.image_count == 1


def test_public_identity_projection_is_identical_across_arbitrary_stream_boundaries():
    items = [
        ContextItem(
            type="resource",
            source="user.material",
            body={
                "name": "合同.docx",
                "unit_id": "internal-block-123456789",
                "parse_version": "opaque-version-987654321",
            },
        )
    ]
    text = (
        "来源 [internal-block-123456789]，2026-10-01 法律版本。opaque-version-987654321"
    )
    projector = PublicText(items)
    output = "".join(projector.push(c) for c in text) + projector.push("", final=True)
    assert output == PublicText(items).render(text)
    assert "internal-block" not in output and "2026-10-01" in output


@pytest.mark.asyncio
async def test_short_context_is_not_forced_to_call_reader_and_pressure_is_bounded(
    tmp_path,
):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path)
    events = MemoryEventSink()
    async with DeepSeekModel(
        settings,
        SecretStr("offline-fixture"),
        events,
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected network send")),
    ) as model:
        perception = TextPerception(settings, model)
        candidate = ContextItem(
            id="material:one",
            type="resource",
            source="user.material",
            body={
                "material_id": "one",
                "name": "题目.docx",
                "units": [{"text": "第1题正文与选项"}],
            },
        )
        request = ModuleRequest(
            run_id="run",
            call_id="perception",
            input=PerceptionInput(
                current=RunInput(text="前十题"),
                memory=MemoryRecall(scope="run"),
                candidates=[candidate],
            ),
        )
        result = await perception.handle(request)
        assert result.status == "ok" and next(i for i in result.output.items if i.id == "material:one").body["units"]
        settings.deployment_limits[settings.model.name] = DeploymentLimits(
            total_tokens=24000
        )
        model.budget.deployment = settings.deployment_limits[settings.model.name]
        candidate.body["units"] = [{"text": "材料正文" * 50000}]
        pressured = await perception.handle(request)
        assert pressured.status == "ok"
        assert "context_compacted" in pressured.output.assembly.notices
        assert (
            pressured.output.assembly.used_tokens
            <= pressured.output.assembly.budget.warning
        )


@pytest.mark.asyncio
async def test_wire_guard_covers_direct_reviewer_client_and_blocks_before_send():
    settings = load_settings()
    sent = []
    async with DeepSeekModel(
        settings,
        SecretStr("offline-fixture"),
        MemoryEventSink(),
        transport=httpx.MockTransport(
            lambda r: sent.append(r) or httpx.Response(200, json={})
        ),
    ) as model:
        with pytest.raises(PortError):
            await model.client.post(
                settings.model.endpoint,
                json={
                    "model": settings.model.name,
                    "messages": [{"role": "user", "content": "x" * 1000000}],
                    "max_tokens": 12,
                },
            )
    assert not sent


@pytest.mark.asyncio
async def test_required_pressure_failure_keeps_trace_without_sending():
    settings = load_settings()
    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(lambda _: pytest.fail("must not send")),
    ) as model:
        request = ModuleRequest(
            run_id="pressure",
            call_id="assemble",
            input=PerceptionInput(
                current=RunInput(text="必须保留的要求" * 50000),
                memory=MemoryRecall(scope="pressure"),
            ),
        )
        result = await TextPerception(settings, model).handle(request)
    assert result.status == "ok" and result.output.needs_compaction
    assert result.output.assembly.trace["stopped_at"] == "bounded_selection"
    assert result.output.assembly.used_tokens > result.output.assembly.budget.hard


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,status", [("stop", "completed"), ("length", "failed")])
async def test_harness_terminal_state_and_partial_answer(finish, status):
    settings = load_settings()
    events, sent = MemoryEventSink(), []

    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": finish, "message": {"content": "已生成部分正文"}}
                ]
            },
        )

    async with DeepSeekModel(
        settings,
        SecretStr("offline-fixture"),
        events,
        transport=httpx.MockTransport(transport),
    ) as model:
        harness = build_harness(
            model=model, settings=settings, events=events, streams=events
        )
        result = await harness.run(RunInput(text="解释一个词"), run_id="offline-run")
    assert result.status == status and result.answer == "已生成部分正文"
    assert len(sent) == 1 and sent[0]["max_tokens"] == 8192
    assert sent[0]["thinking"] == {"type": "disabled"}
    if finish == "length":
        assert result.error.code == "model_output_incomplete"
        assert not any(event.type == "done" for event in events.stream_events)


@pytest.mark.asyncio
async def test_short_attachment_can_answer_in_first_call():
    settings, events, sent = load_settings(), MemoryEventSink(), []

    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "十项答案"}}
                ]
            },
        )

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        harness = build_harness(model=model, settings=settings, events=events)
        harness.material_catalog.bind(
            document(*(f"第{i}项题干及选项" for i in range(1, 11))), run_id="short"
        )
        result = await harness.run(RunInput(text="前十题"), run_id="short")
    assert result.status == "completed" and len(sent) == 1
    encoded = json.dumps(sent[0], ensure_ascii=False)
    assert "第10项题干及选项" in encoded
    assert not any(message.get("tool_calls") for message in sent[0]["messages"])


@pytest.mark.asyncio
async def test_search_tool_tail_returns_to_next_complete_model_request():
    settings, events, sent = load_settings(), MemoryEventSink(), []

    def transport(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if len(sent) == 1:
            message = {
                "tool_calls": [
                    {
                        "id": "call-tail",
                        "type": "function",
                        "function": {
                            "name": "search_material",
                            "arguments": json.dumps(
                                {
                                    "material_ids": ["material-a"],
                                    "query": "结尾定位目标",
                                }
                            ),
                        },
                    }
                ]
            }
            reason = "tool_calls"
        else:
            assert any(
                m["role"] == "tool" and "结尾定位目标" in m["content"]
                for m in packet["messages"]
            )
            message, reason = {"content": "据结尾正文作答"}, "stop"
        return httpx.Response(
            200, json={"choices": [{"finish_reason": reason, "message": message}]}
        )

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        harness = build_harness(model=model, settings=settings, events=events)
        harness.material_catalog.bind(
            document("前文" * 50000 + "结尾定位目标" + "余文" * 1000), run_id="long"
        )
        result = await harness.run(RunInput(text="查找结尾定位目标"), run_id="long")
        assert all(model.budget.check(packet) for packet in sent)
    assert result.status == "completed" and len(sent) == 2
    assert (
        result.execution_state["tool_results"][0]["result"]["matches"][0]["char_offset"]
        > 90000
    )


@pytest.mark.asyncio
async def test_private_recovery_is_persisted_but_absent_from_public_dump(tmp_path):
    from backend.contracts.harness.lifecycle import (
        PersistedSession,
        RunEnvelope,
        SessionSnapshot,
    )
    from backend.infrastructure.persistence.run_store import JsonRunStore

    run = RunEnvelope(
        session_id="conversation",
        account_id="account",
        payload={},
        execution_state={"tool_results": [{"text": "retained fixture evidence"}]},
    )
    assert "execution_state" not in run.model_dump()
    store = JsonRunStore(tmp_path / "runs.json")
    await store.save(
        PersistedSession(
            snapshot=SessionSnapshot(session_id="conversation", runs=[run])
        )
    )
    restored = await store.load(session_id="conversation")
    assert restored.snapshot.runs[0].execution_state == run.execution_state


@pytest.mark.asyncio
async def test_responses_guard_counts_hidden_continuation_before_network():
    from backend.config.relay import RelayConfig
    from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
    from backend.contracts.modules.perception import ContextAssembly, ContextLink
    from backend.infrastructure.model.responses import (
        ResponsesModel,
        ResponsesTransport,
    )

    settings, sent = load_settings(), []
    settings.model.name, settings.model.endpoint = (
        "gpt-6.1-sol",
        "https://relay.test/v1/responses",
    )
    settings.model.allowed_hosts, settings.model.thinking_mode = ["relay.test"], "high"
    transport = ResponsesTransport(
        RelayConfig(
            settings.model.endpoint, settings.model.name, "low", SecretStr("offline")
        ),
        transport=httpx.MockTransport(
            lambda r: sent.append(r) or httpx.Response(200, json={})
        ),
        max_cached_runs=4,
    )
    transport.contexts["relay-run"] = {
        "call": [
            {"type": "reasoning", "id": "opaque", "encrypted_content": "a" * 50000}
        ]
    }
    items = [
        ContextItem(
            id="call-item",
            type="tool_call",
            source="reasoning",
            role="assistant",
            body={
                "calls": [
                    {"action_id": "call", "tool_name": "read_material", "arguments": {}}
                ]
            },
        ),
        ContextItem(
            type="tool_result",
            source="action",
            role="tool",
            body={
                "action_id": "call",
                "tool_name": "read_material",
                "result": {"text": "result"},
            },
            links=[ContextLink(relation="responds_to", target_id="call-item")],
        ),
    ]
    async with ResponsesModel(
        settings, SecretStr("offline"), MemoryEventSink(), transport=transport
    ) as model:
        assert (
            model.estimate_context(items, [], run_id="relay-run").tokens
            > model.context_budget().hard
        )
        with pytest.raises(PortError):
            await model.decide(
                ModuleRequest(
                    run_id="relay-run",
                    call_id="next",
                    input=ReasoningInput(
                        perceived=PerceivedContext(
                            run_id="relay-run", items=items, assembly=ContextAssembly()
                        )
                    ),
                )
            )
    assert not sent


@pytest.mark.asyncio
async def test_repeated_tool_results_stop_without_expanding_round_limit():
    from backend.contracts.modules.loop import ActionResult, ToolSpec

    settings, events, sent = load_settings(), MemoryEventSink(), []

    class Tool:
        def __init__(self):
            self.calls = 0

        async def execute(self, request):
            self.calls += 1
            return ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                result={"text": "unchanged"},
            )

    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": f"call-{len(sent)}",
                                    "type": "function",
                                    "function": {"name": "fixture", "arguments": "{}"},
                                }
                            ]
                        },
                    }
                ]
            },
        )

    tool = Tool()
    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        harness = build_harness(
            model=model,
            settings=settings,
            events=events,
            tools={
                "fixture": (
                    ToolSpec(
                        name="fixture",
                        description="fixture",
                        parameters={"type": "object"},
                        read_only=True,
                        reuse_identical_results=True,
                    ),
                    tool,
                )
            },
        )
        result = await harness.run(RunInput(text="查明结果"), run_id="repeat")
    assert result.status == "failed" and result.error.code == "tool_no_progress"
    assert tool.calls == 2 and len(sent) < settings.runtime.max_rounds
    assert {t["function"]["name"] for t in sent[-1]["tools"]} == {settings.model.delivery_tool, settings.model.clarification_tool}
    assert result.execution_state["tool_results"][-1]["error"]["code"] == "tool_repeated"


@pytest.mark.asyncio
async def test_cancellation_carries_prior_results_and_closes_only_its_run():
    import asyncio

    from backend.contracts.modules.loop import ActionResult, ToolSpec

    settings, events = load_settings(), MemoryEventSink()
    waiting, never = asyncio.Event(), asyncio.Event()
    calls = 0

    class Tool:
        async def execute(self, request):
            return ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                result={"text": "prior evidence"},
            )

    async def transport(request):
        nonlocal calls
        calls += 1
        if calls > 1:
            waiting.set()
            await never.wait()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "prior-call",
                                    "type": "function",
                                    "function": {"name": "fixture", "arguments": "{}"},
                                }
                            ]
                        },
                    }
                ]
            },
        )

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        harness = build_harness(
            model=model,
            settings=settings,
            events=events,
            tools={
                "fixture": (
                    ToolSpec(
                        name="fixture",
                        description="fixture",
                        parameters={"type": "object"},
                    ),
                    Tool(),
                )
            },
        )
        harness.material_catalog.bind(
            document("other", key="other"), run_id="other-run"
        )
        running = asyncio.create_task(
            harness.run(RunInput(text="continue"), run_id="cancel-run")
        )
        await asyncio.wait_for(waiting.wait(), timeout=3)
        running.cancel()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await running
        outcome = cancelled.value.run_result
        assert outcome.status == "cancelled"
        assert (
            outcome.execution_state["tool_results"][0]["result"]["text"]
            == "prior evidence"
        )
        assert (
            "other-run" in harness.material_catalog.runs
            and "cancel-run" not in harness.material_catalog.runs
        )
