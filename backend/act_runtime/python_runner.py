"""Docker 隔离的 Python 执行器。"""

from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Protocol, Sequence

from .contracts import CodeRunResult, WorkspaceFile


class PythonRunner(Protocol):
    runtime_name: str

    def run(
        self, code: str, files: Sequence[WorkspaceFile], *, run_key: str
    ) -> CodeRunResult: ...


class DockerPythonRunner:
    """每次在无网络、只读根文件系统的临时容器中执行代码。"""

    runtime_name = "docker-python"

    def __init__(
        self,
        root: Path,
        *,
        image: str = "python:3.12-slim",
        timeout_seconds: int = 30,
        max_code_characters: int = 30_000,
        max_output_characters: int = 64_000,
        max_artifact_bytes: int = 100 * 1024 * 1024,
    ):
        self.root = root / "act_runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.max_code_characters = max_code_characters
        self.max_output_characters = max_output_characters
        self.max_artifact_bytes = max_artifact_bytes

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[:limit] + f"\n…已截断 {len(value) - limit} 个字符"

    def _failure(self, message: str, started: float) -> CodeRunResult:
        return CodeRunResult(
            "failed", "", message, int((time.monotonic() - started) * 1000), runtime=self.runtime_name
        )

    def run(
        self, code: str, files: Sequence[WorkspaceFile], *, run_key: str
    ) -> CodeRunResult:
        started = time.monotonic()
        if not code.strip():
            return self._failure("没有可执行的 Python 代码", started)
        if len(code) > self.max_code_characters:
            return self._failure(
                f"代码超过 {self.max_code_characters} 字符限制", started
            )
        if shutil.which("docker") is None:
            return self._failure("Docker 命令不可用，无法启动隔离代码环境", started)

        safe_key = "".join(char for char in run_key if char.isalnum() or char in "-_")[:80]
        workspace = self.root / f"{safe_key or 'run'}-{uuid.uuid4().hex[:10]}"
        input_dir = workspace / "input"
        program_dir = workspace / "program"
        output_dir = workspace / "output"
        for folder in (input_dir, program_dir, output_dir):
            folder.mkdir(parents=True, exist_ok=False)
        (program_dir / "main.py").write_text(code, encoding="utf-8")
        try:
            for item in files:
                source = Path(item.source_path)
                if source.is_file():
                    shutil.copy2(source, input_dir / item.alias)
        except OSError as exc:
            shutil.rmtree(workspace, ignore_errors=True)
            return self._failure(f"准备输入文件失败：{type(exc).__name__}", started)

        container_name = "zhouxu-act-" + uuid.uuid4().hex[:18]
        command = [
            "docker", "run", "--rm", "--name", container_name,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "64",
            "--cpus", "1",
            "--memory", "1g",
            "--user", "65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{input_dir.resolve()}:/workspace/input:ro",
            "-v", f"{program_dir.resolve()}:/workspace/program:ro",
            "-v", f"{output_dir.resolve()}:/workspace/output:rw",
            "-w", "/workspace",
            self.image,
            "python", "-I", "-B", "/workspace/program/main.py",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
            shutil.rmtree(workspace, ignore_errors=True)
            return self._failure(
                f"Python 执行超过 {self.timeout_seconds} 秒，已终止", started
            )
        except OSError as exc:
            shutil.rmtree(workspace, ignore_errors=True)
            return self._failure(f"无法启动 Docker：{type(exc).__name__}", started)

        artifacts: list[dict] = []
        total_bytes = 0
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file():
                continue
            size = path.stat().st_size
            total_bytes += size
            artifacts.append(
                {
                    "filename": str(path.relative_to(output_dir)).replace("\\", "/"),
                    "size": size,
                    "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                }
            )
        stdout = self._clip(completed.stdout or "", self.max_output_characters)
        stderr = self._clip(completed.stderr or "", self.max_output_characters)
        duration = int((time.monotonic() - started) * 1000)
        if total_bytes > self.max_artifact_bytes:
            shutil.rmtree(workspace, ignore_errors=True)
            return CodeRunResult(
                "failed", stdout,
                f"生成文件合计超过 {self.max_artifact_bytes} 字节限制",
                duration, runtime=self.runtime_name,
            )
        shutil.rmtree(input_dir, ignore_errors=True)
        shutil.rmtree(program_dir, ignore_errors=True)
        return CodeRunResult(
            "completed" if completed.returncode == 0 else "failed",
            stdout,
            stderr or ("" if completed.returncode == 0 else f"进程退出码 {completed.returncode}"),
            duration,
            tuple(artifacts),
            self.runtime_name,
        )

