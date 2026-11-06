from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import tomllib
from pathlib import Path

import httpx
from pydantic import SecretStr

from backend.config.settings import load_settings
from backend.contracts.modules.loop import PerceptionInput, ReasoningInput, RunInput
from backend.contracts.modules.memory import MemoryInput, MemoryRecord, TaskState
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.modules.memory.service import InMemoryMemory
from backend.modules.perception.service import TextPerception

ROOT = Path(__file__).resolve().parents[3]
THRESHOLDS = Path(__file__).with_name("memory_thresholds.toml")


async def run() -> dict:
    limits = tomllib.loads(THRESHOLDS.read_text(encoding="utf-8"))["memory"]
    memory = InMemoryMemory(max_chars=24_000, max_items=200)
    records = [
        MemoryRecord(
            record_id=f"record-{index}",
            session_id="session-0",
            run_id="run-batch" if index < 1000 else f"run-{index % 100}",
            source="user",
            summary=f"record {index}",
        )
        for index in range(10_000)
    ]
    records[0].kind = "correction"
    records[0].summary = "EARLY_CORRECTION"
    records[1].source = "reasoning"
    records[1].body = {
        "proposal": {
            "kind": "action",
            "action_requests": [
                {"action_id": "lookup-1", "tool_name": "lookup", "arguments": {}}
            ],
        }
    }
    records[2].source = "action"
    records[2].body = {
        "action": {
            "action_id": "lookup-1",
            "tool_name": "lookup",
            "result": {"receipt": "TOOL_EVIDENCE"},
        }
    }
    records[-1].status = "unavailable"
    records[-1].body = {"text": "UNAVAILABLE_SECRET"}
    started = time.perf_counter()
    batch = await memory.handle(
        ModuleRequest(
            run_id="run-batch",
            call_id="batch",
            input=MemoryInput(
                operation="record",
                session_id="session-0",
                items=records[:1000],
            ),
        )
    )
    batch_ms = (time.perf_counter() - started) * 1000
    assert batch.status == "ok" and len(batch.output.write.accepted_record_ids) == 1000

    # Populate the remaining corpus through the repository port so the recall
    # benchmark measures recall rather than repeatedly rebuilding the view.
    await memory.repository.append(records[1000:])
    await memory.repository.save_task(
        TaskState(
            session_id="session-0",
            task_id="task",
            goal="TASK_GOAL",
            blockers=["TASK_BLOCKER"],
        ),
        expected_revision=0,
    )
    isolated = InMemoryMemory(max_chars=20_000, max_items=20)
    for index in range(100):
        await isolated.handle(
            ModuleRequest(
                run_id=f"isolated-run-{index}",
                call_id=f"isolated-write-{index}",
                input=MemoryInput(
                    operation="record",
                    session_id=f"isolated-{index}",
                    items=[
                        MemoryRecord(
                            session_id=f"isolated-{index}",
                            run_id=f"isolated-run-{index}",
                            source="user",
                            summary=f"isolated-{index}",
                        )
                    ],
                ),
            )
        )
    concurrent_started = time.perf_counter()
    concurrent = await asyncio.gather(
        *[
            isolated.handle(
                ModuleRequest(
                    run_id=f"isolated-query-{index}",
                    call_id=f"isolated-query-{index}",
                    input=MemoryInput(
                        operation="recall", session_id=f"isolated-{index}"
                    ),
                )
            )
            for index in range(100)
        ]
    )
    concurrent_ms = (time.perf_counter() - concurrent_started) * 1000
    isolation_pass = all(
        output.output.items[0].summary == f"isolated-{index}"
        for index, output in enumerate(concurrent)
    )
    timings: list[float] = []
    quality_pass = True
    for index in range(100):
        started = time.perf_counter()
        recalled = await memory.handle(
            ModuleRequest(
                run_id="run-query",
                call_id=f"query-{index}",
                input=MemoryInput(
                    operation="recall",
                    session_id="session-0",
                    task_id="task",
                    reference_ids=["record-2"],
                ),
            )
        )
        timings.append((time.perf_counter() - started) * 1000)
        quality_pass &= (
            recalled.status == "ok"
            and {"record-0", "record-1", "record-2", "record-9999"}
            <= set(recalled.output.selected_item_ids)
            and recalled.output.task_state.goal == "TASK_GOAL"
            and recalled.output.task_state.blockers == ["TASK_BLOCKER"]
        )
    timings.sort()
    p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
    result = {
        "records": 10_000,
        "batch_records": 1_000,
        "batch_write_ms": round(batch_ms, 3),
        "recall_p50_ms": round(statistics.median(timings), 3),
        "recall_p95_ms": round(p95, 3),
        "recall_p99_ms": round(
            timings[min(len(timings) - 1, int(len(timings) * 0.99))], 3
        ),
        "concurrent_100_recall_ms": round(concurrent_ms, 3),
        "concurrent_isolation_pass": isolation_pass,
        "quality_pass": quality_pass,
        "batch_pass": batch_ms <= limits["max_batch_write_ms"],
        "recall_pass": p95 <= limits["max_recall_p95_ms"],
        "concurrent_pass": concurrent_ms <= limits["max_concurrent_recall_ms"]
        and isolation_pass,
    }
    result["pass"] = bool(
        result["batch_pass"]
        and result["recall_pass"]
        and result["concurrent_pass"]
        and quality_pass
    )
    # Inspect the actual adapter HTTP body once, outside the recall measurement.
    cfg = load_settings()
    began = time.perf_counter()
    perceived = await TextPerception(cfg).handle(
        ModuleRequest(
            run_id="run-query",
            call_id="perceive",
            input=PerceptionInput(
                current=RunInput(text="CONTINUE"), memory=recalled.output
            ),
        )
    )
    result["perception_ms"] = round((time.perf_counter() - began) * 1000, 3)
    assert perceived.status == "ok"
    packets = []

    def respond(req):
        packets.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "mock"}}]
            },
        )

    async with DeepSeekModel(
        cfg,
        SecretStr("synthetic-only"),
        MemoryEventSink(),
        transport=httpx.MockTransport(respond),
    ) as model:
        await model.decide(
            ModuleRequest(
                run_id="run-query",
                call_id="model",
                input=ReasoningInput(perceived=perceived.output),
            )
        )
    body = json.dumps(packets[0], ensure_ascii=False)
    messages = packets[0]["messages"]
    checks = {
        marker: marker in body
        for marker in ["EARLY_CORRECTION", "TASK_GOAL", "TASK_BLOCKER", "TOOL_EVIDENCE"]
    }
    checks["unavailable_body_absent"] = "UNAVAILABLE_SECRET" not in body
    checks["single_trusted_system"] = sum(m["role"] == "system" for m in messages) == 1
    result["provider_quality_checks"] = checks
    result["pass"] &= all(checks.values())
    result["trace"] = {
        "stored_count": memory.repository.count_records(session_id="session-0"),
        "memory": {
            **recalled.output.trace,
            "required_ids": recalled.output.required_item_ids,
            "selected_ids": recalled.output.selected_item_ids,
            "used_chars": recalled.output.used_chars,
            "budget_chars": recalled.output.budget_chars,
            "omitted_reasons": recalled.output.omitted_reasons,
        },
        "perception": perceived.output.assembly.model_dump(),
        "http": {
            "roles": [m["role"] for m in messages],
            "request_chars": len(body),
            "tool_call_ids": [
                c["id"] for m in messages for c in m.get("tool_calls", [])
            ],
            "tool_result_ids": [
                m["tool_call_id"] for m in messages if m["role"] == "tool"
            ],
            "network": "httpx.MockTransport; no real model call",
        },
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = asyncio.run(run())
    if args.output:
        args.output.write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {k: v for k, v in output.items() if k != "trace"},
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if output["pass"] else 1)
