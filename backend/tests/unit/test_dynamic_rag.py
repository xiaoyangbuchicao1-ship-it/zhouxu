import asyncio

import pytest

from backend.config.settings import load_settings
from backend.contracts.modules.dynamic_rag import (
    Evidence,
    EvidenceReview,
    RagInput,
    RetrievalFilters,
    SearchBatch,
)
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.modules.dynamic_rag.service import DynamicRag


def evidence(key="a", text="original evidence", **kwargs):
    return Evidence(
        chunk_id=key, document_id="doc", parent_id=key,
        kind="article", chunk_text=text, **kwargs,
    )


def request(query="question", **kwargs):
    return ModuleRequest(run_id="run", call_id="retrieval", input=RagInput(query=query, **kwargs))


class Search:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []
        self.plans = []

    async def search(self, query, filters, limit):
        self.plans.append(query)
        self.calls.append((query if isinstance(query, str) else query.query, filters, limit))
        batch = self.batches[min(len(self.calls) - 1, len(self.batches) - 1)]
        if isinstance(batch, Exception):
            raise batch
        return SearchBatch(items=batch, method="hybrid")


class Reviewer:
    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.calls = []

    async def review(self, query, evidence, previous_queries, filters):
        self.calls.append((query, evidence, previous_queries, filters))
        result = self.reviews[min(len(self.calls) - 1, len(self.reviews) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


async def test_observation_driven_followup_preserves_scope_and_originals():
    settings = load_settings()
    first, second = evidence(), evidence("b", "missing supporting clause")
    search = Search([[first], [second]])
    review = Reviewer([
        EvidenceReview(decision="search", selected_ids=["a"], next_query="missing clause", missing_aspects=["exception"]),
        EvidenceReview(decision="sufficient", selected_ids=["b", "a"]),
    ])
    value = request(filters=RetrievalFilters(current_only=True, document_id="doc"))
    response = await DynamicRag(search, settings, review).handle(value)
    assert response.status == "ok" and response.output.sufficiency == "sufficient"
    assert [item.chunk_id for item in response.output.items] == ["b", "a"]
    assert [call[0] for call in search.calls] == ["question", "missing clause"]
    assert all(call[1] == value.input.filters for call in search.calls)
    assert review.calls[1][2] == ["question", "missing clause"]
    response.output.items[1].chunk_text = "changed"
    assert first.chunk_text == "original evidence"


async def test_sufficient_evidence_does_not_always_run_extra_searches():
    search = Search([[evidence()]])
    review = Reviewer([EvidenceReview(decision="sufficient", selected_ids=["a"])])
    result = await DynamicRag(search, load_settings(), review).handle(request())
    assert len(search.calls) == 1
    assert result.output.stop_reason == "sufficient"


@pytest.mark.parametrize("query", ["", "question", "  QUESTION  "])
async def test_empty_or_repeated_followup_stops(query):
    search = Search([[evidence()]])
    review = Reviewer([EvidenceReview(decision="search", next_query=query)])
    result = await DynamicRag(search, load_settings(), review).handle(request())
    assert len(search.calls) == 1
    assert result.output.stop_reason in {"invalid_followup", "repeated_query"}


async def test_no_new_evidence_allows_one_bounded_generic_expansion():
    settings = load_settings()
    settings.rag.max_query_expansions = 1
    search = Search([[evidence()], [evidence()], [evidence("b", "expanded evidence")]])
    review = Reviewer([
        EvidenceReview(decision="search", selected_ids=["a"], next_query="followup"),
        EvidenceReview(decision="search", selected_ids=["a"], next_query="followup"),
        EvidenceReview(decision="sufficient", selected_ids=["b", "a"]),
    ])
    result = await DynamicRag(search, settings, review).handle(request())
    assert result.output.stop_reason == "sufficient"
    assert len(search.calls) == 3 and len(review.calls) == 3
    assert "query_expansion_applied" in result.output.notices
    assert search.calls[2][0] == "question followup"


async def test_round_budget_is_enforced_and_not_called_sufficient():
    settings = load_settings()
    settings.rag.max_rounds = 2
    search = Search([[evidence()], [evidence("b")], [evidence("c")]])
    review = Reviewer([
        EvidenceReview(decision="search", next_query="second"),
        EvidenceReview(decision="search", next_query="third"),
    ])
    result = await DynamicRag(search, settings, review).handle(request())
    assert len(search.calls) == 2
    assert result.output.stop_reason == "round_limit"
    assert result.output.sufficiency == "insufficient"


async def test_empty_is_ok_but_database_failure_is_failed():
    settings = load_settings()
    empty = await DynamicRag(Search([[]]), settings).handle(request())
    assert empty.status == "ok" and empty.output.items == []
    failed = await DynamicRag(Search([PortError(settings.error("rag_search_failed"))]), settings).handle(request())
    assert failed.status == "failed"
    assert failed.error.code == "rag_search_failed"


async def test_later_failure_returns_explicit_partial_evidence():
    settings = load_settings()
    review = Reviewer([EvidenceReview(decision="search", selected_ids=["a"], next_query="more")])
    search = Search([[evidence()], PortError(settings.error("rag_search_failed"))])
    result = await DynamicRag(search, settings, review).handle(request())
    assert result.status == "failed" and len(result.output.items) == 1
    assert result.output.sufficiency == "unassessed"


async def test_review_unavailable_degrades_without_losing_evidence():
    settings = load_settings()
    review = Reviewer([PortError(settings.error("rag_review_failed"))])
    result = await DynamicRag(Search([[evidence()]]), settings, review).handle(request())
    assert result.status == "ok" and len(result.output.items) == 1
    assert result.output.sufficiency == "unassessed"
    assert "review_unavailable" in result.output.notices


async def test_unanswerable_can_return_no_selected_evidence():
    review = Reviewer([EvidenceReview(decision="insufficient", missing_aspects=["private facts"])])
    result = await DynamicRag(Search([[evidence()]]), load_settings(), review).handle(request())
    assert result.output.items == [] and result.output.sufficiency == "insufficient"
    assert result.output.missing_aspects == ["private facts"]


async def test_fabricated_ids_cannot_become_evidence_or_success():
    review = Reviewer([EvidenceReview(decision="sufficient", selected_ids=["fabricated"])])
    result = await DynamicRag(Search([[evidence()]]), load_settings(), review).handle(request())
    assert result.output.sufficiency == "unassessed"
    assert [item.chunk_id for item in result.output.items] == ["a"]
    assert "review_unknown_ids_ignored" in result.output.notices


async def test_output_budget_keeps_chunks_intact_and_downgrades_sufficiency():
    settings = load_settings()
    settings.rag.evidence_budget_chars = 1
    review = Reviewer([EvidenceReview(decision="sufficient", selected_ids=["a"])])
    item = evidence(text="cannot silently truncate")
    result = await DynamicRag(Search([[item]]), settings, review).handle(request())
    assert result.output.items == [] and result.output.omitted_ids == ["a"]
    assert result.output.sufficiency == "unassessed"
    assert item.chunk_text == "cannot silently truncate"


async def test_duplicate_identity_conflict_is_not_silently_overwritten():
    review = Reviewer([EvidenceReview(decision="search", next_query="more")])
    result = await DynamicRag(
        Search([[evidence()], [evidence(text="different")]]), load_settings(), review,
    ).handle(request())
    assert result.status == "failed" and result.error.code == "rag_invalid_evidence"


async def test_timeout_has_real_bounded_wait():
    class SlowSearch:
        async def search(self, *_):
            await asyncio.sleep(10)

    settings = load_settings()
    settings.rag.total_timeout_seconds = 0.02
    result = await DynamicRag(SlowSearch(), settings).handle(request())
    assert result.status == "failed" and result.error.code == "rag_timeout"
    assert result.output.elapsed_seconds < 1


async def test_cancellation_propagates():
    class CancelledSearch:
        async def search(self, *_):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await DynamicRag(CancelledSearch(), load_settings()).handle(request())


@pytest.mark.parametrize("query", [" ", "x" * 1001])
async def test_invalid_query_never_reaches_search(query):
    search = Search([[]])
    result = await DynamicRag(search, load_settings()).handle(request(query))
    assert result.status == "needs_input" and search.calls == []


async def test_independent_calls_do_not_share_pool():
    service = DynamicRag(Search([[evidence()]]), load_settings())
    a, b = await asyncio.gather(service.handle(request("one")), service.handle(request("two")))
    a.output.items[0].chunk_text = "altered"
    assert b.output.items[0].chunk_text == "original evidence"


async def test_second_review_failure_cannot_reuse_previous_sufficiency():
    settings = load_settings()
    reviewer = Reviewer([
        EvidenceReview(decision="search", selected_ids=["a"], next_query="more"),
        PortError(settings.error("rag_review_failed")),
    ])
    result = await DynamicRag(Search([[evidence()], [evidence("b")]]), settings, reviewer).handle(request())
    assert result.status == "ok"
    assert result.output.sufficiency == "unassessed"
    assert {item.chunk_id for item in result.output.items} == {"a", "b"}
