"""Opt-in real model/corpus bridge evaluation; private traces for reviewer inspection."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from backend.bootstrap import build_harness, open_rag
from backend.config.settings import BACKEND_ROOT, load_settings
from backend.contracts.modules.dynamic_rag import Evidence
from backend.contracts.modules.loop import ActionRequest, RunInput
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.tools.rag import RagSearchTool
from backend.modules.dynamic_rag.service import DynamicRag
from backend.tests.benchmarks.run_rag import citation_metrics, generate, group_matches


class AuditedModule:
    def __init__(self, name, module, trace):
        self.name, self.module, self.trace = name, module, trace

    async def handle(self, request):
        start = perf_counter()
        response = await self.module.handle(request)
        self.trace.append(
            {
                "module": self.name,
                "request": request.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "elapsed_seconds": perf_counter() - start,
            }
        )
        return response


class AuditedModel:
    def __init__(self, model, trace):
        self.model, self.trace = model, trace

    async def decide(self, request):
        messages = self.model.trace_messages(request)
        result = await self.model.decide(request)
        self.trace.append(
            {
                "module": "model",
                "run_id": request.run_id,
                "call_id": request.call_id,
                "messages": messages,
                "decision": result.model_dump(mode="json"),
            }
        )
        return result


def inspect_trace(trace, case, result, corpus, settings):
    retrievals = [t for t in trace if t["module"] == "dynamic_rag"]
    model_calls = [t for t in trace if t["module"] == "model"]
    action_results = [
        t["response"]["output"]
        for t in trace
        if t["module"] == "action" and t["response"].get("output")
    ]
    evidence = [
        Evidence.model_validate(i)
        for a in action_results
        for i in (a["result"].get("items") or [])
    ]
    valid_ids = {i.chunk_id for i in evidence}
    facts = RunInput(text=case["text"], facts=case.get("facts", {})).model_dump()
    checks = {
        "status_expected": result.status in case["expected_statuses"],
        "initial_model_before_retrieval": not retrievals
        or next(i for i, t in enumerate(trace) if t["module"] == "model")
        < next(i for i, t in enumerate(trace) if t["module"] == "dynamic_rag"),
        "initial_input_intact": bool(model_calls) and any(
            m["role"] == "user" and json.loads(m["content"]).get("goal") == facts["text"]
            for m in model_calls[0]["messages"]
        ),
        "retrieval_routing": (
            not retrievals
            if case["retrieval"] == "never"
            else bool(retrievals)
            if case["retrieval"] == "required"
            else True
        ),
        "single_trusted_system": all(
            sum(m["role"] == "system" for m in t["messages"]) == 1 for t in model_calls
        ),
        "scope_consistent": all(
            t["request"]["run_id"] == result.run_id for t in retrievals
        ),
        "corpus_identity_and_text": all(
            corpus.get(i.chunk_id)
            == (i.document_id, hashlib.sha256(i.chunk_text.encode()).hexdigest())
            for i in evidence
        ),
        "bounded_rag_calls": len(retrievals) <= settings.rag_tool.max_calls_per_run,
        "tool_pairs_complete": True,
        "selected_evidence_reaches_model": True,
    }
    for entry in model_calls:
        ids = [c["id"] for m in entry["messages"] for c in m.get("tool_calls", [])]
        results = [m["tool_call_id"] for m in entry["messages"] if m["role"] == "tool"]
        checks["tool_pairs_complete"] &= len(ids) == len(set(ids)) and sorted(
            ids
        ) == sorted(results)
    for action in action_results:
        provided = action["result"].get("items", [])
        matching = [
            json.loads(m["content"])
            for t in model_calls
            for m in t["messages"]
            if m["role"] == "tool" and m["tool_call_id"] == action["action_id"]
        ]
        if provided:
            checks["selected_evidence_reaches_model"] &= any(
                m.get("items") == provided for m in matching
            )
    citations = citation_metrics(result.answer or "", valid_ids)
    checks["no_fabricated_citation_ids"] = not citations["invalid_citation_ids"]
    if case["gold_groups"]:
        checks["has_evidence_citations"] = bool(citations["citation_ids"])
        checks["gold_evidence_groups_present"] = all(
            any(group_matches(i, group) for i in evidence)
            for group in case["gold_groups"]
        )
    return {
        "checks": checks,
        "program_pass": all(checks.values()),
        **citations,
        "group_hits": [
            any(group_matches(i, g) for i in evidence) for g in case["gold_groups"]
        ],
        "evidence_ids": sorted(valid_ids),
        "model_calls": len(model_calls),
        "rag_calls": len(retrievals),
        "rag_rounds": [
            len((t["response"].get("output") or {}).get("steps", []))
            for t in retrievals
        ],
        "rag_queries": [t["request"]["input"]["query"] for t in retrievals],
        "rag_stops": [
            (t["response"].get("output") or {}).get("stop_reason") for t in retrievals
        ],
    }


async def run(args):
    settings = load_settings()
    fixture_path = Path(__file__).with_name("rag_harness_cases.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = [c for c in fixture["cases"] if not args.case or c["id"] in args.case]
    if not cases:
        raise ValueError("unknown_case")
    if args.output.exists():
        raise ValueError("output_already_exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    trace_dir = args.output.parent / (args.output.stem + "-private-traces")
    trace_dir.mkdir(exist_ok=False)
    corpus = {}
    groups = [g for c in cases for g in c["gold_groups"]]
    matches = [False] * len(groups)
    docs = {d for g in groups for d in g["documents"]}
    with (BACKEND_ROOT.parent / "opensearch/corpus/manifest/chunks.jsonl").open(
        encoding="utf-8"
    ) as source:
        for line in source:
            item = json.loads(line)
            corpus[item["chunk_id"]] = (
                item["document_id"],
                hashlib.sha256(item["chunk_text"].encode()).hexdigest(),
            )
            if item["document_id"] in docs:
                evidence = Evidence.model_validate(item)
                for index, group in enumerate(groups):
                    matches[index] |= group_matches(evidence, group)
    if not all(matches):
        raise ValueError("fixture_not_supported_by_corpus")
    report = {
        "model": settings.model.name,
        "thinking_mode": settings.model.thinking_mode,
        "corpus_records": len(corpus),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "records": [],
        "comparisons": [],
        "completion": "running",
    }

    def save():
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    startup = perf_counter()
    sink = MemoryEventSink()
    async with open_rag(settings=settings, events=sink) as rag:
        report["startup_seconds"] = perf_counter() - startup
        report["hybrid_encoder_active"] = rag.search.encoder is not None
        print(
            json.dumps(
                {
                    "ready": True,
                    "startup_seconds": report["startup_seconds"],
                    "hybrid_encoder_active": report["hybrid_encoder_active"],
                }
            ),
            flush=True,
        )
        async with open_model(settings=settings, events=sink, role="main") as model:
            report["model"] = model.settings.model.name
            for case in cases:
                trace = []
                actual_rag = rag
                if case.get("inject_search_failure"):

                    class FailedSearch:
                        async def handle(self, request):
                            return ModuleResponse(
                                status="failed",
                                error=settings.error("rag_search_failed"),
                            )

                    actual_rag = FailedSearch()
                observed_rag = AuditedModule("dynamic_rag", actual_rag, trace)
                harness = build_harness(
                    model=AuditedModel(model, trace),
                    settings=settings,
                    events=sink,
                    rag=observed_rag,
                )
                for name in tuple(harness.modules):
                    harness.modules[name] = AuditedModule(
                        name, harness.modules[name], trace
                    )
                before = len(sink.events)
                result = await harness.run(
                    RunInput(text=case["text"], facts=case.get("facts", {})),
                    session_id=f"evaluation:{case['id']}:{uuid4().hex}",
                )
                observed = inspect_trace(trace, case, result, corpus, settings)
                events = sink.events[before:]
                observed["reported_reasoning_tokens"] = sum(
                    e.total_tokens or 0 for e in events
                )
                observed["reported_review_tokens"] = sum(
                    step.get("review_tokens") or 0
                    for t in trace
                    if t["module"] == "dynamic_rag"
                    for step in (t["response"].get("output") or {}).get("steps", [])
                )
                observed["warm_latency_within_target"] = (
                    result.elapsed_seconds <= fixture["warm_latency_target_seconds"]
                )
                report["records"].append(
                    {
                        "case": case["id"],
                        "question": case["text"],
                        "result": result.model_dump(mode="json"),
                        **observed,
                    }
                )
                (trace_dir / (case["id"] + ".json")).write_text(
                    json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                save()
                print(
                    json.dumps(
                        {
                            "case": case["id"],
                            "status": result.status,
                            "seconds": round(result.elapsed_seconds, 3),
                            "program_pass": observed["program_pass"],
                            "groups": observed["group_hits"],
                            "rag_rounds": observed["rag_rounds"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                # Optional paired check: identical inferred query/filters, output
                # budget, answer model/prompt. Not the end-to-end latency comparison.
                retrievals = [t for t in trace if t["module"] == "dynamic_rag"]
                if args.compare and case.get("compare_single_pass") and retrievals:
                    original = retrievals[0]["request"]["input"]
                    baseline = RagSearchTool(DynamicRag(rag.search, settings), settings)
                    base = await baseline.execute_context(
                        ModuleRequest(
                            run_id="baseline",
                            call_id=uuid4().hex,
                            input=ActionRequest(
                                tool_name=baseline.spec.name, arguments=original
                            ),
                        )
                    )
                    dynamic_actions = [
                        t["response"]["output"]["result"]
                        for t in trace
                        if t["module"] == "action" and t["response"].get("output")
                    ]
                    answer_fixture = json.loads(
                        Path(__file__)
                        .with_name("rag_cases.json")
                        .read_text(encoding="utf-8")
                    )
                    for mode, data in [
                        ("traditional_single_pass", base.result),
                        ("dynamic", dynamic_actions[0]),
                    ]:
                        items = [Evidence.model_validate(i) for i in data["items"]]
                        answer = await generate(
                            model, case["text"], items, answer_fixture
                        )
                        report["comparisons"].append(
                            {
                                "case": case["id"],
                                "mode": mode,
                                "inferred_query": original,
                                "answer": answer,
                                "group_hits": [
                                    any(group_matches(i, g) for i in items)
                                    for g in case["gold_groups"]
                                ],
                                **citation_metrics(
                                    answer["text"], {i.chunk_id for i in items}
                                ),
                            }
                        )
                    save()
                if (
                    result.status == "failed"
                    and result.error
                    and result.error.code in {"model_rejected", "credential_missing"}
                ):
                    report["completion"] = "blocked_model"
                    save()
                    return
    report["completion"] = "complete"
    report["program_pass_count"] = sum(r["program_pass"] for r in report["records"])
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live explicitly authorizes model and read-only corpus calls")
    asyncio.run(run(args))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
