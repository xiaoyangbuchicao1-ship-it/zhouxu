"""SearXNG 外部搜索适配器；结果与案件原件保持明确隔离。"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Sequence

from .contracts import SearchRequest


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


class SearxngWebSearch:
    def __init__(
        self,
        endpoint: str | None,
        *,
        timeout: float = 8.0,
        retries: int = 2,
        response_limit_bytes: int = 2 * 1024 * 1024,
    ):
        self.endpoint = (endpoint or "").rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.response_limit_bytes = response_limit_bytes

    @property
    def configured(self) -> bool:
        return self.endpoint.startswith(("http://", "https://"))

    def _url(self, query: str) -> str:
        base = self.endpoint if self.endpoint.endswith("/search") else self.endpoint + "/search"
        return base + "?" + urllib.parse.urlencode(
            {"q": query, "format": "json", "language": "zh-CN", "safesearch": "1"}
        )

    def _one(self, query: str, limit: int) -> list[WebSearchHit]:
        request = urllib.request.Request(
            self._url(query), headers={"Accept": "application/json", "User-Agent": "ZhouXu/0.2"}
        )
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read(self.response_limit_bytes + 1)
                if len(raw) > self.response_limit_bytes:
                    raise RuntimeError("response_too_large")
                payload = json.loads(raw)
                output: list[WebSearchHit] = []
                for item in payload.get("results", [])[:limit]:
                    url = str(item.get("url", "")).strip()
                    title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip()
                    snippet = re.sub(
                        r"\s+", " ", re.sub(r"<[^>]+>", " ", str(item.get("content", "")))
                    ).strip()
                    if url.startswith(("http://", "https://")) and (title or snippet):
                        output.append(
                            WebSearchHit(
                                title=title or url,
                                url=url,
                                snippet=snippet[:1200],
                                published_at=str(item.get("publishedDate") or "").strip() or None,
                            )
                        )
                return output
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.15 * (attempt + 1))
        raise RuntimeError(type(last_error).__name__ if last_error else "search_failed")

    def search(
        self, requests: Sequence[SearchRequest], *, per_query_limit: int = 5
    ) -> tuple[list[WebSearchHit], dict]:
        if not requests:
            return [], {"status": "not_requested", "endpoint_configured": self.configured, "queries": []}
        if not self.configured:
            return [], {
                "status": "unconfigured",
                "endpoint_configured": False,
                "queries": [asdict(item) for item in requests],
            }
        hits: list[WebSearchHit] = []
        errors: list[dict] = []
        seen: set[str] = set()
        for request in requests:
            try:
                for hit in self._one(request.query, per_query_limit):
                    normalized = hit.url.split("#", 1)[0]
                    if normalized not in seen:
                        seen.add(normalized)
                        hits.append(hit)
            except RuntimeError as exc:
                errors.append({"purpose": request.purpose, "error": str(exc)})
        return hits, {
            "status": "ready" if not errors else "degraded",
            "endpoint_configured": True,
            "queries": [asdict(item) for item in requests],
            "result_count": len(hits),
            "errors": errors,
        }
