"""Production composition chooses optional inner review without hiding retrieval."""

from contextlib import asynccontextmanager

import pytest

from backend.bootstrap import open_harness, open_rag
from backend.config.settings import load_settings
from backend.contracts.modules.dynamic_rag import RagInput, SearchBatch
from backend.contracts.modules.loop import DecisionProposal, RunInput
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.support import ScriptedModel
from backend.tests.unit.test_dynamic_rag import evidence


@pytest.fixture
def ports(monkeypatch):
    observed = {"roles": [], "searches": [], "closed": 0}

    class Encoder:
        def __init__(self, settings):
            pass

        async def encode(self, text):
            return [1.0]

        def close(self):
            observed["closed"] += 1

    class Retriever:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def search(self, plan, filters, limit):
            observed["searches"].append(plan.query)
            return SearchBatch(items=[evidence(text="原始规则正文")], method="hybrid")

    @asynccontextmanager
    async def model(*, settings, events, role):
        observed["roles"].append(role)
        yield ScriptedModel(
            DecisionProposal(kind="answer", answer="你好"), settings=settings
        )

    monkeypatch.setattr("backend.infrastructure.search.bge_m3.BgeM3Encoder", Encoder)
    monkeypatch.setattr(
        "backend.infrastructure.search.opensearch.OpenSearchRetriever", Retriever
    )
    monkeypatch.setattr("backend.infrastructure.model.factory.open_model", model)
    return observed


def test_inner_review_default_is_disabled():
    assert load_settings().rag.review_enabled is False
    assert load_settings().rag_tool.enabled is False


async def test_default_retrieval_does_not_open_review_model_but_returns_originals(ports):
    settings = load_settings()
    async with open_rag(settings=settings, events=MemoryEventSink()) as rag:
        assert rag.reviewer is None
        value = await rag.handle(
            ModuleRequest(
                run_id="run", call_id="search", input=RagInput(query="规则疑点")
            )
        )
    assert ports["roles"] == []
    assert ports["searches"] == ["规则疑点"]
    assert value.output.items[0].chunk_text == "原始规则正文"
    assert value.output.sufficiency == "unassessed"
    assert value.output.stop_reason == "single_pass"
    assert ports["closed"] == 1


@pytest.mark.parametrize(
    "configured, override, enabled",
    [(True, None, True), (True, False, False), (False, True, True)],
)
async def test_review_opt_in_and_explicit_diagnostic_override(
    ports, configured, override, enabled
):
    settings = load_settings()
    settings.rag = settings.rag.model_copy(update={"review_enabled": configured})
    async with open_rag(
        settings=settings, events=MemoryEventSink(), dynamic=override
    ) as rag:
        assert (rag.reviewer is not None) is enabled
    assert ports["roles"] == (["review"] if enabled else [])


async def test_production_harness_keeps_tool_without_opening_review_model(ports):
    settings = load_settings()
    settings.rag_tool.enabled = True  # Exercise the optional vector adapter explicitly.
    async with open_harness(settings=settings, events=MemoryEventSink()) as harness:
        tool = harness.modules["action"].tools[settings.rag_tool.name]
        assert tool.module.reviewer is None
        result = await harness.run(RunInput(text="你好"))
    assert result.status == "completed"
    assert result.answer == "你好"
    assert ports["roles"] == ["main"]
    assert ports["searches"] == []
