"""Opt-in 30-scenario evaluation; user prompts never contain routing expectations."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from opensearch.opensearch_delivery.scripts.maritime_index import (
    bm25_body,
    rrf,
    vector_body,
)

from backend.bootstrap import build_harness, open_rag
from backend.config.settings import BACKEND_ROOT, load_settings
from backend.contracts.modules.dynamic_rag import Evidence
from backend.contracts.modules.loop import RunInput
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.benchmarks.run_rag import group_matches
from backend.tests.benchmarks.run_rag_harness import (
    AuditedModel,
    AuditedModule,
    inspect_trace,
)


async def traditional(rag, query, version, limit):
    """Pure single-pass BM25 + vector, same inferred query and version scope."""
    cfg = rag.settings.rag
    clauses = (
        [] if version == "all" else [{"term": {"is_current": version == "current"}}]
    )
    started = perf_counter()
    vector = await rag.search.encoder.encode(query)
    bm, ve = await asyncio.gather(
        rag.search._search(bm25_body(query, clauses, cfg.candidates)),
        rag.search._search(vector_body(vector, clauses, cfg.candidates)),
    )
    rows = rrf(bm, ve, size=limit, k=cfg.rrf_k)
    items = []
    for row in rows:
        value = dict(row["chunk"])
        for field in ("document_name", "article_no", "section_path"):
            value[field] = value.get(field) or ""
        value["version_date"] = value.get("version_date") or None
        items.append(Evidence.model_validate(value).model_dump(mode="json"))
    return {
        "seconds": perf_counter() - started,
        "query": query,
        "version_scope": version,
        "items": items,
    }


async def run(args):
    cfg = load_settings()
    path = Path(__file__).with_name("query_plan_cases.json")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    cases = [c for c in fixture["cases"] if not args.case or c["id"] in args.case]
    if not cases or args.output.exists():
        raise ValueError("empty_selection_or_output_exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    traces = args.output.parent / (args.output.stem + "-private-traces")
    traces.mkdir(exist_ok=False)
    corpus = {}
    groups = [g for c in cases for g in c["gold_groups"]]
    found = [False] * len(groups)
    with (BACKEND_ROOT.parent / "opensearch/corpus/manifest/chunks.jsonl").open(
        encoding="utf-8"
    ) as stream:
        for line in stream:
            row = json.loads(line)
            corpus[row["chunk_id"]] = (
                row["document_id"],
                hashlib.sha256(row["chunk_text"].encode()).hexdigest(),
            )
            for n, group in enumerate(groups):
                if not found[n] and row["document_id"] in group["documents"]:
                    found[n] = group_matches(Evidence.model_validate(row), group)
    if not all(found):
        raise ValueError("gold_evidence_missing_in_corpus")
    report = {
        "completion": "running",
        "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "profile": cfg.model.thinking_mode,
        "corpus_count": len(corpus),
        "records": [],
        "startup_seconds": None,
        "performance_targets": {
            "plain_seconds": fixture["plain_latency_target_seconds"],
            "rag_seconds": fixture["warm_latency_target_seconds"],
        },
    }

    def save():
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    sink = MemoryEventSink()
    start = perf_counter()
    async with (
        open_rag(settings=cfg, events=sink) as rag,
        open_model(settings=cfg, events=sink, role="main") as model,
    ):
        report["startup_seconds"] = perf_counter() - start
        report["model"] = model.settings.model.name
        print(
            json.dumps({"ready": True, "startup_seconds": report["startup_seconds"]}),
            flush=True,
        )
        for case in cases:
            trace = []
            harness = build_harness(
                model=AuditedModel(model, trace),
                settings=cfg,
                events=sink,
                rag=AuditedModule("dynamic_rag", rag, trace),
            )
            for name in tuple(harness.modules):
                harness.modules[name] = AuditedModule(
                    name, harness.modules[name], trace
                )
            session = "query-plan:" + uuid4().hex
            for turn, question in enumerate(case.get("turns", [case.get("text")]), 1):
                trace.clear()
                before = len(sink.events)
                result = await harness.run(RunInput(text=question), session_id=session)
                label = {**case, "text": question}
                if case["id"] == "14":
                    label["gold_groups"] = (
                        [{"documents": ["7633abbbcdac"], "contains": ["第五十条"]}]
                        if turn == 1
                        else [
                            {"documents": ["7633abbbcdac"], "contains": [a]}
                            for a in ("第五十一条", "第五十二条", "第五十三条")
                        ]
                    )
                audit = inspect_trace(trace, label, result, corpus, cfg)
                calls = [t for t in trace if t["module"] == "model"]
                initial = calls[0]["messages"] if calls else []
                audit["checks"]["initial_input_intact"] = any(
                    m["role"] == "user"
                    and json.loads(m["content"]).get("goal") == question
                    for m in initial
                )
                audit["program_pass"] = all(audit["checks"].values())
                retrieved = [t for t in trace if t["module"] == "dynamic_rag"]
                record = {
                    "case": case["id"],
                    "turn": turn,
                    "question": question,
                    "expected_routing": case["retrieval"],
                    "result": result.model_dump(mode="json"),
                    **audit,
                    "candidate_plans": [
                        a["arguments"]
                        for t in calls
                        for a in t["decision"].get("action_requests", [])
                    ],
                    "search_plans": [
                        s.get("plan")
                        for t in retrieved
                        for s in (t["response"].get("output") or {}).get("steps", [])
                    ],
                    "notices": [
                        n
                        for t in retrieved
                        for n in (t["response"].get("output") or {}).get("notices", [])
                    ],
                    "reported_model_tokens": sum(
                        e.total_tokens or 0 for e in sink.events[before:]
                    ),
                    "review_points": case["review_points"],
                    "warm_latency_within_target": result.elapsed_seconds
                    <= (
                        fixture["plain_latency_target_seconds"]
                        if not retrieved
                        else fixture["warm_latency_target_seconds"]
                    ),
                }
                if args.compare and retrieved:
                    value = retrieved[0]["request"]["input"]
                    baseline = await traditional(
                        rag,
                        value["query"],
                        value.get("version_scope", "all"),
                        cfg.rag.default_limit,
                    )
                    baseline["group_hits"] = [
                        any(
                            group_matches(Evidence.model_validate(row), g)
                            for row in baseline["items"]
                        )
                        for g in label["gold_groups"]
                    ]
                    record["traditional"] = baseline
                (traces / f"{case['id']}-{turn}.json").write_text(
                    json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                report["records"].append(record)
                save()
                print(
                    json.dumps(
                        {
                            "case": case["id"],
                            "turn": turn,
                            "status": result.status,
                            "seconds": round(result.elapsed_seconds, 3),
                            "rag_calls": audit["rag_calls"],
                            "pass": audit["program_pass"],
                            "failed_checks": [
                                k for k, v in audit["checks"].items() if not v
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if result.error and result.error.code in {
                    "model_rejected",
                    "credential_missing",
                }:
                    report["completion"] = "blocked_model"
                    save()
                    return
    report["completion"] = "complete"
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for paid model calls")
    asyncio.run(run(args))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
