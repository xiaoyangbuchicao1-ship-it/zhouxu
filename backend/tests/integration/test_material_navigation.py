"""Material navigation through the real encoder, tools and Harness (offline model)."""

import json

import httpx
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.model_context import DeploymentLimits
from backend.contracts.modules.loop import (
    ActionRequest,
    ActionResult,
    RunInput,
    ToolSpec,
)
from backend.contracts.modules.materials import (
    MaterialInput,
    MaterialReadInput,
    MaterialSearchInput,
)
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.infrastructure.tools.material_read import MaterialReadTool


def reply(text=None, calls=()):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "tool_calls" if calls else "stop",
        "message": {"content": text, "tool_calls": list(calls)},
    }]})


def call(identity, tool, arguments):
    return {"id": identity, "type": "function", "function": {
        "name": tool, "arguments": json.dumps(arguments, ensure_ascii=False),
    }}


async def test_initial_context_is_bounded_and_both_material_tools_are_available(tmp_path):
    text = "正文记录。" * 1800 + "只在末尾出现的标记"
    path = tmp_path / "report.txt"
    path.write_text(text, encoding="utf-8")
    settings, events, sent = load_settings(), MemoryEventSink(), []
    settings.materials.cache_root = str(tmp_path / "cache")

    def transport(request):
        packet = json.loads(request.content)
        sent.append(packet)
        tools = {t["function"]["name"]: t["function"] for t in packet["tools"]}
        assert {"search_material", "read_material"} <= tools.keys()
        assert "search" not in tools["read_material"]["parameters"]["properties"]["mode"]["enum"]
        assert "只在末尾出现的标记" not in json.dumps(packet, ensure_ascii=False)
        assert "正文记录" in json.dumps(packet, ensure_ascii=False)
        return reply("先看给定的开头即可。")

    async with DeepSeekModel(settings, SecretStr("offline"), events,
                            transport=httpx.MockTransport(transport)) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(text="概括已经提供的开头。", materials=[MaterialInput(uri=str(path))]))
    assert result.status == "completed" and len(sent) == 1


async def test_search_finds_multiple_documents_then_batch_reads_reach_next_context(tmp_path):
    settings, events, sent = load_settings(), MemoryEventSink(), []
    settings.materials.cache_root = str(tmp_path / "cache")
    files = []
    for index, condition in enumerate(["解除须提前30天通知。", "解除须提前60天通知。"]):
        path = tmp_path / f"contract-{index}.txt"
        path.write_text("一般背景。\n" * 900 + condition + "\n履行完毕后终止。", encoding="utf-8")
        files.append(MaterialInput(uri=str(path), material_id=f"contract-{index}"))

    def transport(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if len(sent) == 1:
            return reply(calls=[call("find", "search_material", {"query": "解除"})])
        results = [json.loads(m["content"]) for m in packet["messages"] if m["role"] == "tool"]
        if len(sent) == 2:
            matches = results[-1]["matches"]
            assert {m["material_id"] for m in matches} == {"contract-0", "contract-1"}
            return reply(calls=[call(f"read-{i}", "read_material", {
                "material_id": match["material_id"], "unit_id": match["unit_id"],
                "end_unit_id": match["unit_id"],
            }) for i, match in enumerate(matches)])
        assert len(results) == 3
        assert "30天" in results[1]["text"] and "60天" in results[2]["text"]
        assert {m["tool_call_id"] for m in packet["messages"] if m["role"] == "tool"} == {"find", "read-0", "read-1"}
        return reply("两份材料分别要求提前30天和60天通知。")

    async with DeepSeekModel(settings, SecretStr("offline"), events,
                            transport=httpx.MockTransport(transport)) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(text="比较两份合同的解除通知要求。", materials=files))
    assert result.status == "completed" and len(sent) == 3
    assert len(result.execution_state["tool_results"]) == 3


async def test_pressure_read_reaches_tail_without_budget_error_or_warning_event(tmp_path):
    settings, events, sent = load_settings(), MemoryEventSink(), []
    settings.deployment_limits[settings.model.name] = DeploymentLimits(total_tokens=64000)
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "large.txt"
    path.write_text("普通背景。" * 40000 + "末尾：青帆九二七", encoding="utf-8")

    def transport(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if len(sent) == 1:
            return reply(calls=[call("tail", "read_material", {
                "material_id": "large", "position": "end", "max_chars": 500,
            })])
        assert "青帆九二七" in json.dumps(packet, ensure_ascii=False)
        return reply("青帆九二七")

    async with DeepSeekModel(settings, SecretStr("offline"), events,
                            transport=httpx.MockTransport(transport)) as model:
        result = await build_harness(model=model, settings=settings, events=events, streams=events).run(
            RunInput(text="末尾的标记是什么？", materials=[MaterialInput(uri=str(path), material_id="large")]))
        assert all(model.budget.estimate(packet).tokens <= model.budget.plan().warning for packet in sent)
    assert result.status == "completed" and result.error is None
    assert len(sent) == 2 and not any(e.type == "error" for e in events.stream_events)


async def test_read_range_preserves_adjacent_content_and_hides_duplicate_preview(tmp_path):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "table-notes.txt"
    path.write_text("标题\n条款正文\n补充条件\n不在选择范围内", encoding="utf-8")
    catalog = MaterialCatalog(settings)
    await catalog.ingest([MaterialInput(uri=str(path), material_id="note")], run_id="r")
    parsed = catalog.runs["r"]["note"]
    selected = catalog.read(MaterialReadInput(
        material_id="note", unit_id=parsed.units[1].unit_id,
        end_unit_id=parsed.units[2].unit_id,
    ), run_id="r", limit=100)
    assert [u.text.strip() for u in selected.units] == ["条款正文", "补充条件"]
    assert selected.next_cursor is None
    assert "标题" in str(catalog.candidates("r")[0].body["units"])


async def test_reading_artifacts_share_parser_version_and_source_mapping(tmp_path):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "source.txt"
    path.write_text("第一段\n第二段", encoding="utf-8")
    catalog = MaterialCatalog(settings)
    await catalog.ingest([MaterialInput(uri=str(path), material_id="first")], run_id="a")
    parsed = catalog.runs["a"]["first"]
    root = catalog.registry.cache.artifact_root(parsed.manifest.parse_version)
    text = (root / "reading.md").read_text(encoding="utf-8")
    mapping = json.loads((root / "reading-map.json").read_text(encoding="utf-8"))
    assert mapping["parse_version"] == parsed.manifest.parse_version
    assert "first" not in json.dumps(mapping)
    for item in mapping["ranges"]:
        original = parsed.units[item["order"]].text
        assert text[item["char_start"]:item["char_end"]] == original.replace("\r\n", "\n").replace("\r", "\n")
    await catalog.ingest([MaterialInput(uri=str(path), material_id="second")], run_id="b")
    assert catalog.runs["b"]["second"].manifest.parse_version == parsed.manifest.parse_version
    assert (root / "reading.md").read_text(encoding="utf-8") == text


async def test_search_pagination_and_run_scope_cannot_mix_documents(tmp_path):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "many.txt"
    path.write_text("\n".join(f"相同锚点，序号{i}" for i in range(12)), encoding="utf-8")
    catalog = MaterialCatalog(settings)
    await catalog.ingest([MaterialInput(uri=str(path), material_id="owned")], run_id="owner")
    cursor, ids = None, []
    for _ in range(12):
        result = catalog.search(MaterialSearchInput(
            query="相同锚点", max_results=3, cursor=cursor,
        ), run_id="owner", limit=2000)
        ids.extend(m["unit_id"] for m in result["matches"])
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 12
    denied = catalog.search(MaterialSearchInput(
        query="相同锚点", material_ids=["owned"],
    ), run_id="other", limit=2000)
    assert denied["status"] == "unavailable" and not denied["matches"]
    assert not catalog.search(MaterialSearchInput(query="相同锚点"), run_id="other", limit=2000)["matches"]


async def test_scoped_continuation_never_reads_past_requested_end(tmp_path):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "ranges.txt"
    path.write_text("开头\n" + "有效范围" * 50 + "\n禁止越过选择范围", encoding="utf-8")
    catalog = MaterialCatalog(settings)
    await catalog.ingest([MaterialInput(uri=str(path), material_id="scope")], run_id="scope")
    units = catalog.runs["scope"]["scope"].units
    value = MaterialReadInput(material_id="scope", unit_id=units[1].unit_id,
                              end_unit_id=units[1].unit_id)
    pieces = []
    for _ in range(20):
        result = catalog.read(value, run_id="scope", limit=19)
        pieces.extend(u.text for u in result.units)
        if not result.next_cursor:
            break
        value = MaterialReadInput(material_id="scope", cursor=result.next_cursor)
    assert "".join(pieces) == units[1].text
    assert "禁止越过" not in "".join(pieces)


async def test_accumulated_tool_evidence_is_summarized_before_omitting_bodies(tmp_path):
    settings, events, main_calls, summaries = load_settings(), MemoryEventSink(), [], []
    settings.deployment_limits[settings.model.name] = DeploymentLimits(total_tokens=64000)
    settings.materials.cache_root = str(tmp_path / "cache")

    class Lookup:
        async def execute(self, request):
            value = "青帆" if request.arguments["part"] == "one" else "白鹭"
            return ActionResult(tool_name="lookup", action_id=request.action_id,
                                result={"text": "普通原始记录。" * 1100, "value": value})

    def transport(request):
        packet = json.loads(request.content)
        if packet["messages"][0]["content"] == settings.prompts["compaction"]:
            summaries.append(packet)
            assert "取得两个部分并合并其中的值" in json.dumps(packet, ensure_ascii=False)
            return reply("已取得第一部分的值：青帆。接下来核对第二部分；原始回执可继续读取。")
        main_calls.append(packet)
        if len(main_calls) < 3:
            part = "one" if len(main_calls) == 1 else "two"
            return reply(calls=[call(part, "lookup", {"part": part})])
        assert summaries
        assert "已取得第一部分的值：青帆" in json.dumps(packet, ensure_ascii=False)
        assert "白鹭" in json.dumps(packet, ensure_ascii=False)
        return reply("青帆、白鹭")

    spec = ToolSpec(name="lookup", description="读取两个测试数据部分。", parameters={
        "type": "object", "properties": {"part": {"type": "string"}}})
    async with DeepSeekModel(settings, SecretStr("offline"), events,
                            transport=httpx.MockTransport(transport)) as model:
        result = await build_harness(model=model, settings=settings, events=events,
                                     streams=events, tools={"lookup": (spec, Lookup())}).run(
            RunInput(text="取得两个部分并合并其中的值。"))
    assert summaries and result.status == "completed"
    assert not any(e.type == "error" for e in events.stream_events)


async def test_reading_tail_does_not_discard_uncovered_initial_preview(tmp_path):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "continuity.txt"
    path.write_text("开头必须保留的事实\n" + "普通正文" * 2000 + "\n末尾事实", encoding="utf-8")
    catalog = MaterialCatalog(settings)
    await catalog.ingest([MaterialInput(uri=str(path), material_id="continuity")], run_id="r")
    catalog.read(MaterialReadInput(material_id="continuity", position="end"), run_id="r", limit=30)
    assert "开头必须保留的事实" in str(catalog.candidates("r")[0].body["units"])
    catalog.read(MaterialReadInput(material_id="continuity"), run_id="r", limit=settings.materials.navigation_preview_chars)
    assert not catalog.candidates("r")[0].body["units"]


async def test_material_preview_precedes_native_read_exchange_without_reordering_results(tmp_path):
    settings, events, sent = load_settings(), MemoryEventSink(), []
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "ordered-source.txt"
    path.write_text("开头事实：交付须通知。\n" + "普通记录。\n" * 600 + "末尾事实：收到后确认。", encoding="utf-8")

    def transport(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if len(sent) == 1:
            return reply(calls=[call("tail", "read_material", {
                "material_id": "ordered", "position": "end", "max_chars": 80,
            })])
        return reply("开头要求通知，末尾要求收到后确认。")

    async with DeepSeekModel(settings, SecretStr("offline"), events,
                            transport=httpx.MockTransport(transport)) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(text="结合开头和末尾概括要求。", materials=[MaterialInput(uri=str(path), material_id="ordered")]))
    assert result.status == "completed" and len(sent) == 2
    messages = sent[-1]["messages"]
    preview = next(i for i, m in enumerate(messages)
                   if m["role"] == "user" and "开头事实：交付须通知。" in (m.get("content") or ""))
    request_index = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
    result_index = next(i for i, m in enumerate(messages) if m["role"] == "tool")
    assert preview < request_index < result_index
    assert messages[result_index]["tool_call_id"] == messages[request_index]["tool_calls"][0]["id"]
    assert "末尾事实：收到后确认。" in messages[result_index]["content"]


async def allocated_material_read(tmp_path, *, allocated=48000, name_repeats=20):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "source.txt"
    path.write_text("\n".join(f"{index:03d} " + "普通记录。" * 6 for index in range(250)), encoding="utf-8")
    catalog = MaterialCatalog(settings)
    await catalog.ingest([MaterialInput(
        uri=str(path), material_id="material-0123456789abcdef",
        name="阅读来源说明" * name_repeats,
    )], run_id="allocation")
    result = await MaterialReadTool(catalog, settings).execute_context(ModuleRequest(
        run_id="allocation", call_id="read",
        input=ActionRequest(tool_name="read_material", arguments={
            "material_id": "material-0123456789abcdef", "max_chars": 6000,
        }),
        context={"material_read_chars": allocated},
    ))
    return catalog, result


async def test_requested_body_size_is_not_mistaken_for_entire_response_allocation(tmp_path):
    _, result = await allocated_material_read(tmp_path)
    assert sum(len(unit["text"]) for unit in result.result["units"]) == 6000


async def test_material_read_commits_only_the_receipt_that_is_returned(tmp_path):
    catalog, result = await allocated_material_read(tmp_path)
    committed = catalog.coverage_for("allocation")
    assert len(committed) == 1
    assert committed[0]["coverage"] == result.result["coverage"]
    assert committed[0]["next_cursor"] == result.result["next_cursor"]


async def test_sizing_probes_do_not_claim_unreturned_text_was_read(tmp_path):
    catalog, result = await allocated_material_read(tmp_path, allocated=500, name_repeats=120)
    returned_chars = sum(len(unit["text"]) for unit in result.result["units"])
    assert 0 < returned_chars < 500
    committed = catalog.coverage_for("allocation")
    assert len(committed) == 1
    assert committed[0]["coverage"] == result.result["coverage"]
    assert sum(span["char_end"] - span["char_start"] for span in committed[0]["coverage"]) == returned_chars
