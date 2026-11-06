"""Explicit live smoke cases. Uses synthetic user inputs and the real model/API.

This tests execution and provenance, not professional legal correctness.
Run from workspace root with --live; output contains no session token/password.
"""

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from uuid import uuid4

import httpx

CASES = [
    (
        "law-research",
        "请检索中国海商法中承运人货物损失责任相关规则，返回两条真实来源链接，区分库内版本与当前效力。只做资料研究，不判断具体案件。",
        False,
    ),
    (
        "case-research",
        "请从案例库检索无单放货争议，选一个真实案例，列案名、已提供事实、裁判理由与片段局限，附工具来源链接。",
        False,
    ),
    (
        "contract-review",
        "请审阅附件合同第七条，引用原文，指出托运人风险并给修改建议。未提供适用法，不要假装已确认；无需完整法律意见。",
        True,
    ),
    (
        "case-assessment",
        "这是演示案：甲是货主，乙是签发提单的承运人，卸港发现包装受潮，目前无检验报告。请梳理合同关系、争点、待核事实，不给胜诉率，先交付初步框架，不需要追问。",
        False,
    ),
    (
        "evidence-review",
        "请只根据附件梳理事件时间线和证据缺口，必须保留实际日期和备注中的封签标识，不用上传日期替代事件日。",
        True,
    ),
    (
        "claim-calculation",
        "仅测算：货损数量3件、单价0.335美元；检验费2次、每次100元人民币。用计算工具分别汇总币种，列明过程。不判断应赔责任，不需其他事实。",
        False,
    ),
    (
        "document-drafting",
        "请起草短索赔通知草稿：甲货主向乙承运人通知受潮货损，提单号和金额均未知，日期未确认。保留占位符，不编造凭证、不声称已发送，无需追问。",
        False,
    ),
    (
        "case-management",
        "演示案当前只有受潮货损通知，送达日期和适用法未知。请整理三项待办、建议责任角色与复核点，不猜诉讼时效，不声称已创建系统任务或已设置提醒。",
        False,
    ),
]
MATERIAL = """演示运输材料（仅用于系统验收）
第七条：承运人对任何原因造成的货物损失均不承担责任，托运人不得提出索赔。
2026年10月1日：甲将20箱设备交由乙运输。
2026年10月8日：卸货时见3箱包装受潮。封签标识 ZX-QA-7139。
2026年10月9日：甲发送检验请求邮件，尚无检验报告或回执。
以上为演示记录，不是真实案件；适用法、提单号和金额尚未确认。
"""


async def run(base, output, require_work=False, selected=None):
    results = []
    cases = [case for case in CASES if not selected or case[0] in selected]
    async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as client:
        auth = await client.post(
            "/auth/register",
            json={
                "email": f"live-{uuid4().hex}@example.test",
                "password": uuid4().hex + "!",
            },
        )
        auth.raise_for_status()
        client.headers["Authorization"] = "Bearer " + auth.json()["session"]["token"]
        readiness = await client.get("/maritime/status")
        readiness.raise_for_status()
        semaphore = asyncio.Semaphore(2)

        async def scenario(skill, question, upload):
            async with semaphore:
                if require_work:
                    question += "\n请生成并保存本项对应的交付工作包，最终简述交付结果。"
                started = time.monotonic()
                conversation = await client.post("/workspace/conversations", json={})
                conversation.raise_for_status()
                cid = conversation.json()["conversation"]["conversation_id"]
                material_ids = []
                if upload:
                    material = await client.post(
                        f"/workspace/conversations/{cid}/materials",
                        files={
                            "file": ("acceptance.txt", MATERIAL.encode(), "text/plain")
                        },
                    )
                    material.raise_for_status()
                    material_ids = [material.json()["material"]["material_id"]]
                submitted = await client.post(
                    f"/workspace/conversations/{cid}/runs",
                    json={
                        "conversation_id": cid,
                        "client_request_id": uuid4().hex,
                        "text": question,
                        "facts": {"skill_id": skill},
                        "material_ids": material_ids,
                        "attached_material_ids": material_ids,
                        "constraints": [],
                    },
                )
                submitted.raise_for_status()
                current = submitted.json()["run"]
                rid = current["run_id"]
                while (
                    current["status"] in {"running", "queued"}
                    and time.monotonic() - started < 240
                ):
                    await asyncio.sleep(0.8)
                    snapshot = await client.get(
                        f"/workspace/runs/{rid}", params={"conversation_id": cid}
                    )
                    snapshot.raise_for_status()
                    current = snapshot.json()["run"]
                answer = current.get("output") or current.get("partial_output") or ""
                packages = []
                if require_work:
                    delivered = await client.get(
                        f"/maritime/runs/{rid}/deliverables",
                        params={"conversation_id": cid},
                    )
                    delivered.raise_for_status()
                    packages = delivered.json()["packages"]
                delivered_text = (
                    answer + "\n" + json.dumps(packages, ensure_ascii=False)
                )
                links = set(
                    re.findall(
                        r'/api/maritime/sources/[^\s<>\)\]\x60"，。；]+', delivered_text
                    )
                )
                citations = []
                for link in links:
                    response = await client.get(link.removeprefix("/api"))
                    citations.append({"url": link, "status": response.status_code})
                anchors = {
                    "law-research": len(links) >= 2,
                    "case-research": len(links) >= 1,
                    "contract-review": "第七条" in delivered_text
                    and "任何原因" in delivered_text,
                    "evidence-review": "ZX-QA-7139" in delivered_text
                    and "2026" in delivered_text,
                    "claim-calculation": "1.01" in delivered_text
                    and "200" in delivered_text,
                    "document-drafting": "草稿" in delivered_text
                    and "待" in delivered_text,
                    "case-management": "未" in delivered_text
                    and "任务" in delivered_text,
                    "case-assessment": "检验报告" in delivered_text,
                }
                result = {
                    "skill": skill,
                    "run_id": rid,
                    "conversation_id": cid,
                    "status": current["status"],
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "error": current.get("error"),
                    "answer": answer,
                    "resolved_skill_version": current["payload"]["facts"]
                    .get("skill_context", {})
                    .get("version"),
                    "source_link_count": answer.count("/api/maritime/sources/"),
                    "citation_http_checks": citations,
                    "scenario_anchor_passed": anchors[skill],
                    "work_packages": packages,
                    "smoke_passed": current["status"] == "completed"
                    and bool(answer.strip())
                    and anchors[skill]
                    and (
                        not require_work
                        or any(p["skill_id"] == skill for p in packages)
                    )
                    and all(c["status"] == 200 for c in citations),
                }
                results.append(result)
                print(
                    json.dumps(
                        {
                            k: result[k]
                            for k in (
                                "skill",
                                "status",
                                "elapsed_seconds",
                                "source_link_count",
                                "smoke_passed",
                            )
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

        outcomes = await asyncio.gather(
            *(scenario(*case) for case in cases), return_exceptions=True
        )
        for case, value in zip(cases, outcomes):
            if isinstance(value, Exception):
                results.append(
                    {
                        "skill": case[0],
                        "smoke_passed": False,
                        "error_type": type(value).__name__,
                    }
                )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "notice": "Execution smoke, not legal acceptance. Synthetic inputs only.",
                    "environment": base,
                    "readiness": readiness.json(),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if not results or not all(r["smoke_passed"] for r in results) or len(results) != len(cases):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--work-packages", action="store_true")
    parser.add_argument("--skills", nargs="+", choices=[case[0] for case in CASES])
    parser.add_argument("--base", default="http://127.0.0.1:8025")
    parser.add_argument(
        "--output", type=Path, default=Path("docs/acceptance/live-smoke.json")
    )
    args = parser.parse_args()
    asyncio.run(run(args.base, args.output, args.work_packages, args.skills))
