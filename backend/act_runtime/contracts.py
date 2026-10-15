"""ACT 模型、工具与执行器之间的数据合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def assistant_wire_value(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(frozen=True)
class ModelDecision:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content or None,
        }
        if self.tool_calls:
            message["tool_calls"] = [call.assistant_wire_value() for call in self.tool_calls]
        return message


@dataclass(frozen=True)
class WorkspaceFile:
    source_path: str
    alias: str
    display_name: str
    source_scope: str


@dataclass
class ToolObservation:
    call_id: str
    tool_name: str
    status: str
    content: str
    summary: str
    duration_ms: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def tool_message(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "name": self.tool_name,
            "content": self.content,
        }

    def trace_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool": self.tool_name,
            "status": self.status,
            "summary": self.summary,
            "duration_ms": self.duration_ms,
            "artifacts": self.artifacts,
            "observation": self.content[:20_000],
        }


@dataclass(frozen=True)
class CodeRunResult:
    status: str
    stdout: str
    stderr: str
    duration_ms: int
    artifacts: tuple[dict[str, Any], ...] = ()
    runtime: str = "docker"


@dataclass
class ActPreparation:
    messages: list[dict[str, Any]]
    trace: dict[str, Any]

