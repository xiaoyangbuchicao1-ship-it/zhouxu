"""Explicit live benchmark; identical first-pass retrieval and answer prompt in both arms."""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
import numpy as np

from backend.bootstrap import open_rag
from backend.config.settings import BACKEND_ROOT, load_settings
from backend.contracts.modules.dynamic_rag import RagInput
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.modules.dynamic_rag.service import DynamicRag


def normalized(text):
    return "".join(text.split())


def group_matches(item, group):
    return item.document_id in group["documents"] and all(
        normalized(part) in normalized(item.chunk_text) for part in group["contains"]
    )


def citation_metrics(text, valid_ids):
    # Identity validity and presentation syntax are different measurements.
    # A real ID written as [chunk_id: ID] is not a fabricated reference.
    cited = re.findall(r"\b([0-9a-f]+#chunk-\d+)\b", text)
    canonical = re.findall(r"\[([0-9a-f]+#chunk-\d+)\]", text)
    return {
        "citation_ids": cited,
        "invalid_citation_ids": [key for key in cited if key not in valid_ids],
        "noncanonical_citation_count": len(cited) - len(canonical),
    }


async def generate(model, query, items, fixture):
    started = perf_counter()
    response = await model.client.post(model.settings.model.endpoint, json={
        "model": model.settings.model.name,
        "messages": [
            {"role": "system", "content": fixture["answer_prompt"]},
            {"role": "user", "content": json.dumps({
                "query": query,
                "evidence": [item.model_dump(exclude={"retrieval_score"}) for item in items],
            }, ensure_ascii=False)},
        ],
        "temperature": model.settings.model.temperature,
        "max_tokens": fixture["answer_max_tokens"],
        "thinking": {"type": "disabled"},
    })
    response.raise_for_status()
    data = response.json()
    choice = data["choices"][0]
    return {
        "text": choice["message"]["content"],
        "finish_reason": choice["finish_reason"],
        "tokens": (data.get("usage") or {}).get("total_tokens"),
        "seconds": perf_counter() - started,
    }


async def run(args):
    settings = load_settings()
    fixture_path = Path(__file__).with_name("rag_cases.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            raise ValueError("unknown_case")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("output_already_exists_use_a_new_run_file")
    output.parent.mkdir(parents=True, exist_ok=True)
    repeats = args.repeats or fixture["repeats"]

    # Independently validate labels against the corpus before observing retrieval.
    groups = [group for case in cases for group in case["gold_groups"]]
    label_matches = [0 for _ in groups]
    document_ids = {doc for group in groups for doc in group["documents"]}
    from backend.contracts.modules.dynamic_rag import Evidence
    corpus = {}
    manifest = BACKEND_ROOT.parent / "opensearch/corpus/manifest/chunks.jsonl"
    manifest_hash = hashlib.sha256()
    with manifest.open("rb") as stream:
        for line in stream:
            manifest_hash.update(line)
            row = json.loads(line)
            # All returned evidence is checked against this local identity/text map.
            corpus[row["chunk_id"]] = (row["document_id"], row["chunk_text"])
            if row["document_id"] in document_ids:
                item = Evidence.model_validate(row)
                for i, group in enumerate(groups):
                    label_matches[i] += int(group_matches(item, group))
    if not all(label_matches):
        raise ValueError(f"gold_label_not_in_corpus:{[i for i, n in enumerate(label_matches) if not n]}")
    report = {
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "manifest_sha256": manifest_hash.hexdigest(), "corpus_count": len(corpus),
        "model": settings.model.name,
        "model_profile": settings.model.thinking_mode,
        "rag_config": settings.rag.model_dump(exclude={"credential_file", "password_name", "username"}),
        "repeats": repeats, "retrieval_only": args.retrieval_only,
        "completion": "running", "records": [],
    }
    began = perf_counter()
    async with open_rag(settings=settings, events=MemoryEventSink(), dynamic=not args.retrieval_only) as dynamic:
        report["startup_seconds"] = perf_counter() - began
        print(json.dumps({"ready": True, "startup_seconds": report["startup_seconds"]}), flush=True)
        # Verify local query encoding against the delivered fixed vector.
        if dynamic.search.encoder is None:
            raise ValueError("hybrid_benchmark_requires_real_encoder")
        vector = await dynamic.search.encoder.encode("无船承运业务需要备案吗")
        known = np.load(BACKEND_ROOT.parent / "opensearch/opensearch_delivery/queries/demo_vectors.npy")[0]
        report["query_cosine_to_delivery"] = float(np.dot(vector, known) / (np.linalg.norm(vector) * np.linalg.norm(known)))
        if report["query_cosine_to_delivery"] < 0.999:
            raise ValueError("query_encoder_mismatch")
        baseline = DynamicRag(dynamic.search, settings)
        # Preheat database paths separately from the paired measurement.
        await baseline.handle(ModuleRequest(run_id="warmup", call_id="warmup", input=RagInput(query=settings.rag.warmup_text)))
        async with AsyncExitStack() as stack:
            model = None
            if not args.retrieval_only:
                model = await stack.enter_async_context(open_model(settings=settings, events=MemoryEventSink(), role="main"))
                report["model"] = model.settings.model.name
            for repetition in range(repeats):
                for position, case in enumerate(cases):
                    modes = [("traditional", baseline), ("dynamic", dynamic)]
                    if args.retrieval_only:
                        modes = [("traditional_retrieval_only", baseline)]
                    if (position + repetition) % 2:
                        modes.reverse()
                    for mode, service in modes:
                        request = ModuleRequest(
                            run_id=uuid4().hex, call_id=uuid4().hex,
                            input=RagInput(query=case["query"], filters=case["filters"]),
                        )
                        result = await service.handle(request)
                        record = {"case": case["id"], "category": case["category"], "repetition": repetition, "mode": mode,
                                  "response": result.model_dump(mode="json")}
                        if result.output:
                            items = result.output.items
                            record["group_hits"] = [any(group_matches(item, group) for item in items) for group in case["gold_groups"]]
                            record["source_matches"] = all(corpus.get(item.chunk_id) == (item.document_id, item.chunk_text) for item in items)
                            # Both modes get exactly the same generator inputs except evidence.
                            if model is not None and result.status == "ok":
                                try:
                                    answer = await generate(model, case["query"], items, fixture)
                                    record["answer"] = answer
                                    ids = {item.chunk_id for item in items}
                                    record.update(citation_metrics(answer["text"], ids))
                                except httpx.HTTPStatusError as exc:
                                    record["answer_error"] = f"http_{exc.response.status_code}"
                                    report["completion"] = "blocked_model"
                                except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError):
                                    record["answer_error"] = "model_response_unavailable"
                                    report["completion"] = "blocked_model"
                        report["records"].append(record)
                        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(json.dumps({
                            "case": case["id"], "mode": mode, "repeat": repetition,
                            "status": result.status,
                            "rounds": len(result.output.steps) if result.output else None,
                            "seconds": round(result.output.elapsed_seconds, 3) if result.output else None,
                            "groups": record.get("group_hits"),
                            "stop": result.output.stop_reason if result.output else None,
                        }, ensure_ascii=False), flush=True)
                        if report["completion"] == "blocked_model":
                            print(json.dumps({"blocked": record["answer_error"]}), flush=True)
                            return
    report["completion"] = "complete"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow database and paid model calls")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--retrieval-only", action="store_true", help="Database baseline only; no model calls or dynamic-quality claims")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required")
    if args.repeats is not None and args.repeats < 1:
        parser.error("--repeats must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
