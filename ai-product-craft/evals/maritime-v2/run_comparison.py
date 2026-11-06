"""Explicit live paired comparison of business instructions; not expert legal grading."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from backend.application.maritime.catalog import COMMON, builtin_skills
from backend.application.maritime.work_packages import configured_store
from backend.bootstrap import open_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import RunInput
from backend.contracts.modules.materials import MaterialInput
from backend.infrastructure.observability.memory import MemoryEventSink

ROOT = Path(__file__).resolve().parents[3]
CASES = [
    (
        "contract",
        "contract-review",
        "我代表托运人。请仅审附件第七条，给一份可谈判的工作包：保留原文、替代条款、能接受的底线及签署前待核事项。不要扩大成全合同审查。",
        True,
    ),
    (
        "evidence",
        "evidence-review",
        "请根据附件制作时间线和待证事实工作包。重点处理检验日期的矛盾，保留封签标识；原文没给的日期或文件不要补写。",
        True,
    ),
    (
        "claim",
        "claim-calculation",
        "仅测算：货损3件、单价0.335美元；检验2次、每次100元人民币。请交可复算工作包，分币种汇总，不推断责任或汇率。",
        False,
    ),
    (
        "holdout-short",
        "law-research",
        "请只用一句话说明提单通常用来做什么。不需要研究具体法律、表格或工作包。",
        False,
    ),
    (
        "holdout-events",
        "evidence-review",
        "仅整理以下给定记录，不判断单证合法性：2026年5月1日装船；5月2日签发提单；5月4日保险人收到索赔通知。请按日期做证据整理工作包，并检查这些日期是否互相矛盾。不要推定谁记错或补写未给的发送日期。",
        False,
    ),
]


async def run(output, selected_cases=None):
    previous = json.loads(
        (ROOT / "ai-product-craft/evals/maritime-v1/builtins.json").read_text(
            encoding="utf-8"
        )
    )
    old = {s["id"]: s for s in previous}
    current = {s["id"]: s for s in builtin_skills()}
    settings = load_settings()
    settings.application["work_store_path"] = str(
        ROOT / ".ai-tmp/maritime-v2-20261105/paired-work.sqlite3"
    )
    results = []
    async with open_harness(settings=settings, events=MemoryEventSink()) as harness:
        for index, (case_id, skill_id, prompt, has_material) in enumerate(CASES):
            if selected_cases and case_id not in selected_cases:
                continue
            for arm in ["v1", "v2"] if index % 2 == 0 else ["v2", "v1"]:
                method = old[skill_id] if arm == "v1" else current[skill_id]
                # Hold shared current runtime rules/tools constant; compare the business method.
                instructions = (
                    method["instructions"].rsplit("\n", 1)[-1]
                    if arm == "v1"
                    else method["instructions"]
                )
                materials = (
                    [
                        MaterialInput(
                            uri=str(
                                ROOT / "ai-product-craft/evals/maritime-v2/fixture.txt"
                            ),
                            material_id="paired-fixture",
                            name="演示运输记录.txt",
                            media_type="text/plain",
                        )
                    ]
                    if has_material
                    else []
                )
                result = await harness.run(
                    RunInput(
                        text=prompt,
                        facts={
                            "skill_context": {
                                "id": skill_id,
                                "name": method["name"],
                                "version": method["version"],
                                "instructions": instructions,
                                "runtime_rules": COMMON,
                            }
                        },
                        materials=materials,
                    ),
                    run_id="paired-" + uuid4().hex,
                )
                packages = configured_store(settings).list(result.run_id)
                row = {
                    "case": case_id,
                    "arm": arm,
                    "status": result.status,
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                    "answer": result.answer,
                    "error": result.error.model_dump() if result.error else None,
                    "tool_counts": result.execution_state.get("tool_counts", {}),
                    "packages": packages,
                }
                results.append(row)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(
                        {
                            "notice": "Paired cases, same model/runtime/tools in each round; alternating order. Descriptive observations only, not professional legal accuracy or statistical proof.",
                            "model": settings.model.name,
                            "thinking": settings.model.thinking_mode,
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
                            k: row[k]
                            for k in (
                                "case",
                                "arm",
                                "status",
                                "elapsed_seconds",
                                "tool_counts",
                            )
                        }
                        | {"packages": len(packages)},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/acceptance/method-v2/paired-results.json"),
    )
    parser.add_argument("--cases", nargs="*")
    args = parser.parse_args()
    asyncio.run(run(args.output.resolve(), args.cases))
