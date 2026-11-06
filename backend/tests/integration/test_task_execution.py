"""Explicit real Docker execution tests; no fake sandbox success."""

import asyncio
import io
import os
from uuid import uuid4

import pytest

from backend.infrastructure.execution.runtime import DockerRuntime, ExecutionStore

pytestmark = pytest.mark.skipif(
    os.getenv("ZHOUXU_EXECUTION_TEST") != "1", reason="Explicit Docker execution opt-in"
)


@pytest.mark.asyncio
async def test_real_write_edit_execute_present_and_isolation(tmp_path):
    runtime = DockerRuntime(store=ExecutionStore(tmp_path))
    run, other = uuid4().hex, uuid4().hex
    try:
        first = await runtime.operate(
            run, "write", {"path": "calc.py", "content": "print(3*2)"}
        )
        assert first["ok"]
        conflict = await runtime.operate(
            run,
            "edit",
            {
                "path": "calc.py",
                "old_text": "3*2",
                "new_text": "4*2",
                "expected_sha256": "wrong",
            },
        )
        assert not conflict["ok"]
        edited = await runtime.operate(
            run,
            "edit",
            {
                "path": "calc.py",
                "old_text": "3*2",
                "new_text": "4*2",
                "expected_sha256": first["sha256"],
            },
        )
        assert edited["ok"]
        result = await runtime.operate(run, "bash", {"command": "python calc.py"})
        assert result["stdout"].strip() == "8"
        assert not (await runtime.operate(other, "read", {"path": "calc.py"}))["ok"]
        assert not (await runtime.operate(run, "read", {"path": "../etc/passwd"}))["ok"]
        assert not (
            await runtime.operate(
                run, "write", {"path": "/tmp/outside", "content": "x"}
            )
        )["ok"]
        result = await runtime.operate(
            run, "bash", {"command": "ln -s /etc/passwd bad.txt"}
        )
        assert result["ok"]
        assert not (await runtime.operate(run, "present", {"path": "bad.txt"}))["ok"]
        result = await runtime.operate(
            run,
            "bash",
            {
                "command": """python - <<'PY'
from openpyxl import Workbook,load_workbook
from docx import Document
w=Workbook();w.active.append(['事件','时间']);w.active.append(['装货','2026-10-01']);w.save('timeline.xlsx')
assert load_workbook('timeline.xlsx').active['B2'].value=='2026-10-01'
d=Document();d.add_heading('证据目录',0);d.add_paragraph('未知事实待核');d.save('draft.docx')
assert Document('draft.docx').paragraphs[-1].text=='未知事实待核'
print('verified')
PY"""
            },
        )
        assert result["ok"] and "verified" in result["stdout"]
        item = await runtime.operate(run, "present", {"path": "timeline.xlsx"})
        assert item["ok"]
        artifact_id = item["artifact"]["id"]
        await runtime.destroy(run)
        metadata, data = runtime.store.get(run, artifact_id)
        from openpyxl import load_workbook

        assert load_workbook(io.BytesIO(data)).active["B2"].value == "2026-10-01"
        assert runtime.store.get(other, artifact_id) is None
    finally:
        await runtime.destroy(run)
        await runtime.destroy(other)


@pytest.mark.asyncio
async def test_container_policy_and_cancellation(tmp_path):
    runtime = DockerRuntime(store=ExecutionStore(tmp_path))
    run = uuid4().hex
    try:
        async with runtime.locks.setdefault(run, asyncio.Lock()):
            name = await runtime.world(run)
        code, out, _ = await runtime.command(["inspect", name])
        import json

        config = json.loads(out)[0]
        assert config["HostConfig"]["NetworkMode"] == "none"
        assert config["HostConfig"]["ReadonlyRootfs"]
        assert config["HostConfig"]["Memory"] == 512 * 1024 * 1024
        assert config["HostConfig"]["Binds"] in (None, [])
        assert config["Config"]["User"] == "65534:65534"
        result = await runtime.operate(run, "bash", {"command": "touch /etc/escape"})
        assert not result["ok"]
        running = asyncio.create_task(
            runtime.operate(run, "bash", {"command": "sleep 60 & wait"})
        )
        await asyncio.sleep(0.5)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        code, _, _ = await runtime.command(["inspect", name])
        assert code != 0 and run not in runtime.worlds
    finally:
        await runtime.destroy(run)
