"""Opt-in paid evaluation of real models, files and the production Harness."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from backend.bootstrap import build_harness, open_harness
from backend.config.settings import load_settings
from backend.contracts.model_context import DeploymentLimits
from backend.contracts.modules.loop import RunInput
from backend.contracts.modules.materials import MaterialInput
from backend.contracts.modules.memory import MemoryRecord
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.benchmarks.run_rag_harness import AuditedModule


class TimedEvents(MemoryEventSink):
    def __init__(self):
        super().__init__()
        self.began = perf_counter()
        self.first_delta = {}

    def emit_stream(self, event):
        if event.type == "delta" and event.text:
            self.first_delta.setdefault(event.run_id, perf_counter() - self.began)
        super().emit_stream(event)


async def main(args):
    settings = load_settings()
    settings.model.thinking_mode = "none"
    task = Path(args.workdir).resolve()
    task.mkdir(parents=True, exist_ok=True)
    settings.materials.cache_root = str(task / "parser-cache")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    events, results, trace = TimedEvents(), [], []
    material = MaterialInput(uri=str(Path(args.document).resolve()))
    marker = "潮汐" + uuid4().hex[:8]
    text = (
        ("这是一段用于上下文验证的普通记录，无需核查法律。\n" * 1500)[:30000]
        + "\n末尾校验词："
        + marker
    )
    source = task / "long.txt"
    source.write_text(text, encoding="utf-8")

    async def evaluate(harness, name, value, **kwargs):
        events.began = perf_counter()
        before = len(events.events)
        result = await harness.run(value, **kwargs)
        selected = events.events[before:]
        calls = [
            event
            for event in selected
            if event.module == "model" and event.phase == "model"
        ]
        assembly = [
            event.diagnostics
            for event in selected
            if event.module == "perception" and event.diagnostics
        ]
        review_tokens = sum(
            step.get("total_tokens") or 0
            for raw in result.execution_state.get("tool_results", [])
            for step in (raw.get("result", {}).get("steps") or [])
            if isinstance(step, dict)
        )
        row = {
            "case": name,
            "question": value.text,
            "run_id": result.run_id,
            "status": result.status,
            "seconds": round(result.elapsed_seconds, 3),
            "first_delta_seconds": round(events.first_delta.get(result.run_id, 0), 3),
            "model_calls": len(calls),
            "model_total_tokens": sum(e.total_tokens or 0 for e in calls),
            "review_tokens_if_reported": review_tokens,
            "tools": [
                raw["tool_name"]
                for raw in result.execution_state.get("tool_results", [])
            ],
            "answer": result.answer,
            "error": result.error.model_dump() if result.error else None,
            "assemblies": [
                {
                    "used": a["used_tokens"],
                    "warning": a["budget"]["warning"],
                    "decisions": a.get("trace", {}).get("decisions", []),
                }
                for a in assembly
            ],
        }
        if "tail" in name:
            row["marker_correct"] = marker in (result.answer or "")
        results.append(row)
        output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (task / "trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    key: row[key]
                    for key in (
                        "case",
                        "status",
                        "seconds",
                        "model_calls",
                        "tools",
                        "error",
                    )
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        return result

    began = perf_counter()
    async with open_harness(settings=settings, events=events) as harness:
        warmup = round(perf_counter() - began, 3)
        for name, module in list(harness.modules.items()):
            harness.modules[name] = AuditedModule(name, module, trace)
        await evaluate(
            harness, "ordinary", RunInput(text="你好，请用一句话介绍你能帮我做什么。")
        )
        for index in range(3):
            await evaluate(
                harness,
                f"quiz-{index + 1}",
                RunInput(text="前十题", materials=[material]),
            )
        await evaluate(
            harness,
            "file-tail",
            RunInput(
                text="材料末尾的校验词是什么？只输出校验词。",
                materials=[MaterialInput(uri=str(source))],
            ),
        )
        await evaluate(
            harness,
            "pasted-tail",
            RunInput(text=text + "\n材料末尾的校验词是什么？只输出校验词。"),
        )
        await evaluate(
            harness,
            "law-article",
            RunInput(text="海商法第50条规定了承运人的什么航线义务？"),
        )
        chat = "evaluation-" + uuid4().hex
        await evaluate(
            harness,
            "history-initial",
            RunInput(text="记住本对话目的港是宁波。只回复收到。"),
            session_id=chat,
        )
        await evaluate(
            harness,
            "history-correction",
            RunInput(text="纠正：目的港改为上海。只回复收到。"),
            session_id=chat,
        )
        await evaluate(
            harness,
            "history-followup",
            RunInput(text="目的港现在是哪里？只输出港口名称。"),
            session_id=chat,
        )
        parsed = harness.material_catalog.registry.parse(material)
        full = "\n".join(unit.text for unit in parsed.units if unit.text.strip())
        async with open_model(
            settings=settings, events=MemoryEventSink(), role="main"
        ) as raw_model:
            began = perf_counter()
            response = await raw_model.client.post(
                settings.model.endpoint,
                headers={"x-zhouxu-purpose": "main"},
                json={
                    "model": settings.model.name,
                    "messages": [
                        {"role": "system", "content": settings.prompts["system"]},
                        {
                            "role": "user",
                            "content": "材料：\n" + full + "\n用户问题：前十题",
                        },
                    ],
                    "max_tokens": raw_model.context_budget().output_reserved,
                    "temperature": 0,
                    "thinking": {"type": "disabled"},
                },
            )
            response.raise_for_status()
            data = response.json()
            results.append(
                {
                    "case": "direct-model-control",
                    "seconds": round(perf_counter() - began, 3),
                    "answer": data["choices"][0]["message"]["content"],
                    "usage": data.get("usage"),
                    "note": "Same model/system/question/full text; no agent tools or loop. Network and sampling vary.",
                }
            )
    limited = settings.model_copy(deep=True)
    limited.rag_tool.enabled = False
    limited.deployment_limits[limited.model.name] = DeploymentLimits(total_tokens=64000)
    large = task / "oversized.txt"
    large.write_text(
        ("普通背景记录，无须核验法律。\n" * 12000) + "末尾校验词：" + marker,
        encoding="utf-8",
    )
    async with open_model(settings=limited, events=events, role="main") as model:
        harness = build_harness(
            model=model, settings=limited, events=events, streams=events
        )
        for name, module in list(harness.modules.items()):
            harness.modules[name] = AuditedModule(name, module, trace)
        await evaluate(
            harness,
            "pressure-file-tail",
            RunInput(
                text="材料末尾的校验词是什么？只输出校验词。",
                materials=[MaterialInput(uri=str(large))],
            ),
        )
        history = [
            MemoryRecord(
                record_id=f"prior-{i}",
                session_id="pressure-chat",
                run_id=f"run-{i}",
                source="user",
                retention="session",
                body={"text": "目的港由宁波改为上海。" + "普通背景资料。" * 1000},
            )
            for i in range(2)
        ]
        await evaluate(
            harness,
            "pressure-history",
            RunInput(text="最新目的港是什么？只输出港口名称。"),
            session_id="pressure-chat",
            history=history,
        )
    results.append(
        {
            "case": "environment",
            "thinking_mode": settings.model.thinking_mode,
            "model": settings.model.name,
            "warmup_seconds_excluded_from_run_latency": warmup,
            "counting": "conservative UTF-8 byte estimate; actual usage separate",
        }
    )
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--document", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("--live is required: this spends model credit")
    asyncio.run(main(arguments))
