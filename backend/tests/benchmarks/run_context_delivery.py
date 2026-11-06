"""Paid comparison with real per-step wire evidence and independent visibility.

Can load the detached baseline via --source-root. No account/database writes;
all fixtures and parsing caches remain inside --workdir. Provider continuation
is used only by production code/private checkpoints and redacted from evidence.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true")
parser.add_argument("--source-root", type=Path, default=ROOT)
parser.add_argument("--workdir", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--mode", choices=["none", "low", "high"], default="none")
parser.add_argument("--cases", default="")
parser.add_argument("--without-inventory", action="store_true")
parser.add_argument("--repeat", type=int, default=1)


def union_length(spans):
    end = count = 0
    for a, b in sorted(spans):
        if b > end:
            count += b - max(a, end)
            end = b
    return count


def visibility(request, catalog):
    ranges = defaultdict(list)
    locations = defaultdict(list)
    for item in request.input.perceived.items:
        if item.status not in {"available", "partial"}:
            continue
        body = (
            item.body.get("result")
            if item.type == "tool_result"
            else item.body
            if item.type == "resource"
            else None
        )
        if not isinstance(body, dict) or not body.get("units"):
            continue
        key = body.get("material_id")
        parsed = catalog.runs.get(request.run_id, {}).get(key)
        if parsed is None or body.get("parse_version") != parsed.manifest.parse_version:
            continue
        present = {u.get("unit_id"): len(u.get("text", "")) for u in body["units"]}
        for span in body.get("coverage", []):
            if (
                span["unit_id"] not in present
                or span["char_end"] - span["char_start"] > present[span["unit_id"]]
            ):
                continue
            ranges[(key, span["unit_id"])].append(
                (span["char_start"], span["char_end"])
            )
            locations[key].append(span)
    materials = []
    for key, p in catalog.runs.get(request.run_id, {}).items():
        if p.manifest.parser == "tool-result":
            continue
        total = sum(len(u.text) for u in p.units)
        shown = sum(
            min(len(u.text), union_length(ranges.get((key, u.unit_id), [])))
            for u in p.units
        )
        materials.append(
            {
                "material_id": key,
                "name": p.manifest.name,
                "parse_status": p.manifest.status,
                "parse_complete": p.manifest.complete,
                "total_chars": total,
                "visible_chars": shown,
                "full_text_visible": total > 0 and shown == total,
                "missing_ranges": p.manifest.missing_ranges,
                "visible_ranges": locations[key],
            }
        )
    return materials


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, inner, row):
        self.inner, self.row, self.buffer = inner, row, b""

    async def __aiter__(self):
        async for chunk in self.inner:
            self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.startswith(b"data:"):
                    continue
                try:
                    value = json.loads(line[5:].strip())
                except (ValueError, UnicodeDecodeError):
                    continue
                if value.get("usage"):
                    self.row["usage"] = value["usage"]
                choice = (value.get("choices") or [{}])[0]
                if choice.get("finish_reason"):
                    self.row["finish_reason"] = choice["finish_reason"]
                delta = choice.get("delta") or {}
                self.row["reasoning_chars"] = self.row.get("reasoning_chars", 0) + len(
                    delta.get("reasoning_content") or ""
                )
            yield chunk

    async def aclose(self):
        await self.inner.aclose()


async def main(args):
    if not args.live:
        raise ValueError("--live is required; this consumes model credit")
    if args.output.exists():
        raise ValueError("evidence output already exists")
    sys.path.insert(0, str(args.source_root.resolve()))
    import httpx

    from backend.bootstrap import build_harness, open_harness
    from backend.config.settings import load_settings
    from backend.contracts.events.streaming import visible_text
    from backend.contracts.model_context import DeploymentLimits
    from backend.contracts.modules.loop import RunInput
    from backend.contracts.modules.materials import MaterialInput
    from backend.contracts.modules.memory import MemoryRecord
    from backend.infrastructure.model.factory import open_model
    from backend.infrastructure.observability.memory import MemoryEventSink
    from backend.tests.scenarios.run_core import (
        check_result,
        fixture_text,
        write_fixtures,
    )

    settings = load_settings()
    baseline = not hasattr(settings.model, "thinking_mode")
    if baseline and args.mode != "none":
        raise ValueError(
            "baseline thinking protocol is unsupported; do not silently modify baseline"
        )
    if not baseline:
        settings.model.thinking_mode = args.mode
        settings.context_policy.inventory_enabled = not args.without_inventory
    settings.runtime.max_rounds = 50
    settings.model.credential_file = str(ROOT / "backend/.env")
    settings.rag.credential_file = str(ROOT / "opensearch/opensearch_delivery/dev/.env")
    args.workdir.mkdir(parents=True, exist_ok=True)
    settings.materials.cache_root = str(args.workdir / "parsed")
    spec = json.loads(
        (ROOT / "backend/tests/scenarios/context_delivery_v1.json").read_text(
            encoding="utf-8"
        )
    )
    corpus_path = ROOT / "backend/tests/scenarios" / spec["source_corpus"]
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    paths = write_fixtures(corpus, args.workdir, corpus_path.parent)
    selected = (
        set(args.cases.split(","))
        if args.cases
        else set(
            spec["core_cases"]
            + [p["id"] for p in spec["professional"]]
            + [spec["pressure"]["id"]]
        )
    )
    jobs = [
        c
        for c in corpus["cases"]
        if c["id"] in spec["core_cases"] and c["id"] in selected
    ]
    professional_file = (
        ROOT / "backend/tests/scenarios/fixtures/maritime_questions.docx"
    )
    for case in spec["professional"]:
        if case["id"] in selected:
            jobs.append(
                {
                    "id": case["id"],
                    "professional": True,
                    "turns": [
                        {"text": case["text"], "expected": {"status": "completed"}}
                    ],
                }
            )
    pressure = spec["pressure"]
    if pressure["id"] in selected:
        jobs.append(
            {
                "id": pressure["id"],
                "pressure": True,
                "materials": [pressure["fixture"]],
                "turns": [
                    {
                        "text": pressure["text"],
                        "expected": {"status": "completed", "contains": ["海风", "80"]},
                    }
                ],
            }
        )
    if selected != {c["id"] for c in jobs}:
        raise ValueError("unknown test selection")
    sink = MemoryEventSink()
    report = {
        "version": spec["version"],
        "source_root": str(args.source_root),
        "baseline": baseline,
        "mode": args.mode,
        "inventory_enabled": not baseline and not args.without_inventory,
        "round_limit": 50,
        "rows": [],
        "limitations": [
            "not browser upload/E2E",
            "synthetic fixtures explicitly identified",
            "professional key is user reference, not legal authority",
            "trace excludes private reasoning",
            "small samples; no population accuracy claim",
        ],
    }
    report["corpus_sha256"] = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    report["professional_source_sha256"] = hashlib.sha256(
        professional_file.read_bytes()
    ).hexdigest()
    report["source_sha256"] = {
        name: hashlib.sha256((args.source_root / name).read_bytes()).hexdigest()
        for name in [
            "backend/config/defaults.toml",
            "backend/infrastructure/model/deepseek.py",
            "backend/modules/perception/service.py",
        ]
    }

    def save():
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    class WireObserver(httpx.AsyncBaseTransport):
        def __init__(self, inner, owner):
            self.inner, self.owner = inner, owner

        async def handle_async_request(self, request):
            packet = json.loads(request.content)
            row = self.owner.active
            if row is not None:
                row["wire_messages"] = [
                    {k: v for k, v in m.items() if k != "reasoning_content"}
                    for m in packet["messages"]
                ]
                row["protocol"] = {
                    "model": packet["model"],
                    "thinking": packet.get("thinking"),
                    "effort": packet.get("reasoning_effort"),
                    "max_tokens": packet["max_tokens"],
                    "tool_choice": packet.get("tool_choice"),
                    "tools": [t["function"]["name"] for t in packet.get("tools", [])],
                    "private_replay_chars": sum(
                        len(m.get("reasoning_content") or "")
                        for m in packet["messages"]
                    ),
                }
                row["wire_tokens_estimate"] = self.owner.model.budget.estimate(
                    packet
                ).tokens
                row["budget_warning"] = self.owner.model.budget.plan(
                    purpose=row["purpose"]
                ).warning
                assert row["wire_tokens_estimate"] <= row["budget_warning"]
            response = await self.inner.handle_async_request(request)
            if row is not None:
                row["http_status"] = response.status_code
                if packet.get("stream") and response.is_success:
                    response.stream = ResponseStream(response.stream, row)
                elif response.is_success:
                    await response.aread()
                    value = response.json()
                    row["usage"] = value.get("usage")
                    choice = (value.get("choices") or [{}])[0]
                    row["finish_reason"] = choice.get("finish_reason")
                    row["reasoning_chars"] = len(
                        (choice.get("message") or {}).get("reasoning_content") or ""
                    )
            return response

        async def aclose(self):
            await self.inner.aclose()

    class Observer:
        def __init__(self, model):
            self.model, self.calls, self.active, self.catalog = model, [], None, None
            model.client.headers["Accept-Encoding"] = "identity"
            t = model.client._transport
            t.inner = WireObserver(t.inner, self)

        async def execute(self, request, on_delta=None):
            row = {
                "call_id": request.call_id,
                "purpose": request.input.purpose,
                "goal": request.input.perceived.goal,
                "visibility": visibility(request, self.catalog),
                "assembly": request.input.perceived.assembly.model_dump(mode="json"),
            }
            self.calls.append(row)
            self.active = row
            start = perf_counter()

            async def forward(text):
                if text and "first_delta_seconds" not in row:
                    row["first_delta_seconds"] = round(perf_counter() - start, 4)
                await on_delta(text)

            try:
                decision = (
                    await self.model.decide_stream(request, forward)
                    if on_delta
                    else await self.model.decide(request)
                )
                row["decision"] = decision.model_dump(mode="json")
                full_ids = {
                    m["material_id"]
                    for m in row["visibility"]
                    if m["full_text_visible"]
                }
                row["potential_duplicate_reads"] = [
                    a.action_id
                    for a in decision.action_requests
                    if a.tool_name == "read_material"
                    and a.arguments.get("material_id") in full_ids
                    and not a.arguments.get("include_image")
                ]
                return decision
            except BaseException as exc:
                row["error"] = getattr(
                    getattr(exc, "error", None), "code", type(exc).__name__
                )
                raise
            finally:
                row["seconds"] = round(perf_counter() - start, 4)
                self.active = None

        async def decide_stream(self, request, on_delta):
            return await self.execute(request, on_delta)

        async def decide(self, request):
            return await self.execute(request)

    started = perf_counter()
    async with open_harness(settings=settings, events=sink) as resident:
        report["startup_seconds"] = round(perf_counter() - started, 3)
        rag_bridge = resident.modules["action"].tools.get(settings.rag_tool.name)
        if not rag_bridge:
            raise RuntimeError("real RAG registration missing")
        for repeat in range(args.repeat):
            for case in jobs:
                chosen = settings.model_copy(deep=True)
                if case.get("pressure"):
                    chosen.deployment_limits[chosen.model.name] = DeploymentLimits(
                        total_tokens=pressure["deployment_tokens"]
                    )
                async with open_model(
                    settings=chosen, events=sink, role="main"
                ) as model:
                    observed = Observer(model)
                    agent = build_harness(
                        model=observed,
                        model_context=model,
                        settings=chosen,
                        events=sink,
                        streams=sink,
                        rag=rag_bridge.module,
                    )
                    observed.catalog = agent.material_catalog
                    session = "context-eval:" + uuid4().hex
                    history = []
                    if case.get("pressure"):
                        history = [
                            MemoryRecord(
                                record_id=f"synthetic-background-{i}",
                                session_id=session,
                                run_id="fixture-old",
                                source="user",
                                kind="background",
                                retention="session",
                                body={"text": "背景资料" * pressure["history_repeats"]},
                            )
                            for i in range(2)
                        ]
                    for turn_index, turn in enumerate(case["turns"]):
                        files = (
                            [
                                MaterialInput(
                                    uri=str(professional_file),
                                    material_id="question-source",
                                )
                            ]
                            if case.get("professional")
                            else [
                                MaterialInput(
                                    uri=str(paths[k]), material_id="fixture-" + k
                                )
                                for k in case.get("materials", [])
                            ]
                        )
                        text = turn["text"]
                        if case.get("inline_fixture"):
                            text += "\n\n以下为用户原始材料：\n" + fixture_text(
                                corpus["fixtures"][case["inline_fixture"]]
                            )
                        print(
                            json.dumps(
                                {
                                    "starting": case["id"],
                                    "turn": turn_index + 1,
                                    "mode": args.mode,
                                    "baseline": baseline,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        first = len(observed.calls)
                        event_start = len(sink.events)
                        stream_start = len(sink.stream_events)
                        began = perf_counter()
                        run = await agent.run(
                            RunInput(text=text, materials=files),
                            session_id=session,
                            history=history,
                        )
                        own_streams = sink.stream_events[stream_start:]
                        calls = observed.calls[first:]
                        row = {
                            "case": case["id"],
                            "repeat": repeat + 1,
                            "turn": turn_index + 1,
                            "input": text,
                            "run_id": run.run_id,
                            "status": run.status,
                            "seconds": round(perf_counter() - began, 3),
                            "answer": run.answer,
                            "error": run.error.code if run.error else None,
                            "missing_inputs": run.missing_inputs,
                            "calls": calls,
                            "tools": run.execution_state.get("tool_results", []),
                            "events": [
                                e.model_dump(mode="json", exclude_none=True)
                                for e in sink.events[event_start:]
                            ],
                            "stream_matches_answer": visible_text(own_streams)
                            == (run.answer or ""),
                            "public_error_events": sum(
                                e.type == "error" for e in own_streams
                            ),
                            "delta_count": sum(e.type == "delta" for e in own_streams),
                            "compacted_record_ids": run.execution_state.get(
                                "compacted_record_ids", []
                            ),
                            "delivered_stage_count": len(
                                run.execution_state.get("delivered_stages", [])
                            ),
                        }
                        if not case.get("professional"):
                            projected = {
                                **row,
                                "calls": [
                                    {
                                        **c,
                                        "within_budget": c.get(
                                            "wire_tokens_estimate", 0
                                        )
                                        <= c.get("budget_warning", 0),
                                    }
                                    for c in calls
                                ],
                            }
                            row["checks"] = check_result(turn["expected"], projected)
                        report["rows"].append(row)
                        save()
                        print(
                            json.dumps(
                                {
                                    "finished": case["id"],
                                    "status": run.status,
                                    "seconds": row["seconds"],
                                    "calls": len(calls),
                                    "tools": [t["tool_name"] for t in row["tools"]],
                                    "error": row["error"],
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        if row["error"] in {
                            "model_quota_exhausted",
                            "credential_missing",
                            "model_rejected",
                        }:
                            raise RuntimeError(
                                "real provider blocked; evidence saved, no fabricated replacement"
                            )
    save()


if __name__ == "__main__":
    asyncio.run(main(parser.parse_args()))
