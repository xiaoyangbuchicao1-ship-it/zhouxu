"""案件材料的词法、语义混合检索；接口可替换为外部搜索引擎。"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Protocol, Sequence

from .contracts import ContentUnit, SearchHit, SearchRequest


_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9._@:/-]*", re.I)


def search_tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.casefold())
    terms = list(_ASCII_TOKEN.findall(normalized))
    for sequence in re.findall(r"[\u3400-\u9fff]+", normalized):
        if len(sequence) == 1:
            terms.append(sequence)
        else:
            terms.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
            if len(sequence) >= 3:
                terms.extend(sequence[index : index + 3] for index in range(len(sequence) - 2))
    return [term for term in terms if term.strip()]


def search_terms(text: str) -> set[str]:
    return set(search_tokens(text))


class SemanticEncoder(Protocol):
    name: str

    def encode(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...


class NoSemanticEncoder:
    name = "disabled"

    def encode(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        return []


class FastEmbedEncoder:
    """按需加载轻量多语言模型，小材料完整阅读时不会下载模型。"""

    def __init__(
        self,
        cache_dir: Path,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ):
        self.name = model_name
        self.cache_dir = cache_dir
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(model_name=self.name, cache_dir=str(self.cache_dir))
        return self._model

    def encode(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        return [
            vector.tolist()
            for vector in self._load().embed(
                list(texts), batch_size=32
            )
        ]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


class LocalHybridSearch:
    """使用倒排词项与向量相似度，再以 RRF 合并两组排名。"""

    def __init__(self, encoder: SemanticEncoder | None = None):
        self.encoder = encoder or NoSemanticEncoder()
        self._embedding_cache: dict[str, list[float]] = {}
        self._term_cache: dict[str, Counter[str]] = {}
        self._semantic_error: str | None = None

    def _lexical_scores(self, query: str, units: Sequence[ContentUnit]) -> list[float]:
        query_terms = search_terms(query)
        if not query_terms:
            return [0.0] * len(units)
        documents = []
        for unit in units:
            if unit.id not in self._term_cache:
                self._term_cache[unit.id] = Counter(search_tokens(unit.search_text))
            documents.append(self._term_cache[unit.id])
        frequencies = {
            term: sum(counter.get(term, 0) > 0 for counter in documents)
            for term in query_terms
        }
        average_length = sum(sum(counter.values()) for counter in documents) / max(1, len(documents))
        k1, b = 1.2, 0.75
        scores: list[float] = []
        for counter in documents:
            length = sum(counter.values())
            score = 0.0
            for term in query_terms:
                term_frequency = counter.get(term, 0)
                if not term_frequency:
                    continue
                inverse_frequency = math.log(
                    1 + (len(units) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
                )
                normalizer = term_frequency + k1 * (
                    1 - b + b * length / max(1.0, average_length)
                )
                score += inverse_frequency * term_frequency * (k1 + 1) / normalizer
            scores.append(score)
        return scores

    def _semantic_scores(self, query: str, units: Sequence[ContentUnit]) -> list[float]:
        if isinstance(self.encoder, NoSemanticEncoder) or not units:
            return [0.0] * len(units)
        self._semantic_error = None
        for unit in units:
            if unit.embedding is not None and unit.id not in self._embedding_cache:
                self._embedding_cache[unit.id] = list(unit.embedding)
        missing = [unit for unit in units if unit.id not in self._embedding_cache]
        if missing:
            try:
                vectors = self.encoder.encode([unit.search_text for unit in missing])
                self._embedding_cache.update(
                    {unit.id: vector for unit, vector in zip(missing, vectors)}
                )
            except Exception as exc:
                self._semantic_error = type(exc).__name__
                return [0.0] * len(units)
        try:
            query_vectors = self.encoder.encode([query], query=True)
        except Exception as exc:
            self._semantic_error = type(exc).__name__
            return [0.0] * len(units)
        if not query_vectors:
            return [0.0] * len(units)
        return [
            _cosine(query_vectors[0], self._embedding_cache.get(unit.id, [])) for unit in units
        ]

    def search(
        self, requests: Sequence[SearchRequest], units: Sequence[ContentUnit], *, limit: int = 24
    ) -> tuple[list[SearchHit], dict]:
        combined: dict[str, dict] = {}
        trace_requests: list[dict] = []
        for request in requests:
            lexical = self._lexical_scores(request.query, units)
            semantic = self._semantic_scores(request.query, units)
            lexical_order = sorted(range(len(units)), key=lambda index: lexical[index], reverse=True)
            semantic_order = sorted(range(len(units)), key=lambda index: semantic[index], reverse=True)
            lexical_rank = {
                index: rank
                for rank, index in enumerate(lexical_order, start=1)
                if lexical[index] > 0
            }
            semantic_rank = {
                index: rank
                for rank, index in enumerate(semantic_order, start=1)
                if semantic[index] > 0
            }
            chosen = set(lexical_order[: max(limit, 12)]) | set(
                semantic_order[: max(limit, 12)]
            )
            for index in chosen:
                lex_rank = lexical_rank.get(index)
                sem_rank = semantic_rank.get(index)
                if lex_rank is None and sem_rank is None:
                    continue
                score = (1 / (60 + lex_rank) if lex_rank else 0) + (
                    1 / (60 + sem_rank) if sem_rank else 0
                )
                entry = combined.setdefault(
                    units[index].id,
                    {
                        "unit": units[index],
                        "score": 0.0,
                        "lexical_rank": None,
                        "semantic_rank": None,
                        "purposes": set(),
                    },
                )
                entry["score"] += score
                entry["lexical_rank"] = min(
                    filter(None, [entry["lexical_rank"], lex_rank]), default=None
                )
                entry["semantic_rank"] = min(
                    filter(None, [entry["semantic_rank"], sem_rank]), default=None
                )
                entry["purposes"].add(request.purpose)
            trace_requests.append(
                {
                    "purpose": request.purpose,
                    "query": request.query,
                    "lexical_candidates": sum(value > 0 for value in lexical),
                    "semantic_candidates": sum(value > 0 for value in semantic),
                }
            )
        ranked = sorted(combined.values(), key=lambda item: item["score"], reverse=True)
        hits = [
            SearchHit(
                item["unit"],
                item["score"],
                item["lexical_rank"],
                item["semantic_rank"],
                tuple(sorted(item["purposes"])),
            )
            for item in ranked[:limit]
        ]
        return hits, {
            "engine": "local_hybrid",
            "lexical_ranker": "bm25",
            "fusion": "rrf-k60",
            "semantic_encoder": self.encoder.name,
            "semantic_status": (
                "disabled"
                if isinstance(self.encoder, NoSemanticEncoder)
                else "degraded" if self._semantic_error else "ready"
            ),
            "semantic_error": self._semantic_error,
            "requests": trace_requests,
        }
