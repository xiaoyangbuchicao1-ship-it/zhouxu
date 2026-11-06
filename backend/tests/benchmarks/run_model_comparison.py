"""Explicit comparison using existing Harness, fixtures and search, with private traces."""

import argparse
import asyncio
import hashlib
import json
import sys
import tomllib
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from backend.bootstrap import build_harness, open_rag
from backend.config.settings import BACKEND_ROOT, ModelProfile, load_key, load_settings
from backend.contracts.modules.dynamic_rag import Evidence
from backend.contracts.modules.loop import RunInput
from backend.contracts.schemas.core import ModuleResponse
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.search.deepseek_review import DeepSeekEvidenceReviewer
from backend.modules.dynamic_rag.service import DynamicRag
from backend.tests.benchmarks.relay_responses import ResponsesTransport, read_config
from backend.tests.benchmarks.run_rag import group_matches
from backend.tests.benchmarks.run_rag_harness import (
    AuditedModel,
    AuditedModule,
    inspect_trace,
)


async def run(args):
    cfg = load_settings()
    relay = read_config(args.relay_config)
    relay_cfg = cfg.model_copy(deep=True)
    relay_cfg.model.endpoint = relay.endpoint
    relay_cfg.model.allowed_hosts = [httpx.URL(relay.endpoint).host]
    relay_cfg.model.name = relay.model
    if args.profiles:
        with args.profiles.open("rb") as stream:
            candidates = {
                name: ModelProfile.model_validate(value)
                for name, value in tomllib.load(stream).items()
            }
        if not candidates:
            raise ValueError("no_candidate_profiles")
        cfg.model_routing.profiles = candidates
        cfg.model_routing.relay_config_file = str(args.relay_config.resolve())
    fixture_path = args.fixture or Path(__file__).with_name("rag_harness_cases.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = [c for c in fixture["cases"] if not args.case or c["id"] in args.case]
    if not cases or args.output.exists():
        raise ValueError("unknown_cases_or_existing_output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    trace_dir = args.output.parent / (args.output.stem + "-private-traces")
    trace_dir.mkdir(exist_ok=False)
    corpus = {}
    groups = [group for case in cases for group in case["gold_groups"]]
    found = [False] * len(groups)
    docs = {doc for group in groups for doc in group["documents"]}
    with (BACKEND_ROOT.parent / "opensearch/corpus/manifest/chunks.jsonl").open(
        encoding="utf-8"
    ) as stream:
        for line in stream:
            row = json.loads(line)
            corpus[row["chunk_id"]] = (
                row["document_id"],
                hashlib.sha256(row["chunk_text"].encode()).hexdigest(),
            )
            if row["document_id"] in docs:
                item = Evidence.model_validate(row)
                for index, group in enumerate(groups):
                    found[index] |= group_matches(item, group)
    if not all(found):
        raise ValueError("fixture_not_supported_by_corpus")
    report = {
        "completion": "running",
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "models": {"deepseek": cfg.model.name, "relay": relay.model},
        "relay_effort": relay.effort,
        "corpus_records": len(corpus),
        "warm_target_seconds": fixture["warm_latency_target_seconds"],
        "budgets": {
            "runtime": cfg.runtime.model_dump(),
            "rag_tool": cfg.rag_tool.model_dump(exclude={"description"}),
            "model_max_output_tokens": cfg.model.max_tokens,
            "review_max_output_tokens": cfg.rag.review_max_tokens,
        },
        "records": [],
    }
    if args.profiles:
        report["profiles"] = {
            name: profile.model_dump(exclude_none=True)
            for name, profile in candidates.items()
        }

    def save():
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    sink = MemoryEventSink()
    started = perf_counter()
    async with AsyncExitStack() as stack:
        base_rag = await stack.enter_async_context(
            open_rag(settings=cfg, events=sink, dynamic=False)
        )
        report["encoder_startup_seconds"] = perf_counter() - started
        report["hybrid_encoder_active"] = base_rag.search.encoder is not None
        models, settings, reviewers, transports = {}, {}, {}, {}
        if args.profiles:
            for name in candidates:
                selected = cfg.model_copy(deep=True)
                selected.model_routing.active_profile = name
                main_model = await stack.enter_async_context(
                    open_model(
                        settings=selected,
                        events=sink,
                        role="main",
                        collect_metrics=True,
                    )
                )
                review_model = await stack.enter_async_context(
                    open_model(
                        settings=selected,
                        events=sink,
                        role="review",
                        collect_metrics=True,
                    )
                )
                models[name], settings[name] = main_model, selected
                reviewers[name] = DeepSeekEvidenceReviewer(review_model)
                transports[name] = [
                    m.responses_transport
                    for m in [main_model, review_model]
                    if hasattr(m, "responses_transport")
                ]
        else:
            ds_model = await stack.enter_async_context(
                DeepSeekModel(cfg, load_key(cfg), sink)
            )
            relay_transport = ResponsesTransport(relay)
            relay_model = await stack.enter_async_context(
                DeepSeekModel(relay_cfg, relay.key, sink, transport=relay_transport)
            )
            models = {"deepseek": ds_model, "relay": relay_model, "mixed": relay_model}
            settings = {"deepseek": cfg, "relay": relay_cfg, "mixed": relay_cfg}
            reviewers = {
                "deepseek": DeepSeekEvidenceReviewer(ds_model),
                "relay": DeepSeekEvidenceReviewer(relay_model),
                "mixed": DeepSeekEvidenceReviewer(ds_model),
            }
            transports = {name: [relay_transport] for name in models}
        print(
            json.dumps(
                {
                    "ready": True,
                    "encoder_startup_seconds": report["encoder_startup_seconds"],
                }
            ),
            flush=True,
        )
        schedule = [(case, 1) for case in cases]
        if args.repeat_focused:
            schedule += [
                (case, 2)
                for case in cases
                if case["id"] in fixture.get("repeat_cases", [])
            ]
        for index, (case, repeat) in enumerate(schedule):
            # Alternate order to reduce systematic cache/order advantage.
            selected_modes = (
                list(candidates) if args.profiles else case.get("modes", args.mode)
            )
            offset = index % len(selected_modes)
            modes = selected_modes[offset:] + selected_modes[:offset]
            for mode in modes:
                trace = []
                for transport in transports[mode]:
                    transport.reset_context()
                selected_cfg = settings[mode]
                rag = DynamicRag(base_rag.search, selected_cfg, reviewers[mode])
                if case.get("inject_search_failure"):

                    class Unavailable:
                        async def handle(self, request):
                            return ModuleResponse(
                                status="failed", error=cfg.error("rag_search_failed")
                            )

                    rag = Unavailable()
                harness = build_harness(
                    model=AuditedModel(models[mode], trace),
                    settings=selected_cfg,
                    events=sink,
                    rag=AuditedModule("dynamic_rag", rag, trace),
                )
                for name in tuple(harness.modules):
                    harness.modules[name] = AuditedModule(
                        name, harness.modules[name], trace
                    )
                event_start = len(sink.events)
                call_starts = [len(t.calls) for t in transports[mode]]
                result = await harness.run(
                    RunInput(text=case["text"], facts=case.get("facts", {})),
                    session_id="comparison:" + uuid4().hex,
                )
                observed = inspect_trace(trace, case, result, corpus, selected_cfg)
                text = result.answer or "\n".join(result.missing_inputs)
                entry = {
                    "case": case["id"],
                    "repeat": repeat,
                    "mode": mode,
                    "question": case["text"],
                    "result": result.model_dump(mode="json"),
                    **observed,
                    "answer_chars": len(text),
                    "warm_latency_within_target": result.elapsed_seconds
                    <= fixture["warm_latency_target_seconds"],
                    "decision_tokens": sum(
                        e.total_tokens or 0 for e in sink.events[event_start:]
                    ),
                    "review_tokens": sum(
                        s.get("review_tokens") or 0
                        for t in trace
                        if t["module"] == "dynamic_rag"
                        for s in (t["response"].get("output") or {}).get("steps", [])
                    ),
                    "relay_call_metrics": [
                        entry
                        for transport, start in zip(transports[mode], call_starts)
                        for entry in transport.calls[start:]
                    ],
                }
                report["records"].append(entry)
                stem = (
                    mode
                    + "-"
                    + case["id"]
                    + (f"-r{repeat}" if args.profiles or args.repeat_focused else "")
                )
                (trace_dir / (stem + ".json")).write_text(
                    json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                save()
                print(
                    json.dumps(
                        {
                            "case": case["id"],
                            "repeat": repeat,
                            "mode": mode,
                            "status": result.status,
                            "seconds": round(result.elapsed_seconds, 3),
                            "pass": observed["program_pass"],
                            "chars": len(text),
                            "rag_rounds": observed["rag_rounds"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    report["completion"] = "complete"
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--relay-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument(
        "--profiles",
        type=Path,
        help="Candidate role profiles; uses the production model factory",
    )
    parser.add_argument("--repeat-focused", action="store_true")
    parser.add_argument("--case", action="append")
    parser.add_argument(
        "--mode",
        nargs="+",
        choices=["deepseek", "relay", "mixed"],
        default=["deepseek", "relay"],
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for paid model and read-only corpus calls")
    asyncio.run(run(args))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
