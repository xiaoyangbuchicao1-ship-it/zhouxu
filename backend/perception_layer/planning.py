"""把开放问题变成有界的案件材料检索计划。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable, Protocol, Sequence

from .contracts import SearchPlan, SearchRequest


class SearchPlanner(Protocol):
    def plan(
        self, current_request: str, recent_history: Sequence[dict], manifest: str
    ) -> SearchPlan: ...


class RuleBasedSearchPlanner:
    """模型规划不可用时，以原始问题与历史进行本地检索，不猜测工具意图。"""

    def plan(self, current_request: str, recent_history: Sequence[dict], manifest: str) -> SearchPlan:
        query = current_request.strip()
        if recent_history:
            previous_user = next(
                (
                    item["content"]
                    for item in reversed(recent_history)
                    if item.get("role") == "user"
                ),
                "",
            )
            query = f"{previous_user}\n{query}".strip()
        referenced = tuple(
            sorted(
                {
                    name
                    for name in re.findall(
                        r"[\w\-\u3400-\u9fff]+\.(?:pdf|docx?|xlsx?|eml|txt|md|html?)",
                        current_request,
                        re.I,
                    )
                }
            )
        )
        return SearchPlan(
            (SearchRequest("回答用户当前问题", query),), referenced, "raw_query_fallback", ()
        )


class DeepSeekToolPlanner:
    """让轻量模型决定检索范围与查询；失败时只用原始问题回退本地检索。"""

    TOOL = {
        "type": "function",
        "function": {
            "name": "case_context_search",
            "description": "在当前会话的案件材料和历史对话中查找回答用户问题所需的原文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "requests": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "properties": {
                                "purpose": {"type": "string"},
                                "query": {"type": "string"},
                            },
                            "required": ["purpose", "query"],
                        },
                    },
                    "referenced_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                    },
                },
                "required": ["requests", "referenced_files"],
            },
        },
    }
    WEB_TOOL = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "仅当问题需要最新、公开、外部法律规则、案例或网站信息时搜索互联网。",
            "parameters": {
                "type": "object",
                "properties": {
                    "requests": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "purpose": {"type": "string"},
                                "query": {"type": "string"},
                            },
                            "required": ["purpose", "query"],
                        },
                    }
                },
                "required": ["requests"],
            },
        },
    }

    def __init__(
        self,
        key_supplier: Callable[[], str],
        *,
        endpoint: str,
        model: str = "deepseek-flash",
        timeout: float = 60.0,
    ):
        self.key_supplier = key_supplier
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.fallback = RuleBasedSearchPlanner()

    def plan(self, current_request: str, recent_history: Sequence[dict], manifest: str) -> SearchPlan:
        history_text = "\n".join(
            f"{item.get('role')}：{item.get('content', '')[:1000]}" for item in recent_history[-6:]
        ) or "无"
        messages = [
            {
                "role": "system",
                "content": (
                    "你只负责规划检索，不回答问题。案件事实、合同和当事人说法必须使用"
                    "case_context_search；只有最新、公开、外部法律规则、案例或网站信息才使用"
                    "web_search。可以同时调用两个工具。每项查询覆盖精确名称、同义表达、时间、"
                    "金额或主体；不要因为材料清单没有摘要就推断内容不存在。"
                    "不需要检索时不调用工具，不要仅因用户选择了参考资料就强制检索。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前问题：\n{current_request}\n\n近期对话：\n{history_text}"
                    f"\n\n材料清单：\n{manifest}"
                ),
            },
        ]
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "thinking": {"type": "disabled"},
                "max_tokens": 1200,
                "tools": [self.TOOL, self.WEB_TOOL],
                "tool_choice": "auto",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.key_supplier(),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
            calls = result["choices"][0]["message"].get("tool_calls") or []
            case_arguments: dict = {}
            web_arguments: dict = {}
            for call in calls:
                name = call.get("function", {}).get("name")
                arguments = json.loads(call["function"]["arguments"])
                if name == "case_context_search":
                    case_arguments = arguments
                elif name == "web_search":
                    web_arguments = arguments
            requests = tuple(
                SearchRequest(str(item["purpose"])[:200], str(item["query"])[:1000])
                for item in case_arguments.get("requests", [])[:6]
                if str(item.get("query", "")).strip()
            )
            referenced = tuple(
                str(value)[:255] for value in case_arguments.get("referenced_files", [])[:20]
            )
            web_requests = tuple(
                SearchRequest(str(item["purpose"])[:200], str(item["query"])[:1000])
                for item in web_arguments.get("requests", [])[:4]
                if str(item.get("query", "")).strip()
            )
            return SearchPlan(requests, referenced, f"tool:{self.model}", web_requests)
        except (
            KeyError,
            StopIteration,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            urllib.error.URLError,
            TimeoutError,
            RuntimeError,
        ) as exc:
            fallback = self.fallback.plan(current_request, recent_history, manifest)
            return SearchPlan(
                fallback.requests,
                fallback.referenced_files,
                f"raw_query_fallback:{type(exc).__name__}",
                fallback.web_requests,
            )
