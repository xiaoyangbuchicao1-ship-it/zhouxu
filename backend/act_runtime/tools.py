"""ACT 可调用工具与权限边界。"""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from perception_layer.contracts import ContentUnit, SearchRequest
from perception_layer.retrieval import LocalHybridSearch
from perception_layer.web_search import SearxngWebSearch

from .contracts import ToolCall, ToolObservation, WorkspaceFile
from .python_runner import PythonRunner


def _function_tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_DEFINITIONS = [
    _function_tool(
        "case_search",
        "在当前对话的全部案件材料中查找原文。适合事实、合同、邮件、时间和双方主张。",
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 1500},
            "purpose": {"type": "string", "minLength": 1, "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
        },
        ["query", "purpose"],
    ),
    _function_tool(
        "resource_search",
        "在用户已关联到当前对话的资源库资料中查找参考原文。不得把结果写成案件事实。",
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 1500},
            "purpose": {"type": "string", "minLength": 1, "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
        },
        ["query", "purpose"],
    ),
    _function_tool(
        "web_search",
        "查找最新、公开、外部的规则、案例或网站信息。案件原件内容应使用 case_search。",
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 1500},
            "purpose": {"type": "string", "minLength": 1, "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        ["query", "purpose"],
    ),
    _function_tool(
        "python_run",
        "在无网络的隔离 Python 环境执行计算或文件分析。输入在 /workspace/input，可将成果写到 /workspace/output。",
        {
            "purpose": {"type": "string", "minLength": 1, "maxLength": 240},
            "code": {"type": "string", "minLength": 1, "maxLength": 30000},
        },
        ["purpose", "code"],
    ),
]


class ActToolbox:
    def __init__(
        self,
        *,
        search: LocalHybridSearch,
        web_search: SearxngWebSearch,
        case_units: Sequence[ContentUnit],
        resource_units: Sequence[ContentUnit],
        workspace_files: Sequence[WorkspaceFile],
        python_runner: PythonRunner,
        run_key: str,
    ):
        self.search = search
        self.web_search = web_search
        self.case_units = list(case_units)
        self.resource_units = list(resource_units)
        self.workspace_files = list(workspace_files)
        self.python_runner = python_runner
        self.run_key = run_key

    @property
    def definitions(self) -> list[dict]:
        return TOOL_DEFINITIONS

    def context_note(self) -> str:
        if not self.workspace_files:
            return "\n\n## ACT 工具工作区\n当前没有可供 Python 读取的原件。"
        lines = [
            f"- {item.display_name}：/workspace/input/{item.alias}（{item.source_scope}）"
            for item in self.workspace_files
        ]
        return (
            "\n\n## ACT 工具工作区\n"
            "Python 每次在无网络的隔离环境运行。可读输入文件，成果写入 "
            "/workspace/output。工作区文件：\n" + "\n".join(lines)
        )

    @staticmethod
    def _arguments(call: ToolCall) -> tuple[str, str, int]:
        query = str(call.arguments.get("query", "")).strip()
        purpose = str(call.arguments.get("purpose", "")).strip()[:200]
        try:
            limit = max(1, min(12, int(call.arguments.get("limit", 6))))
        except (TypeError, ValueError):
            limit = 6
        return query[:1500], purpose or "查找回答所需信息", limit

    def _search_units(self, call: ToolCall, units: Sequence[ContentUnit], scope: str) -> ToolObservation:
        started = time.monotonic()
        query, purpose, limit = self._arguments(call)
        if not query:
            return ToolObservation(call.id, call.name, "failed", "{\"error\":\"查询不能为空\"}", "查询为空")
        hits, trace = self.search.search([SearchRequest(purpose, query)], units, limit=limit)
        results = [
            {
                "filename": hit.unit.filename,
                "locator": hit.unit.locator,
                "text": hit.unit.text[:3500],
                "source_scope": hit.unit.source_scope,
                "lexical_rank": hit.lexical_rank,
                "semantic_rank": hit.semantic_rank,
            }
            for hit in hits[:limit]
        ]
        payload = {
            "scope": scope,
            "query": query,
            "result_count": len(results),
            "results": results,
            "retrieval": {
                "lexical": trace.get("lexical_ranker"),
                "semantic_status": trace.get("semantic_status"),
                "fusion": trace.get("fusion"),
            },
        }
        return ToolObservation(
            call.id, call.name, "completed", json.dumps(payload, ensure_ascii=False),
            f"找到 {len(results)} 个{scope}内容单元",
            int((time.monotonic() - started) * 1000),
        )

    def _web(self, call: ToolCall) -> ToolObservation:
        started = time.monotonic()
        query, purpose, limit = self._arguments(call)
        if not query:
            return ToolObservation(call.id, call.name, "failed", "{\"error\":\"查询不能为空\"}", "查询为空")
        hits, trace = self.web_search.search([SearchRequest(purpose, query)])
        results = [
            {
                "title": hit.title,
                "url": hit.url,
                "snippet": hit.snippet[:1200],
                "published_at": hit.published_at,
            }
            for hit in hits[: min(8, limit)]
        ]
        status = "completed" if trace.get("status") == "ready" else "failed"
        payload = {"query": query, "results": results, "search_trace": trace}
        return ToolObservation(
            call.id, call.name, status, json.dumps(payload, ensure_ascii=False),
            f"外部搜索返回 {len(results)} 条结果" if results else "外部搜索没有可用结果",
            int((time.monotonic() - started) * 1000),
        )

    def _python(self, call: ToolCall) -> ToolObservation:
        purpose = str(call.arguments.get("purpose", "")).strip()[:240] or "执行计算"
        code = str(call.arguments.get("code", ""))
        result = self.python_runner.run(
            code, self.workspace_files, run_key=f"{self.run_key}-{call.id}"
        )
        payload = {
            "status": result.status,
            "runtime": result.runtime,
            "purpose": purpose,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "artifacts": list(result.artifacts),
        }
        if result.status == "completed":
            summary = purpose + (f"；生成 {len(result.artifacts)} 个文件" if result.artifacts else "")
        else:
            summary = purpose + "失败，已将错误返回模型"
        return ToolObservation(
            call.id, call.name, result.status, json.dumps(payload, ensure_ascii=False),
            summary, result.duration_ms, list(result.artifacts),
        )

    def execute(self, call: ToolCall) -> ToolObservation:
        try:
            if call.name == "case_search":
                return self._search_units(call, self.case_units, "案件材料")
            if call.name == "resource_search":
                return self._search_units(call, self.resource_units, "资源库")
            if call.name == "web_search":
                return self._web(call)
            if call.name == "python_run":
                return self._python(call)
            return ToolObservation(
                call.id, call.name, "refused",
                json.dumps({"error": f"不允许的工具：{call.name}"}, ensure_ascii=False),
                "工具不在允许列表中",
            )
        except Exception as exc:
            return ToolObservation(
                call.id, call.name, "failed",
                json.dumps(
                    {"error": f"{type(exc).__name__}：{str(exc)[:500]}"},
                    ensure_ascii=False,
                ),
                f"工具执行失败：{type(exc).__name__}",
            )

