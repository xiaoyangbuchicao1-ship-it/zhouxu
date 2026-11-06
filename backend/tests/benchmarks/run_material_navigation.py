"""Explicit paid material navigation evaluation; synthetic files are test fixtures."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from openpyxl import Workbook

from backend.bootstrap import build_harness, open_harness
from backend.config.settings import load_settings
from backend.contracts.model_context import DeploymentLimits
from backend.contracts.modules.loop import ActionResult, RunInput, ToolSpec
from backend.contracts.modules.materials import MaterialInput
from backend.infrastructure.model.factory import open_model
from backend.tests.benchmarks.run_rag_harness import AuditedModule
from backend.tests.benchmarks.run_unified_context import TimedEvents


async def main(args):
    root, output = Path(args.workdir).resolve(), Path(args.output).resolve()
    if output.exists():
        raise ValueError("evaluation_output_already_exists")
    root.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    settings.model.thinking_mode = "none"
    settings.materials.cache_root = str(root / "parser-cache")
    events, rows, trace, wire = TimedEvents(), [], [], []

    class CountedRequests(httpx.AsyncBaseTransport):
        def __init__(self, inner, budget):
            self.inner, self.budget = inner, budget

        async def handle_async_request(self, request):
            # This observer is INSIDE BudgetTransport: only guarded requests
            # reach it. Persist counts only, never headers, keys or full bodies.
            packet = json.loads(request.content)
            purpose = "compaction" if packet["messages"][0]["content"] == settings.prompts["compaction"] else "main"
            plan = self.budget.plan(purpose=purpose, output_tokens=packet["max_tokens"])
            wire.append({"purpose": purpose, "estimated_tokens": self.budget.estimate(packet).tokens,
                         "warning": plan.warning, "hard": plan.hard})
            return await self.inner.handle_async_request(request)

        async def aclose(self):
            await self.inner.aclose()

    def audit(harness):
        model = harness.modules["reasoning"].model
        transport = model.client._transport
        if not isinstance(transport.inner, CountedRequests):
            transport.inner = CountedRequests(transport.inner, model.budget)
        for name, module in list(harness.modules.items()):
            harness.modules[name] = AuditedModule(name, module, trace)

    async def evaluate(harness, name, question, files=(), expected=()):
        if args.case and name not in args.case:
            return
        events.began = perf_counter()
        before, stream_before, wire_before = len(events.events), len(events.stream_events), len(wire)
        result = await harness.run(RunInput(text=question, materials=list(files)))
        calls = [e for e in events.events[before:] if e.module == "model" and e.phase == "model"]
        assemblies = [e.diagnostics for e in events.events[before:]
                      if e.module == "perception" and e.diagnostics]
        tool_results = result.execution_state.get("tool_results", [])
        row = {
            "case": name, "question": question, "run_id": result.run_id,
            "status": result.status, "seconds": round(result.elapsed_seconds, 3),
            "first_delta_seconds": round(events.first_delta.get(result.run_id, 0), 3),
            "model_calls": len(calls), "reported_tokens": sum(e.total_tokens or 0 for e in calls),
            "tools": [{"name": t["tool_name"], "status": t["result"].get("status")}
                      for t in tool_results],
            "answer": result.answer, "error": result.error.model_dump() if result.error else None,
            "public_error_events": sum(e.type == "error" for e in events.stream_events[stream_before:]),
            "all_candidate_assemblies_below_warning": all(a["used_tokens"] <= a["budget"]["warning"] for a in assemblies),
            "sent_requests": wire[wire_before:],
            "all_sent_requests_within_budget": bool(wire[wire_before:]) and all(
                e["estimated_tokens"] <= min(e["warning"], e["hard"]) for e in wire[wire_before:]),
            "estimated_inputs": [a["used_tokens"] for a in assemblies],
            "internal_ids_in_answer": bool(re.search(r"#chunk-|parse_version|next_cursor|material-[0-9a-f]{10}", result.answer or "")),
            "expected_strings": list(expected),
            "expected_strings_found": all(
                bool(re.search(re.escape(v.split("=")[0]) + r"[\s|=*`：:]*" + re.escape(v.split("=", 1)[1]), result.answer or ""))
                if "=" in v else v in (result.answer or "") for v in expected
            ),
            "observed_answer_numbers": re.findall(r"(?m)^\s*(?:\*\*)?(\d+)[.、．]", result.answer or ""),
        }
        rows.append(row)
        output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: row[k] for k in ["case", "status", "seconds", "model_calls", "tools", "public_error_events", "expected_strings_found"]}), flush=True)

    word = MaterialInput(uri=str(Path(args.document).resolve()))
    contracts = []
    for i, date in enumerate(["2026-12-16", "2026-12-18"]):
        path = root / f"agreement-{i}.txt"
        path.write_text("一般记录，无须查询外部资料。\n" * 1100 + f"设备归还日期：{date}。\n", encoding="utf-8")
        contracts.append(MaterialInput(uri=str(path)))
    codes = [f"M{i:02d}={uuid4().hex[:6]}" for i in range(1, 25)]
    records = root / "records.txt"
    records.write_text("\n".join("普通背景记录。" * 500 + "\n校验标记：" + code for code in codes), encoding="utf-8")
    workbook, table = Workbook(), root / "table.xlsx"
    workbook.active.append(["月份", "数量"])
    for month, value in [("一月", 21), ("二月", 18), ("三月", 37), ("四月", 22)]:
        workbook.active.append([month, value])
    workbook.save(table)
    warmup = 0
    if not args.case or set(args.case) - {"pressure-tail", "pressure-tool-summary"}:
        warm_start = perf_counter()
        async with open_harness(settings=settings, events=events) as harness:
            warmup = perf_counter() - warm_start
            audit(harness)
            await evaluate(harness, "ordinary", "你好，请简短介绍自己。")
            for i in range(3):
                await evaluate(harness, f"first-ten-{i + 1}", "前十题", [word])
            await evaluate(harness, "whole-material", "按文档出现顺序完成这份材料的所有题目，每项给答案和一句简短理由。", [word])
            await evaluate(harness, "topic-comparison", "比较两份材料规定的设备归还日期。", contracts,
                           ["2026-12-16", "2026-12-18"])
            await evaluate(harness, "all-markers", "列出材料中所有“校验标记”及其值，不要遗漏。", [MaterialInput(uri=str(records))], codes)
            await evaluate(harness, "spreadsheet", "列出表格各月份的数量并计算总计。", [MaterialInput(uri=str(table))], ["98"])
    limited = settings.model_copy(deep=True)
    limited.deployment_limits[limited.model.name] = DeploymentLimits(total_tokens=64000)
    marker = "尾部" + uuid4().hex[:8]
    large = root / "large.txt"
    large.write_text("普通背景记录。" * 30000 + "\n末尾校验词：" + marker, encoding="utf-8")
    async with open_model(settings=limited, events=events, role="main") as model:
        harness = build_harness(model=model, settings=limited, events=events, streams=events)
        audit(harness)
        await evaluate(harness, "pressure-tail", "材料末尾校验词是什么？只输出校验词。", [MaterialInput(uri=str(large))], [marker])
        first, second, next_key = (uuid4().hex[:10] for _ in range(3))

        class FixtureParts:
            async def execute(self, request):
                is_first = request.arguments.get("key") == "start"
                assert is_first or request.arguments.get("key") == next_key
                return ActionResult(tool_name="read_test_part", action_id=request.action_id, result={
                    "status": "ok", "value": first if is_first else second,
                    "next_key": next_key if is_first else None,
                    "background": "普通原始记录。" * 1100,
                })

        spec = ToolSpec(name="read_test_part", description="测试数据源，按key取得一个部分及其value、next_key。", parameters={
            "type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]})
        harness = build_harness(model=model, settings=limited, events=events, streams=events,
                                tools={spec.name: (spec, FixtureParts())})
        audit(harness)
        await evaluate(harness, "pressure-tool-summary",
                       "用read_test_part从起始键start开始，按返回的next_key继续，取得两个部分的value并按顺序输出。",
                       expected=[first, second])
    rows.append({"case": "environment", "model": settings.model.name,
                 "warmup_seconds_excluded_from_run_latency": round(warmup, 3),
                 "note": "No legal gold key: format/coverage checks are not an accuracy grade."})
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--document", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required: this spends model credit")
    asyncio.run(main(args))
