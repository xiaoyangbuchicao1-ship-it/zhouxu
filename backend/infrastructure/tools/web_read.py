"""Harness tool for DeepSeek page fetching and evidence receipts."""

import hashlib
import json

from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.modules.web import WebReadInput
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.search.deepseek_web import (
    DeepSeekWebClient,
    DeepSeekWebError,
)


class WebReadTool:
    def __init__(self, client: DeepSeekWebClient, settings: Settings):
        self.client, self.settings = client, settings
        self.spec = ToolSpec(
            name="web_read",
            description=settings.web.read_description,
            parameters=WebReadInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            value = WebReadInput.model_validate(request.input.arguments)
        except ValueError:
            return _error(request, self.settings, "web_invalid_url")
        if (
            self.settings.web.max_calls_per_run
            and (request.context or {}).get("tool_call_index", 1)
            > self.settings.web.max_calls_per_run
        ):
            return _error(request, self.settings, "web_call_limit")
        try:
            response = await self.client.fetch(value.url)
        except DeepSeekWebError as exc:
            return _error(
                request,
                self.settings,
                exc.code,
                retryable=exc.retryable,
            )
        full_content = response.content
        digest = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
        budget = min(
            self.settings.web.result_budget_chars,
            (request.context or {}).get(
                "tool_result_chars", self.settings.web.result_budget_chars
            ),
        )
        max_chars = min(
            value.max_chars or self.settings.web.max_content_chars,
            self.settings.web.max_content_chars,
            max(256, budget - 1200),
        )
        content = full_content[:max_chars]
        result = {
            "status": "partial" if len(content) < len(full_content) else "ok",
            "url": value.url,
            "title": response.title,
            "content": content,
            "content_sha256": digest,
            "content_chars": len(full_content),
            "retrieved_at": response.retrieved_at,
            "location": {
                "char_start": 0,
                "char_end": len(content),
                "line_start": 1,
                "line_end": content.count("\n") + 1,
            },
            "truncated": len(content) < len(full_content),
            "evidence_policy": "正文回执可进入证据矩阵；搜索摘要不能替代正文。",
        }
        if len(json.dumps(result, ensure_ascii=False)) > budget:
            return _error(request, self.settings, "web_tool_budget")
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            outcome="partial" if result["truncated"] else "ok",
            result=result,
            session_summary={
                "status": result["status"],
                "url": value.url,
                "title": response.title,
                "retrieved_at": response.retrieved_at,
                "content_sha256": digest,
                "content_chars": len(full_content),
                "visible_chars": len(content),
                "truncated": result["truncated"],
                "location": result["location"],
            },
        )


def _error(request, settings, code: str, *, retryable: bool = False) -> ActionResult:
    error = settings.error(code, retryable=retryable)
    return ActionResult(
        tool_name=request.input.tool_name,
        action_id=request.input.action_id,
        outcome="error",
        error=error,
        result={"status": "failed", "error": error.code, "message": error.message},
        session_summary={"status": "failed", "error": error.code},
    )
