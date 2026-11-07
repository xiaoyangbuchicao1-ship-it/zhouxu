"""Docker-backed execution worlds. No host path mounts or unrestricted fallback."""

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import Error, PortError
from backend.infrastructure.search.maritime_corpus import connection

ROOT = Path(__file__).resolve().parents[2] / ".runtime" / "execution"
WORKER = Path(__file__).with_name("worker.py").read_text(encoding="utf-8")
EXECUTION_TIMEOUT_SECONDS = 600

# Office previews are rendered inside the reviewed execution image.  The
# worker deliberately receives the artifact through stdin and writes a
# self-contained, image-based HTML preview to stdout; no host path is ever
# mounted into the container.
PREVIEW_WORKER = r'''
import os
import subprocess
import sys
import base64
from pathlib import Path

suffix = os.environ.get("ZHOUXU_PREVIEW_SUFFIX", "")
if suffix not in {".xlsx", ".docx"}:
    raise SystemExit("preview_type_not_supported")
root = Path("/workspace")
source = root / ("source" + suffix)
output = root / "preview"
output.mkdir()
source.write_bytes(sys.stdin.buffer.read())
try:
    result = subprocess.run(
        [
            "soffice",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output),
            str(source),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=35,
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit("preview_conversion_timeout")
if result.returncode:
    raise SystemExit(result.stderr.decode("utf-8", "replace")[-400:])
pdf = output / "source.pdf"
if not pdf.is_file():
    raise SystemExit("preview_pdf_missing")
render = subprocess.run(
    [
        "pdftoppm",
        "-jpeg",
        "-r",
        "110",
        "-f",
        "1",
        "-l",
        "8",
        str(pdf),
        str(output / "page"),
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    timeout=25,
    check=False,
)
if render.returncode:
    raise SystemExit(render.stderr.decode("utf-8", "replace")[-400:])
pages = sorted(output.glob("page-*.jpg"))
if not pages:
    raise SystemExit("preview_pages_missing")
parts = [
    b'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    b'<meta name="viewport" content="width=device-width,initial-scale=1">'
    b'<style>html,body{margin:0;background:#eef1f4;color:#26313c;font-family:Arial,"Noto Sans CJK SC",sans-serif}main{padding:24px 16px 40px}figure{margin:0 auto 22px;max-width:980px}img{display:block;width:100%;height:auto;background:#fff;box-shadow:0 4px 18px #24334224}figcaption{padding:8px 0 0;text-align:center;color:#657486;font-size:12px}p{margin:0 auto 18px;max-width:980px;padding:10px 12px;border:1px solid #c9d4df;border-radius:6px;background:#f8fafc;color:#526477;font-size:12px;line-height:1.6}</style></head><body><main>'
]
total = 0
for index, path in enumerate(pages, 1):
    raw = path.read_bytes()
    total += len(raw)
    if total > 18 * 1024 * 1024:
        raise SystemExit("preview_pages_too_large")
    encoded = base64.b64encode(raw)
    label = ("第 " + str(index) + " 页").encode("utf-8")
    parts.append(b'<figure><img alt="' + label + b'" src="data:image/jpeg;base64,' + encoded + b'"><figcaption>' + label + b'</figcaption></figure>')
if len(pages) >= 8:
    parts.insert(1, '<p>预览显示前 8 页；完整内容请下载原始文件核对。</p>'.encode('utf-8'))
parts.append(b'</main></body></html>')
sys.stdout.buffer.write(b''.join(parts))
'''


def validate_artifact(name, data):
    suffix = Path(name).suffix.lower()
    if not data:
        raise ValueError("empty_delivery")
    if suffix not in {".xlsx", ".docx"}:
        return
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            members = package.infolist()
            if (
                len(members) > 1000
                or sum(i.file_size for i in members) > 100 * 1024 * 1024
            ):
                raise ValueError("office_package_too_large")
            names = {i.filename for i in members}
            if len(names) != len(members) or any(
                n.lower().endswith(".bin") or ".." in Path(n).parts or n.startswith("/")
                for n in names
            ):
                raise ValueError("office_active_or_invalid_parts")
            required = "xl/workbook.xml" if suffix == ".xlsx" else "word/document.xml"
            for part in ("[Content_Types].xml", required):
                if (
                    part not in names
                    or package.getinfo(part).file_size > 2 * 1024 * 1024
                ):
                    raise ValueError("invalid_office_package")
                root = ElementTree.fromstring(package.read(part))
                expected = (
                    "{http://schemas.openxmlformats.org/package/2006/content-types}Types"
                    if part == "[Content_Types].xml"
                    else (
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}workbook"
                        if suffix == ".xlsx"
                        else "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document"
                    )
                )
                if root.tag != expected:
                    raise ValueError("invalid_office_xml_root")
    except (zipfile.BadZipFile, KeyError, RuntimeError, ElementTree.ParseError) as exc:
        raise ValueError("invalid_office_package") from exc


class ExecutionStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def save(self, run_id, name, data):
        self.root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        # Store bounded bytes transactionally; no user-controlled host path.
        item = dict(id=uuid4().hex, name=name, size=len(data), sha256=digest)
        with connection(self.root / "files.sqlite3") as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,metadata TEXT NOT NULL,data BLOB NOT NULL)"
            )
            count, size = db.execute(
                "SELECT count(*),coalesce(sum(length(data)),0) FROM artifacts WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if count >= 10 or size + len(data) > 30 * 1024 * 1024:
                raise ValueError("delivery_quota_exceeded")
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?)",
                (item["id"], run_id, json.dumps(item), data),
            )
        return item

    def list(self, run_id):
        if not (self.root / "files.sqlite3").exists():
            return []
        with connection(self.root / "files.sqlite3") as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT metadata FROM artifacts WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                )
            ]

    def get(self, run_id, artifact_id):
        if not (self.root / "files.sqlite3").exists():
            return None
        with connection(self.root / "files.sqlite3") as db:
            row = db.execute(
                "SELECT metadata,data FROM artifacts WHERE run_id=? AND id=?",
                (run_id, artifact_id),
            ).fetchone()
            return (json.loads(row[0]), bytes(row[1])) if row else None


class DockerRuntime:
    def __init__(self, image=None, store=None):
        # The tag is configurable so a reviewed image can be promoted without
        # changing the tool contract or the harness loop.
        self.image = image or os.environ.get(
            "ZHOUXU_EXECUTION_IMAGE", "zhouxu-task-runtime:0.2"
        )
        self.pinned = None
        self.docker = shutil.which("docker")
        self.store = store or ExecutionStore()
        self.worlds = {}
        self.locks = {}
        self.admission = asyncio.Lock()
        self.staged = {}
        self.failures = {}

    async def preview(self, name, data):
        """Render one immutable Office artifact to PDF in an isolated container."""
        suffix = Path(name).suffix.lower()
        if suffix not in {".xlsx", ".docx"}:
            raise ValueError("preview_type_not_supported")
        if not data or len(data) > 30 * 1024 * 1024:
            raise ValueError("preview_input_too_large")
        if self.pinned is None:
            code, out, _ = await self.command(
                ["image", "inspect", self.image, "--format", "{{.Id}}"]
            )
            if code or not out.strip().startswith(b"sha256:"):
                raise RuntimeError("sandbox_image_unavailable")
            self.pinned = out.decode().strip()
        name_id = "zhouxu-preview-" + uuid4().hex
        args = [
            "run",
            "--rm",
            "--interactive",
            "--name",
            name_id,
            "--label",
            "zhouxu.execution.preview=1",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--ulimit",
            "fsize=33554432:33554432",
            "--tmpfs",
            "/workspace:rw,nosuid,nodev,size=128m,uid=65534,gid=65534,mode=0700",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m,uid=65534,gid=65534,mode=0700",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/workspace",
            "--env",
            "ZHOUXU_PREVIEW_SUFFIX=" + suffix,
            self.pinned,
            "python",
            "-c",
            PREVIEW_WORKER,
        ]
        try:
            code, out, err = await self.command(
                args, data=data, timeout=50, limit=21 * 1024 * 1024
            )
        except asyncio.TimeoutError:
            await asyncio.shield(self.command(["rm", "--force", name_id], timeout=10))
            raise RuntimeError("preview_conversion_timeout") from None
        if code:
            message = err.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError((message[-1] if message else "preview_conversion_failed")[:300])
        if not out.startswith(b"<!doctype html>"):
            raise RuntimeError("preview_html_invalid")
        return out

    async def command(self, args, data=None, timeout=EXECUTION_TIMEOUT_SECONDS, limit=65536):
        if not self.docker:
            raise RuntimeError("sandbox_unavailable")
        process = await asyncio.create_subprocess_exec(
            self.docker,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def drain(stream):
            output = bytearray()
            while chunk := await stream.read(8192):
                output.extend(chunk[: max(0, limit - len(output))])
            return bytes(output)

        async def communicate():
            async def feed():
                try:
                    if data:
                        process.stdin.write(data)
                        await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()

            _, out, err, _ = await asyncio.gather(
                feed(), drain(process.stdout), drain(process.stderr), process.wait()
            )
            return process.returncode, out, err

        try:
            return await asyncio.wait_for(communicate(), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise

    async def world(self, run_id):
        async with self.admission:
            return await self._world(run_id)

    async def _world(self, run_id):
        if run_id in self.worlds:
            return self.worlds[run_id]
        if len(self.worlds) >= 4:
            raise RuntimeError("sandbox_capacity_reached")
        if self.pinned is None:
            code, out, _ = await self.command(
                ["image", "inspect", self.image, "--format", "{{.Id}}"]
            )
            if code or not out.strip().startswith(b"sha256:"):
                raise RuntimeError("sandbox_image_unavailable")
            self.pinned = out.decode().strip()
        name = "zhouxu-task-" + uuid4().hex
        args = [
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--label",
            "zhouxu.execution=1",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--ulimit",
            "fsize=16777216:16777216",
            "--tmpfs",
            "/workspace:rw,nosuid,nodev,size=67108864,uid=65534,gid=65534,mode=0700",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=33554432,uid=65534,gid=65534,mode=0700",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            self.pinned,
            "sleep",
            "3600",
        ]
        self.worlds[run_id] = name
        try:
            code, _, _ = await self.command(args)
            if code:
                raise RuntimeError("sandbox_start_failed")
        except BaseException:
            await asyncio.shield(self.destroy(run_id))
            raise
        return name

    async def destroy(self, run_id):
        name = self.worlds.pop(run_id, None)
        if name:
            await self.command(["rm", "--force", name], timeout=10)

    async def close_run(self, run_id):
        await self.destroy(run_id)
        self.locks.pop(run_id, None)
        self.staged.pop(run_id, None)
        self.failures.pop(run_id, None)

    async def stage_materials(self, run_id, catalog):
        if run_id in self.staged:
            return
        entries = []
        for material_id, parsed in catalog.runs.get(run_id, {}).items():
            text = "\n".join(unit.text for unit in parsed.units)
            if len(text.encode("utf-8")) > 180000:
                entries.append(
                    {
                        "material_id": material_id,
                        "name": parsed.manifest.name,
                        "status": "use_read_material_for_large_source",
                    }
                )
                continue
            path = (
                "inputs/"
                + hashlib.sha256(material_id.encode()).hexdigest()[:24]
                + ".txt"
            )
            result = await self.operate(
                run_id, "write", {"path": path, "content": text}
            )
            if not result.get("ok"):
                raise RuntimeError("material_stage_failed")
            entries.append(
                {
                    "material_id": material_id,
                    "name": parsed.manifest.name,
                    "path": path,
                    "sha256": result["sha256"],
                    "status": str(parsed.manifest.status),
                    "units": [
                        {
                            "id": u.unit_id,
                            "locator": u.locator.model_dump(exclude_none=True),
                        }
                        for u in parsed.units
                    ],
                    "notice": "解析文本，不代表全文或原件；原文定位使用read_material返回值，不要把自行拆分的文本行号冒充原文页/段号。",
                }
            )
        result = await self.operate(
            run_id,
            "write",
            {
                "path": "inputs/index.json",
                "content": json.dumps(entries, ensure_ascii=False),
            },
        )
        if not result.get("ok"):
            raise RuntimeError("material_index_stage_failed")
        self.staged[run_id] = entries

    async def operate(self, run_id, op, value):
        allowed = {
            "read": {"path", "offset"},
            "write": {"path", "content", "expected_sha256"},
            "edit": {"path", "old_text", "new_text", "expected_sha256"},
            "bash": {"command"},
            "present": {"path"},
        }
        if op not in allowed or not isinstance(value, dict) or set(value) - allowed[op]:
            return {"ok": False, "error": "invalid_operation_fields"}
        async with self.locks.setdefault(run_id, asyncio.Lock()):
            name = await self.world(run_id)
            try:
                if op == "bash":
                    command = value.get("command")
                    if (
                        not isinstance(command, str)
                        or not command.strip()
                        or len(command) > 20000
                    ):
                        return {
                            "ok": False,
                            "error": "command_required_under_20000_chars",
                        }
                    code, out, err = await self.command(
                        ["exec", name, "bash", "--noprofile", "--norc", "-c", command],
                        timeout=EXECUTION_TIMEOUT_SECONDS,
                    )
                    diagnostic = {}
                    if code:
                        signature = (
                            err.decode(errors="replace").strip().splitlines()
                            or ["exit:" + str(code)]
                        )[-1][:300]
                        failures = self.failures.setdefault(run_id, {})
                        failures[signature] = failures.get(signature, 0) + 1
                        if failures[signature] >= 5:
                            return {
                                "ok": False,
                                "error": "execution_repeated_failure",
                                "message": "同一错误已出现五次，本轮执行停止。请修正任务方案；未交付文件不能视为完成。",
                            }
                        if failures[signature] >= 2:
                            diagnostic = {
                                "repair_required": "相同错误重复发生。先读取失败脚本和实际数据结构、定位报错行，再修改逻辑；不要只改文件名或重复运行原逻辑。"
                            }
                    return {
                        **diagnostic,
                        "ok": code == 0,
                        "exit_code": code,
                        "stdout": out.decode(errors="replace"),
                        "stderr": err.decode(errors="replace"),
                        "output_limit_bytes": 65536,
                    }
                data = json.dumps(
                    {**value, "op": "export" if op == "present" else op},
                    ensure_ascii=False,
                ).encode()
                code, out, _ = await self.command(
                    ["exec", "-i", name, "python", "-c", WORKER],
                    data,
                    limit=15 * 1024 * 1024,
                )
                if code:
                    return {"ok": False, "error": "sandbox_worker_failed"}
                result = json.loads(out)
                if op == "present" and result.get("ok"):
                    path = value["path"]
                    filename = Path(path).name
                    if Path(filename).suffix.lower() not in {
                        ".xlsx",
                        ".docx",
                        ".csv",
                        ".txt",
                        ".md",
                        ".json",
                        ".pdf",
                    }:
                        return {"ok": False, "error": "unsupported_delivery_type"}
                    raw = base64.b64decode(result.pop("data"), validate=True)
                    try:
                        validate_artifact(filename, raw)
                        item = self.store.save(run_id, filename, raw)
                    except ValueError as exc:
                        return {
                            "ok": False,
                            "error": str(exc),
                            "message": "文件尚未通过交付检查，请修复真实文件后重新交付。",
                        }
                    return {
                        "ok": True,
                        "artifact": item,
                        "notice": "文件快照已保存，前端提供鉴权下载；不等于专业内容已复核。",
                    }
                return result
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Killing only docker exec would leave the payload running.
                await asyncio.shield(self.destroy(run_id))
                raise


def execution_tools(runtime):
    definitions = {
        "read": (
            "读取本任务工作区UTF-8文件或列目录，返回sha256。授权材料的解析文本目录为inputs/index.json，可供代码直接读取；大材料与原文定位仍用read_material，不接受宿主路径。",
            {"path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}},
            ["path"],
        ),
        "write": (
            "在本任务隔离工作区创建文本文件。覆盖已有文件须提供read返回的expected_sha256。",
            {
                "path": {"type": "string"},
                "content": {"type": "string", "maxLength": 200000},
                "expected_sha256": {"type": "string"},
            },
            ["path", "content"],
        ),
        "edit": (
            "精确替换已读取文件中唯一匹配的old_text；必须提供当前sha256，不匹配则先重新读取。",
            {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "expected_sha256": {"type": "string"},
            },
            ["path", "old_text", "new_text", "expected_sha256"],
        ),
        "bash": (
            "在当前任务Linux容器执行命令，600秒超时；无网络、无宿主挂载。可运行Python、openpyxl、python-docx。先读取真实材料，写脚本、运行并检查输出，出错须修复；禁止把容器文件路径当下载链接。工作区/workspace，容量64MiB。",
            {"command": {"type": "string", "maxLength": 20000}},
            ["command"],
        ),
        "present": (
            "把实际存在的工作区文件快照交付给用户。生成Word/Excel后须用代码重新打开检查内容再调用；文件存在不等于事实或法律正确。输入相对路径，系统生成真实下载入口，不自行编造URL。",
            {"path": {"type": "string"}},
            ["path"],
        ),
    }

    class Tool:
        def __init__(self, name, description, properties, required):
            self.name = name
            self.runtime = runtime
            self.spec = ToolSpec(
                name=name,
                description=description,
                parameters={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                read_only=name == "read",
            )

        async def execute_context(self, request):
            if (request.context or {}).get("user_facts", {}).get("task_mode") != "work":
                raise PortError(
                    Error(
                        code="execution_not_authorized",
                        message="聊天模式未授权任务代码执行；请在工作模式发起任务。",
                        retryable=False,
                    )
                )
            try:
                if getattr(self, "material_catalog", None) is not None:
                    await runtime.stage_materials(request.run_id, self.material_catalog)
                result = await runtime.operate(
                    request.run_id, self.name, request.input.arguments
                )
                if result.get("error") == "execution_repeated_failure":
                    raise PortError(
                        Error(
                            code="execution_repeated_failure",
                            message=result["message"],
                            retryable=False,
                        )
                    )
            except asyncio.TimeoutError:
                result = {
                    "ok": False,
                    "error": "execution_timeout_workspace_destroyed",
                    "message": "运行超过600秒，容器及其子进程已停止；交付快照不受影响。",
                }
            except (RuntimeError, OSError, ValueError, json.JSONDecodeError):
                raise PortError(
                    Error(
                        code="sandbox_unavailable",
                        message="隔离执行环境不可用，未回退为宿主执行。",
                        retryable=False,
                    )
                ) from None
            return ActionResult(
                tool_name=self.name,
                action_id=request.input.action_id,
                outcome="ok" if result.get("ok") else "error",
                result=result,
                side_effects=[] if self.name == "read" else ["private_task_workspace"],
            )

    return {
        name: (tool.spec, tool)
        for name, args in definitions.items()
        for tool in [Tool(name, *args)]
    }
