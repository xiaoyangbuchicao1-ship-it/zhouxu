"""Real-model material-to-Harness benchmark.

Run only with explicit --live because it may use paid model and retrieval
services. The output keeps answers for review but never writes credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from backend.bootstrap import open_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import RunInput
from backend.contracts.modules.materials import MaterialInput
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.benchmarks.run_materials import _FixtureHandler, make_fixtures


def _materials(case, fixtures, web_base):
    if case["format"] == "web":
        path = "/missing" if case["id"] == "MAT-19" else "/notice"
        materials = [MaterialInput(uri=web_base + path, material_id=case["id"])]
        if case["id"] == "MAT-18":
            materials.append(MaterialInput(uri=str(fixtures["attachment"]), material_id="MAT-18-attachment"))
        return materials
    if case["format"] == "mixed":
        return [
            MaterialInput(uri=str(fixtures[key]), material_id=f"{case['id']}-{key}")
            for key in ("image_text", "pollution_xlsx", "pdf")
        ]
    special = {
        "MAT-01": "image_text",
        "MAT-02": "image_text",
        "MAT-03": "text_pdf",
        "MAT-04": "pdf",
        "MAT-05": "old_pdf",
        "MAT-06": "pdf",
        "MAT-11": "pollution_xlsx",
        "MAT-16": "claims_json",
    }
    if case["id"] == "MAT-05":
        return [
            MaterialInput(uri=str(fixtures["old_pdf"]), material_id="MAT-05-old"),
            MaterialInput(uri=str(fixtures["new_pdf"]), material_id="MAT-05-new"),
        ]
    if case["id"] in special:
        key = special[case["id"]]
        return [MaterialInput(uri=str(fixtures[key]), material_id=case["id"])]
    return [MaterialInput(uri=str(fixtures[case["format"]]), material_id=case["id"])]


async def run(output: Path, limit: int | None = None) -> dict:
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    cases = json.loads(
        (Path(__file__).with_name("material_cases.json")).read_text(encoding="utf-8")
    )[:limit]
    settings = load_settings()
    events = MemoryEventSink()
    rows = []
    with tempfile.TemporaryDirectory(prefix="zhouxu-material-live-") as temp:
        fixtures = make_fixtures(Path(temp))
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            async with open_harness(settings=settings, events=events) as harness:
                active_tool_names = []
                delegate = harness.modules["action"]

                class ActionTrace:
                    async def handle(self, request):
                        active_tool_names.append(request.input.tool_name)
                        return await delegate.handle(request)

                harness.modules["action"] = ActionTrace()
                for case in cases:
                    started = perf_counter()
                    before = len(events.events)
                    active_tool_names.clear()
                    try:
                        result = await harness.run(
                            RunInput(
                                text=case["question"],
                                materials=_materials(
                                    case,
                                    fixtures,
                                    f"http://127.0.0.1:{server.server_port}",
                                ),
                            )
                        )
                        error = result.error.model_dump() if result.error else None
                        answer = result.answer
                    except Exception as exc:  # keep later cases running
                        result = None
                        error = {"type": type(exc).__name__, "message": str(exc)}
                        answer = None
                    run_events = events.events[before:]
                    rows.append(
                        {
                            **case,
                            "status": result.status if result else "exception",
                            "answer": answer,
                            "error": error,
                            "action_finished": sum(
                                1
                                for event in run_events
                                if event.module == "action" and event.phase == "finished"
                            ),
                            "tool_names": list(active_tool_names),
                            "model_finished": sum(
                                1
                                for event in run_events
                                if event.module == "reasoning" and event.phase == "finished"
                            ),
                            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                        }
                    )
        finally:
            server.shutdown()
            thread.join(timeout=2)
    summary = {
        "case_count": len(rows),
        "completed": sum(row["status"] == "completed" for row in rows),
        "needs_input": sum(row["status"] == "needs_input" for row in rows),
        "failed": sum(row["status"] in {"failed", "exception"} for row in rows),
        "rows": rows,
        "note": "Live model evaluation; action_finished is a routing signal, not a semantic answer score.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("real model calls require --live")
    summary = asyncio.run(run(args.output, args.limit))
    print(json.dumps({key: summary[key] for key in ("case_count", "completed", "needs_input", "failed")}, ensure_ascii=False))
