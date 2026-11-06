"""Explicit live account/model/container/file acceptance, synthetic evidence only."""

import argparse
import asyncio
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx

MATERIAL = """验收演示材料，不是真实客户案件。
2026年10月1日，甲将20箱设备交由乙运输。封签标识 ZX-EXEC-4816。
2026年10月8日，卸货记录写3箱外包装受潮；货物内部是否受损未知。
甲方邮件称2026年10月7日完成检验，但未附检验报告。
乙方邮件称2026年10月9日首次收到检验请求；未提供发出记录或回执。
检验完成、请求发出和请求收到是不同事件，发送主体及日期未知。
提单号、适用法、赔偿金额未提供。
"""


async def main(output):
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8025", timeout=40, trust_env=False
    ) as client:

        async def register():
            r = await client.post(
                "/auth/register",
                json={
                    "email": f"execution-{uuid4().hex}@example.test",
                    "password": uuid4().hex + "!",
                },
            )
            r.raise_for_status()
            return r.json()["session"]["token"]

        token = await register()
        client.headers["Authorization"] = "Bearer " + token
        r = await client.post("/workspace/conversations", json={})
        r.raise_for_status()
        cid = r.json()["conversation"]["conversation_id"]
        r = await client.post(
            f"/workspace/conversations/{cid}/materials",
            files={"file": ("演示证据.txt", MATERIAL.encode(), "text/plain")},
        )
        r.raise_for_status()
        mid = r.json()["material"]["material_id"]
        r = await client.post(
            f"/workspace/conversations/{cid}/runs",
            json={
                "conversation_id": cid,
                "client_request_id": uuid4().hex,
                "text": "请根据附件制作可下载的Excel证据时间线和Word补证清单草稿。读取真实输入，用Python实际生成，重新打开检查日期、封签和表格内容，再交付两个文件。保持已知日期并排序，未知发送日期留未知；不推断受潮原因、责任、赔偿金额或两种不同事件的矛盾。不要仅给代码或Markdown，不需要追问。",
                "facts": {"skill_id": "evidence-review", "task_mode":"work"},
                "material_ids": [mid],
                "attached_material_ids": [mid],
                "constraints": [],
            },
        )
        r.raise_for_status()
        run = r.json()["run"]
        rid = run["run_id"]
        start = time.monotonic()
        while run["status"] in {"queued", "running"} and time.monotonic() - start < 240:
            await asyncio.sleep(1)
            r = await client.get(
                f"/workspace/runs/{rid}", params={"conversation_id": cid}
            )
            r.raise_for_status()
            run = r.json()["run"]
        r = await client.get(
            f"/maritime/runs/{rid}/deliverables", params={"conversation_id": cid}
        )
        r.raise_for_status()
        files = r.json().get("files", [])
        results = []
        for file in files:
            url = f"/maritime/runs/{rid}/files/{file['id']}"
            r = await client.get(url, params={"conversation_id": cid})
            r.raise_for_status()
            raw = r.content
            assert hashlib.sha256(raw).hexdigest() == file["sha256"]
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                text = "\n".join(
                    z.read(n).decode("utf-8")
                    for n in z.namelist()
                    if n.endswith(".xml")
                )
            assert "ZX-EXEC-4816" in text
            target = output.parent / file["name"]
            target.write_bytes(raw)
            other = await register()
            denied = await client.get(
                url,
                params={"conversation_id": cid},
                headers={"Authorization": "Bearer " + other},
            )
            anonymous = await client.get(
                url, params={"conversation_id": cid}, headers={"Authorization": ""}
            )
            assert denied.status_code == 404 and anonymous.status_code == 401
            results.append(
                {
                    **file,
                    "download_status": r.status_code,
                    "other_account_status": denied.status_code,
                    "anonymous_status": anonymous.status_code,
                    "xml_contains_seal": True,
                }
            )
        passed = (
            run["status"] == "completed"
            and any(f["name"].endswith(".xlsx") for f in files)
            and any(f["name"].endswith(".docx") for f in files)
        )
        report = {
            "notice": "Synthetic live execution/provenance acceptance, not professional legal approval or visual document acceptance.",
            "run_id": rid,
            "conversation_id": cid,
            "status": run["status"],
            "answer": run.get("output"),
            "error": run.get("error"),
            "elapsed": round(time.monotonic() - start, 2),
            "files": results,
            "passed": passed,
        }
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
        if not passed:
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("docs/acceptance/execution-v1/live.json")
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args.output))
