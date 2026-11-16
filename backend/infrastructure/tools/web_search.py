"""Harness tool for DeepSeek web search discovery."""

import json

from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.modules.web import WebSearchInput
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.search.deepseek_web import (
    DeepSeekWebClient,
    DeepSeekWebError,
)


class WebSearchTool:
    def __init__(self, client: DeepSeekWebClient, settings: Settings):
        self.client, self.settings = client, settings
        self.spec = ToolSpec(
            name="web_search",
            description=settings.web.search_description,
            parameters=WebSearchInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            value = WebSearchInput.model_validate(request.input.arguments)
        except ValueError:
            return _error(request, self.settings, "web_invalid_query")
        if (
            self.settings.web.max_calls_per_run
            and (request.context or {}).get("tool_call_index", 1)
            > self.settings.web.max_calls_per_run
        ):
            return _error(request, self.settings, "web_call_limit")
        try:
            response = await self.client.search(
                value.query,
                count=min(value.count, self.settings.web.max_search_results),
                original_question=(request.context or {}).get("user_request"),
            )
        except DeepSeekWebError as exc:
            return _error(
                request,
                self.settings,
                exc.code,
                retryable=exc.retryable,
            )
        items = []
        for index, item in enumerate(response.results, start=1):
            projected = {
                "rank": index,
                "title": item["title"],
                "url": item["url"],
                "snippet": item["snippet"],
                "summary": item["summary"],
                "date": item["date"],
                "authority": item["authority"],
                "rerank_score": item["rerank_score"],
                "site_name": item["site_name"],
            }
            items.append(projected)
        result = {
            "status": "ok" if items else "empty",
            "query": value.query,
            "results": items,
            "retrieved_at": response.retrieved_at,
            "content_policy": "搜索结果是候选来源；需要正文和哈希回执必须调用 web_read。",
        }
        encoded = json.dumps(result, ensure_ascii=False)
        budget = min(
            self.settings.web.result_budget_chars,
            (request.context or {}).get(
                "tool_result_chars", self.settings.web.result_budget_chars
            ),
        )
        if len(encoded) > budget:
            while len(items) > 1 and len(encoded) > budget:
                items.pop()
                result["results"] = items
                encoded = json.dumps(result, ensure_ascii=False)
            if len(encoded) > budget:
                return _error(request, self.settings, "web_tool_budget")
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            outcome="ok" if items else "empty",
            result=result,
            session_summary={
                "status": result["status"],
                "query": value.query,
                "source_refs": [
                    {
                        "url": item["url"],
                        "title": item["title"],
                        "date": item["date"],
                        "authority": item["authority"],
                    }
                    for item in items
                ],
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
