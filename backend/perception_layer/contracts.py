"""感知层各部件之间唯一共享的数据合同。"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field


_CJK = re.compile(r"[\u3400-\u9fff]")
_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9._@:/-]*", re.I)


def estimate_tokens(text: str) -> int:
    """保守估计中英文混排 token；最终装配仍保留安全余量。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    ascii_words = len(_ASCII_TOKEN.findall(text))
    remaining = max(0, len(text) - cjk)
    return max(1, math.ceil(cjk / 1.45 + ascii_words * 1.15 + remaining / 9))


@dataclass(frozen=True)
class ContentUnit:
    id: str
    material_id: str
    filename: str
    ordinal: int
    locator: str
    heading_path: tuple[str, ...]
    kind: str
    text: str
    search_text: str
    estimated_tokens: int
    boundary_reason: str
    sentence_complete: bool
    previous_id: str | None = None
    next_id: str | None = None
    current_upload: bool = False
    source_scope: str = "case_material"
    embedding: tuple[float, ...] | None = field(default=None, repr=False, compare=False)

    def trace_dict(self) -> dict:
        value = asdict(self)
        value["heading_path"] = list(self.heading_path)
        value["preview"] = re.sub(r"\s+", " ", self.text)[:220]
        value.pop("search_text", None)
        value.pop("text", None)
        value.pop("embedding", None)
        return value


@dataclass(frozen=True)
class SearchRequest:
    purpose: str
    query: str


@dataclass(frozen=True)
class SearchPlan:
    requests: tuple[SearchRequest, ...]
    referenced_files: tuple[str, ...] = ()
    planner: str = "rules"
    web_requests: tuple[SearchRequest, ...] = ()


@dataclass(frozen=True)
class SearchHit:
    unit: ContentUnit
    score: float
    lexical_rank: int | None
    semantic_rank: int | None
    purposes: tuple[str, ...]


@dataclass
class PerceptionPackage:
    mode: str
    context: str
    history_messages: list[dict[str, str]]
    trace: dict
    trace_text: str
    selected_units: list[ContentUnit] = field(repr=False)
    all_units: list[ContentUnit] = field(default_factory=list, repr=False)
