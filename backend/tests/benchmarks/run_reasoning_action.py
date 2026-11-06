"""Paid single-agent office-work evaluation; fixtures and faults are explicit.

Run from the repository root with --live. No existing account data is touched.
The JSON contains fixture requests and public decisions, never credentials or
provider reasoning. It is intended for local, subjective trace review.
"""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from backend.bootstrap import build_harness, open_harness
from backend.config.settings import load_settings
from backend.contracts.events.streaming import visible_text
from backend.contracts.modules.loop import RunInput, ToolSpec
from backend.contracts.modules.materials import MaterialInput
from backend.contracts.schemas.core import PortError
from backend.infrastructure.observability.memory import MemoryEventSink


class ObservedModel:
    def __init__(self, model):
        self.model, self.calls = model, []

    async def decide_stream(self, request, on_delta):
        entry = {
            "run_id": request.run_id,
            "purpose": request.input.purpose,
            "estimated_input_tokens": request.input.perceived.assembly.used_tokens,
            "messages": self.model.trace_messages(request),
            "available_tools": [t.name for t in request.input.tools],
        }
        self.calls.append(entry)
        began = perf_counter()

        async def forward(text):
            if text and "first_delta_seconds" not in entry:
                entry["first_delta_seconds"] = round(perf_counter() - began, 3)
            await on_delta(text)

        try:
            decision = await self.model.decide_stream(request, forward)
            entry["decision"] = decision.model_dump(mode="json")
            return decision
        except PortError as exc:
            entry["error"] = exc.error.code
            raise
        except asyncio.CancelledError:
            entry["error"] = "model_timeout_or_cancelled"
            raise
        finally:
            entry["seconds"] = round(perf_counter() - began, 3)


class UnavailableFixture:
    """Fault injection, not a real business integration."""

    def __init__(self, settings):
        self.settings = settings
        self.spec = ToolSpec(
            name="read_test_record",
            description="读取本次测试资料中的业务记录，record_id 是记录编号。",
            parameters={
                "type": "object",
                "properties": {"record_id": {"type": "string"}},
                "required": ["record_id"],
            },
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute(self, request):
        raise PortError(self.settings.error("tool_failed"))


async def evaluate(args, work):
    settings, sink = (
        load_settings(Path(args.config) if args.config else None),
        MemoryEventSink(),
    )
    if settings.model.provider != "deepseek":
        raise RuntimeError(
            "This evaluation requires the configured DeepSeek deployment."
        )
    settings.materials.cache_root = str(work / "parsed")
    long = work / "long-record.txt"
    long.write_text(
        "航次背景记录：材料待核验。\n" * 1500 + "\n末尾交接暗号：青帆九二七。",
        encoding="utf-8",
    )
    first, second = work / "agreement-a.txt", work / "agreement-b.txt"
    first.write_text(
        "甲版本：解除合同应提前30天通知。双方应书面交接。", encoding="utf-8"
    )
    second.write_text(
        "乙版本：解除合同应提前60天通知。双方应书面交接。", encoding="utf-8"
    )
    doc = Path(args.questions)
    if not doc.is_file():
        raise FileNotFoundError("Question document is missing.")
    cases = [
        ("rewrite", "把这句话改得礼貌、简洁，只交付正文：货还没到，请赶紧查一下。", []),
        ("translation", "翻译成英文，只给译文：请提供提单副本，货物预计周五到港。", []),
        (
            "extract",
            "整理为两列表格，字段是船名、件数、预计到港日：船名海风，80件，预计11月8日到港。不要补年份。",
            [],
        ),
        (
            "draft",
            "写一封简短邮件，请对方提供检验报告。收件人和日期未定，用占位符即可，只交付邮件。",
            [],
        ),
        (
            "private_fact",
            "我那笔货损赔款到底到账了吗？我还没有提供付款记录或银行流水。",
            [],
        ),
        (
            "first_ten",
            "按文档出现顺序做最前面的十道题，每题一个答案和一句简短理由。",
            [doc],
        ),
        ("long_tail", "这份记录末尾的交接暗号是什么？只给暗号。", [long]),
        (
            "compare",
            "比较两个版本解除合同的通知要求，只列差异，不核查法律。",
            [first, second],
        ),
        (
            "compare_draft",
            "先比较两份约定的通知期限，再起草一封请对方确认采用哪个版本的短邮件。不要替双方选定版本。",
            [first, second],
        ),
        ("law", "海商法第50条规定了承运人的什么航线义务？", []),
        (
            "fault",
            "请读取记录 ZX-17，告诉我其中登记的交接时间；读不到就说明，不要猜。",
            [],
        ),
        ("revision", "把“货物延误，请告知原因”改为礼貌邮件，先给两句话。", []),
    ]
    if args.cases:
        selected = set(args.cases.split(","))
        if selected - {name for name, _, _ in cases}:
            raise ValueError("Unknown evaluation case")
        cases = [case for case in cases if case[0] in selected]
    results = []
    began = perf_counter()
    async with open_harness(settings=settings, events=sink) as agent:
        warmup = perf_counter() - began
        observed = ObservedModel(agent.modules["reasoning"].model)
        agent.modules["reasoning"].model = observed
        failure = UnavailableFixture(settings)
        fault_agent = build_harness(
            model=observed,
            model_context=agent.model_context,
            settings=settings,
            events=sink,
            streams=sink,
            tools={failure.spec.name: (failure.spec, failure)},
        )
        for repeat in range(args.repeats):
            for name, text, files in cases:
                session = f"evaluation:{uuid4().hex}" if name == "revision" else None
                target = fault_agent if name == "fault" else agent
                turns = [text] + (
                    ["保留事实，改成一句，不需要称呼落款，只交付正文。"]
                    if session
                    else []
                )
                for turn_index, user_text in enumerate(turns):
                    call_start, event_start, stream_start = (
                        len(observed.calls),
                        len(sink.events),
                        len(sink.stream_events),
                    )
                    started = perf_counter()
                    run = await target.run(
                        RunInput(
                            text=user_text,
                            materials=[MaterialInput(uri=str(p)) for p in files],
                        ),
                        session_id=session,
                    )
                    events, streams = (
                        sink.events[event_start:],
                        sink.stream_events[stream_start:],
                    )
                    calls = observed.calls[call_start:]
                    own_streams = [s for s in streams if s.run_id == run.run_id]
                    entry = {
                        "case": name,
                        "repeat": repeat + 1,
                        "turn": turn_index + 1,
                        "input": user_text,
                        "run_id": run.run_id,
                        "seconds": round(perf_counter() - started, 3),
                        "status": run.status,
                        "answer": run.answer,
                        "missing_inputs": run.missing_inputs,
                        "error": run.error.code if run.error else None,
                        "calls": calls,
                        "tools": [
                            {
                                "tool": r["tool_name"],
                                "outcome": r.get("outcome", "ok"),
                                "error": r.get("error"),
                                "result": r["result"],
                            }
                            for r in run.execution_state.get("tool_results", [])
                        ],
                        "tokens": sum(
                            e.total_tokens or 0 for e in events if e.module == "model"
                        ),
                        "usage_complete": all(
                            e.total_tokens is not None
                            for e in events
                            if e.module == "model"
                        ),
                        "stream_matches_answer": visible_text(own_streams)
                        == (run.answer or ""),
                        "stream_delta_count": sum(
                            s.type == "delta" for s in own_streams
                        ),
                        "events": [
                            e.model_dump(mode="json", exclude_none=True) for e in events
                        ],
                    }
                    if not entry["usage_complete"]:
                        entry["tokens"] = None
                    results.append(entry)
                    provider_unavailable = bool(calls) and all(
                        c.get("error")
                        in {
                            "model_unavailable",
                            "model_quota_exhausted",
                            "model_timeout_or_cancelled",
                        }
                        for c in calls
                    )
                    Path(args.output).write_text(
                        json.dumps(
                            {
                                "thinking_mode": settings.model.thinking_mode,
                                "validation_status": "provider_unavailable"
                                if provider_unavailable
                                else "responses_collected",
                                "subjective_review_required": True,
                                "warmup_seconds": round(warmup, 3),
                                "results": results,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    print(
                        json.dumps(
                            {
                                k: entry[k]
                                for k in (
                                    "case",
                                    "repeat",
                                    "turn",
                                    "seconds",
                                    "status",
                                    "error",
                                )
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if provider_unavailable:
                        print(
                            "Provider unavailable: stopped evaluation; no answer-quality claim is possible.",
                            flush=True,
                        )
                        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--questions", default="D:/旧数据/数据/数据/海商法智能体/评估/评估/单选题.docx"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--cases", help="Optional comma-separated case names")
    parser.add_argument(
        "--config",
        help="Optional existing settings file for controlled prompt comparisons",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for paid model calls")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(".ai_tmp")
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="reasoning-action-eval-", dir=temp_root
    ) as directory:
        available = asyncio.run(evaluate(args, Path(directory)))
    if not available:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
