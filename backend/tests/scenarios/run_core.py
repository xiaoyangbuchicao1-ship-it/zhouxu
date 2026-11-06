"""Versioned real-model regression; --live is mandatory and consumes credit."""

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from backend.bootstrap import build_harness, open_harness
from backend.config.settings import load_settings
from backend.contracts.events.streaming import visible_text
from backend.contracts.modules.loop import ActionResult, RunInput, ToolSpec
from backend.contracts.modules.materials import MaterialInput
from backend.contracts.schemas.core import PortError
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.benchmarks.run_reasoning_action import ObservedModel

CORPUS = Path(__file__).with_name("core_v1.json")


def fixture_text(spec):
    if "paragraphs" in spec:
        return "\n".join(spec["paragraphs"])
    if "text" in spec:
        return spec["text"]
    block = spec["repeat"] * spec["count"]
    return spec["prefix"] + block + spec["middle"] + block + spec["suffix"]


def write_fixtures(corpus, directory, source_base=None):
    paths = {}
    for name, spec in corpus["fixtures"].items():
        path = directory / (name + "." + spec["format"])
        if spec.get("file"):
            source = ((source_base or CORPUS.parent) / spec["file"]).resolve()
            shutil.copyfile(source, path)
        elif spec["format"] == "docx":
            from docx import Document

            document = Document()
            for paragraph in spec["paragraphs"]:
                document.add_paragraph(paragraph)
            document.save(path)
        else:
            path.write_text(fixture_text(spec), encoding="utf-8")
        paths[name] = path
    return paths


def check_result(expected, row):
    """Small factual/contract oracles; never claim to grade complete semantics."""
    failures, warnings, review = [], [], []
    text = row.get("answer") or ""
    names = [t["tool_name"] for t in row["tools"]]
    if row["status"] != expected["status"]:
        failures.append("wrong_terminal_state")
    waiting = (expected["status"] == row["status"] == "needs_input"
               and row.get("error") in {None, "missing_input"})
    if row.get("error") and not waiting:
        failures.append("run_error:" + row["error"])
    if waiting and not any(isinstance(q, str) and q.strip() for q in row.get("missing_inputs", [])):
        failures.append("missing_user_question")
    if any(value not in text for value in expected.get("contains", [])):
        failures.append("missing_expected_fact")
    if set(names) & set(expected.get("forbid_tools", [])):
        failures.append("unnecessary_tool")
    required = expected.get("require_any_tool", [])
    if required and not set(names) & set(required):
        failures.append("required_observation_not_obtained")
    if expected.get("evidence_required") and not any(
        t["tool_name"] == "dynamic_rag_search"
        and any(item.get("text") for item in t["result"].get("items", []))
        for t in row["tools"]
    ):
        failures.append("no_retrieved_body")
    if not row["stream_matches_answer"]:
        failures.append("stream_final_mismatch")
    if row.get("public_error_events"):
        failures.append("public_error_event")
    if any(c.get("within_budget") is False for c in row["calls"]):
        failures.append("request_over_budget")
    if re.search(r"#chunk-|\b(?:parse_version|next_cursor|tool_calls|unit_id)\b", text):
        failures.append("internal_metadata_in_answer")
    if expected.get("max_calls") and len(row["calls"]) > expected["max_calls"]:
        warnings.append("call_count_above_reference")
    if "answer_key" in expected:
        cleaned = text.replace("**", "")
        pairs = re.findall(
            r"(?m)^\s*(?:[-*]\s*)?(?:第)?(\d{1,2})\s*(?:题|[.、．:：)])\s*[:：]?\s*(?:答案\s*[:：]?\s*)?([A-D])(?:[.、．:：)\s]|$)",
            cleaned,
        )
        wanted = list(enumerate(expected["answer_key"], 1))
        actual = [(int(i), option) for i, option in pairs]
        if actual:
            if actual != wanted:
                failures.append("answer_key_or_order_mismatch")
        else:
            review.append("answer_format_needs_human_check")
    if "judgment_key" in expected:
        pairs = re.findall(
            r"(?m)^\s*(\d{1,2})[.、．)]\s*(?:(?:答案为|答案|判断)\s*[:：]?\s*)?(正确|错误|对|错|[a-dA-D])(?=[.、:：。)\s]|$)",
            text.replace("**", ""),
        )
        def normalize(value):
            return {"正确": "对", "错误": "错"}.get(value, value.upper())
        actual = [(int(number), normalize(value)) for number, value in pairs]
        wanted = [
            (i, normalize(value)) for i, value in enumerate(expected["judgment_key"], 1)
        ]
        if actual:
            if actual != wanted:
                failures.append("user_reference_key_mismatch")
        else:
            review.append("judgment_format_needs_human_check")
        review.append("compare_with_user_key_and_review_reasons_in_historical_context")
    return {
        "failures": failures,
        "warnings": warnings,
        "manual_checks": review,
        "machine_pass": not failures,
        "subjective_review_required": True,
    }


class FixtureRecord:
    """Explicitly synthetic fault/partial/empty injection, never a business DB."""

    def __init__(self, settings, mode):
        self.settings, self.mode = settings, mode
        self.spec = ToolSpec(
            name="read_test_record",
            description="读取本次测试提供的交接记录，record_id为记录编号。",
            parameters={
                "type": "object",
                "properties": {"record_id": {"type": "string"}},
                "required": ["record_id"],
            },
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute(self, request):
        if self.mode == "unavailable":
            raise PortError(self.settings.error("tool_failed"))
        return ActionResult(
            tool_name=request.tool_name,
            action_id=request.action_id,
            outcome="partial" if self.mode == "partial" else "empty",
            result={"status": self.mode, "time": "14:30", "pieces": None}
            if self.mode == "partial"
            else {"status": "no_match", "items": []},
        )


class CoreObserver(ObservedModel):
    async def decide_stream(self, request, on_delta):
        index = len(self.calls)
        try:
            return await super().decide_stream(request, on_delta)
        finally:
            if len(self.calls) > index:
                entry = self.calls[index]
                entry["message_chars"] = [
                    len(json.dumps(m, ensure_ascii=False)) for m in entry["messages"]
                ]
                budget = self.model.budget.plan(purpose=request.input.purpose)
                packet = self.model._payload(request)
                packet.update(stream=True, stream_options={"include_usage": True})
                estimate = self.model.budget.estimate(packet).tokens
                entry["budget"] = budget.model_dump(mode="json")
                entry["wire_estimated_tokens"] = estimate
                entry["within_budget"] = estimate <= min(budget.hard, budget.warning)


async def evaluate(args, corpus, work):
    settings, sink = load_settings(), MemoryEventSink()
    settings.model.thinking_mode = "none"
    settings.materials.cache_root = str(work / "parsed")
    corpus_path = Path(args.corpus)
    paths = write_fixtures(corpus, work, corpus_path.parent)
    rows = []
    output = Path(args.output)
    report = {
        "corpus_version": corpus["version"],
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "production_files_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path("backend/config/defaults.toml"),
                Path("backend/infrastructure/model/deepseek.py"),
                Path("backend/infrastructure/model/reading.py"),
            ]
        },
        "thinking_mode": settings.model.thinking_mode,
        "selection": args.cases,
        "repeats": args.repeats,
        "quality_status": "human_review_required",
        "token_scope": "main model events only; direct RAG reviewer requests excluded",
        "results": rows,
        "limitations": [
            "representative risks, not measured traffic coverage",
            "no browser test",
            "fault cases are synthetic injections",
        ],
    }

    def save():
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    start = perf_counter()
    async with open_harness(settings=settings, events=sink) as source:
        report["startup_seconds"] = round(perf_counter() - start, 3)
        raw_model = source.modules["reasoning"].model
        report["model"] = raw_model.settings.model.name
        observed = CoreObserver(raw_model)
        base_tools = {
            spec.name: (spec, source.modules["action"].tools[spec.name])
            for spec in source.tools
        }
        selected = (
            set(args.cases.split(","))
            if args.cases
            else {c["id"] for c in corpus["cases"]}
        )
        unknown = selected - {c["id"] for c in corpus["cases"]}
        if unknown:
            raise ValueError("unknown_case_ids:" + ",".join(sorted(unknown)))
        report["selected_case_ids"] = sorted(selected)
        for repeat in range(args.repeats):
            for case in corpus["cases"]:
                if case["id"] not in selected:
                    continue
                tools = dict(base_tools)
                if case.get("fault"):
                    injected = FixtureRecord(settings, case["fault"])
                    tools[injected.spec.name] = (injected.spec, injected)
                agent = build_harness(
                    model=observed,
                    model_context=source.model_context,
                    settings=settings,
                    events=sink,
                    streams=sink,
                    tools=tools,
                )
                session = "core-eval:" + uuid4().hex
                for number, turn in enumerate(case["turns"], 1):
                    text = turn["text"]
                    if case.get("inline_fixture"):
                        text += "\n\n以下为用户原始材料：\n" + fixture_text(
                            corpus["fixtures"][case["inline_fixture"]]
                        )
                    call_start, event_start, stream_start = (
                        len(observed.calls),
                        len(sink.events),
                        len(sink.stream_events),
                    )
                    began = perf_counter()
                    run = await agent.run(
                        RunInput(
                            text=text,
                            materials=[
                                MaterialInput(
                                    uri=str(paths[key]), material_id="fixture-" + key
                                )
                                for key in case.get("materials", [])
                            ],
                        ),
                        session_id=session,
                    )
                    calls, events, streams = (
                        observed.calls[call_start:],
                        sink.events[event_start:],
                        sink.stream_events[stream_start:],
                    )
                    # Save no auth headers, credentials, provider reasoning or business accounts.
                    row = {
                        "case": case["id"],
                        "repeat": repeat + 1,
                        "turn": number,
                        "family": case["family"],
                        "tier": case["tier"],
                        "risks": case["risks"],
                        "input": text,
                        "run_id": run.run_id,
                        "status": run.status,
                        "answer": run.answer,
                        "missing_inputs": run.missing_inputs,
                        "error": run.error.code if run.error else None,
                        "seconds": round(perf_counter() - began, 3),
                        "calls": calls,
                        "tools": run.execution_state.get("tool_results", []),
                        "tokens": sum(
                            e.total_tokens or 0 for e in events if e.module == "model"
                        ),
                        "usage_complete": all(
                            e.total_tokens is not None
                            for e in events
                            if e.module == "model"
                        ),
                        "stream_matches_answer": visible_text(streams)
                        == (run.answer or ""),
                        "public_error_events": sum(e.type == "error" for e in streams),
                        "delta_count": sum(e.type == "delta" for e in streams),
                        "expected": turn["expected"],
                        "rubric": turn["rubric"],
                    }
                    row["checks"] = check_result(turn["expected"], row)
                    rows.append(row)
                    save()
                    print(
                        json.dumps(
                            {
                                k: row[k]
                                for k in [
                                    "case",
                                    "repeat",
                                    "turn",
                                    "status",
                                    "seconds",
                                    "error",
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if calls and all(
                        c.get("error")
                        in {
                            "model_unavailable",
                            "model_quota_exhausted",
                            "model_timeout_or_cancelled",
                        }
                        for c in calls
                    ):
                        report["quality_status"] = "provider_unavailable"
                        save()
                        return 2
        report["machine_passed"] = sum(row["checks"]["machine_pass"] for row in rows)
        report["run_count"] = len(rows)
        report["model_calls"] = sum(len(row["calls"]) for row in rows)
        save()
    return 0 if all(row["checks"]["machine_pass"] for row in rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--cases", help="Comma-separated stable case IDs; omitted means all"
    )
    parser.add_argument(
        "--corpus",
        default=str(CORPUS),
        help="Versioned corpus JSON; defaults to core_v1",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live required; real model credit will be consumed")
    if args.repeats < 1:
        parser.error("positive repeats required")
    output = Path(args.output)
    if output.exists():
        parser.error("output already exists; choose a new path to preserve evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    task = Path(".ai_tmp")
    task.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="core-v1-", dir=task) as directory:
        code = asyncio.run(evaluate(args, corpus, Path(directory)))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
