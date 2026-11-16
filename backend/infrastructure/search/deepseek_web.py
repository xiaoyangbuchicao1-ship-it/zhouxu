"""Small, explicit adapter for DeepSeek's Search and Fetch HTTP APIs.

The adapter intentionally owns only provider concerns: endpoint validation,
authentication, HTTP status mapping, and response-shape validation.  The
Harness-facing tools in ``infrastructure.tools`` own budgets and receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import SecretStr

from backend.config.settings import Settings
from backend.contracts.schemas.core import PortError


@dataclass(frozen=True)
class DeepSeekWebSearchResponse:
    results: list[dict[str, Any]]
    retrieved_at: str


@dataclass(frozen=True)
class DeepSeekWebFetchResponse:
    title: str
    content: str
    retrieved_at: str


class DeepSeekWebError(Exception):
    """A provider failure with a stable, public-facing error code."""

    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class DeepSeekWebClient:
    """Direct Search/Fetch client using the same key loader as the model."""

    def __init__(
        self,
        settings: Settings,
        key: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.key = key
        self.transport = transport
        self.endpoint = self._validate_endpoint(settings.web.endpoint)

    def _validate_endpoint(self, endpoint: str) -> str:
        try:
            url = httpx.URL(endpoint)
        except httpx.InvalidURL:
            raise PortError(self.settings.error("invalid_endpoint")) from None
        if (
            url.scheme != "https"
            or url.host not in self.settings.web.allowed_hosts
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise PortError(self.settings.error("invalid_endpoint"))
        return str(url).rstrip("/")

    async def search(
        self,
        query: str,
        *,
        count: int,
        original_question: str | None = None,
    ) -> DeepSeekWebSearchResponse:
        if not query.strip() or len(query) > self.settings.web.max_query_chars:
            raise DeepSeekWebError("web_invalid_query")
        if count < 1 or count > 50:
            raise DeepSeekWebError("web_invalid_query")
        body: dict[str, Any] = {
            "query": query,
            "count": count,
            "need_content": False,
        }
        bounded_question = _bounded_text(
            original_question, self.settings.web.max_ori_question_chars
        )
        if bounded_question:
            body["ori_question"] = bounded_question
        payload = await self._post("/search", body)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise DeepSeekWebError("web_invalid_response")
        results = [_validate_search_result(item) for item in raw_results]
        return DeepSeekWebSearchResponse(
            results=results,
            retrieved_at=_now(),
        )

    async def fetch(self, url: str) -> DeepSeekWebFetchResponse:
        _validate_target_url(url, self.settings.web.max_url_chars)
        payload = await self._post("/fetch", {"url": url})
        title = payload.get("title")
        content = payload.get("content")
        if not isinstance(title, str) or not isinstance(content, str):
            raise DeepSeekWebError("web_invalid_response")
        if not content.strip():
            raise DeepSeekWebError("web_empty_content")
        return DeepSeekWebFetchResponse(
            title=title,
            content=content,
            retrieved_at=_now(),
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.web.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.endpoint}{path}",
                    headers={
                        "Authorization": "Bearer " + self.key.get_secret_value(),
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": self.settings.web.user_agent,
                    },
                    json=body,
                )
        except httpx.TimeoutException:
            raise DeepSeekWebError("web_timeout", retryable=True) from None
        except httpx.RequestError:
            raise DeepSeekWebError("web_unavailable", retryable=True) from None

        if response.status_code == 401:
            raise DeepSeekWebError("web_credential_invalid")
        if response.status_code == 402:
            raise DeepSeekWebError("web_quota_exhausted")
        if response.status_code == 429:
            raise DeepSeekWebError("web_rate_limited", retryable=True)
        if response.status_code in {408, 500, 502, 503, 504}:
            raise DeepSeekWebError("web_unavailable", retryable=True)
        if response.status_code == 404 and path == "/fetch":
            raise DeepSeekWebError("web_target_unavailable")
        if response.status_code == 403:
            raise DeepSeekWebError("web_target_unavailable")
        if response.status_code == 404:
            raise DeepSeekWebError("web_unavailable")
        if response.status_code in {400, 413}:
            raise DeepSeekWebError("web_request_rejected")
        if not response.is_success:
            raise DeepSeekWebError("web_unavailable", retryable=True)
        try:
            payload = response.json()
        except ValueError:
            raise DeepSeekWebError("web_invalid_response") from None
        if not isinstance(payload, dict):
            raise DeepSeekWebError("web_invalid_response")
        return payload


def _validate_search_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeepSeekWebError("web_invalid_response")
    required = ("title", "url", "snippet", "summary", "content", "authority")
    if any(not isinstance(value.get(key), (str, int)) for key in required):
        raise DeepSeekWebError("web_invalid_response")
    if not isinstance(value["title"], str) or not isinstance(value["url"], str):
        raise DeepSeekWebError("web_invalid_response")
    if not isinstance(value["snippet"], str) or not isinstance(value["summary"], str):
        raise DeepSeekWebError("web_invalid_response")
    if not isinstance(value["content"], str):
        raise DeepSeekWebError("web_invalid_response")
    if not isinstance(value["authority"], int):
        raise DeepSeekWebError("web_invalid_response")
    date = value.get("date")
    if date is not None and not isinstance(date, str):
        raise DeepSeekWebError("web_invalid_response")
    score = value.get("rerank_score")
    if score is not None and not isinstance(score, (int, float)):
        raise DeepSeekWebError("web_invalid_response")
    site_name = value.get("site_name")
    if site_name is not None and not isinstance(site_name, str):
        raise DeepSeekWebError("web_invalid_response")
    return {
        "title": value["title"],
        "url": value["url"],
        "snippet": value["snippet"],
        "summary": value["summary"],
        "content": value["content"],
        "date": date,
        "authority": value["authority"],
        "rerank_score": score,
        "site_name": site_name,
    }


def _validate_target_url(url: str, max_chars: int) -> None:
    if len(url) > max_chars:
        raise DeepSeekWebError("web_invalid_url")
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise DeepSeekWebError("web_invalid_url") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.host
        or parsed.username
        or parsed.password
    ):
        raise DeepSeekWebError("web_invalid_url")


def _bounded_text(value: str | None, max_chars: int) -> str:
    if not value:
        return ""
    collapsed = " ".join(value.split())
    return "".join(list(collapsed)[:max_chars])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
