import pytest

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import DecisionProposal, RunInput
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus
from backend.infrastructure.tools.maritime import CorpusTool
from backend.tests.support import ScriptedModel


async def test_invented_internal_source_cannot_finish_as_completed(tmp_path):
    settings = load_settings()
    tool = CorpusTool()
    bad = "/api/maritime/sources/laws/invented?snapshot=" + "a" * 64
    model = ScriptedModel(
        *[
            DecisionProposal(kind="answer", answer=f"依据：[资料]({bad})")
            for _ in range(3)
        ],
        settings=settings,
    )
    harness = build_harness(
        model=model,
        settings=settings,
        events=MemoryEventSink(),
        tools={tool.spec.name: (tool.spec, tool)},
    )
    result = await harness.run(RunInput(text="引用来源"))
    assert result.status == "failed"


async def test_real_snapshot_resolves_and_allows_completion(tmp_path):
    corpus = MaritimeCorpus(tmp_path / "corpus.db")
    item = {"id": "law-1", "kind": "laws", "title": "验收片段", "text": "真实测试文本"}
    key = corpus.remember_source(item)
    tool = CorpusTool()
    tool.corpus = corpus
    settings = load_settings()
    answer = f"[原文](/api/maritime/sources/laws/law-1?snapshot={key})"
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer=answer), settings=settings
    )
    harness = build_harness(
        model=model,
        settings=settings,
        events=MemoryEventSink(),
        tools={tool.spec.name: (tool.spec, tool)},
    )
    result = await harness.run(RunInput(text="引用来源"))
    assert result.status == "completed" and result.answer == answer


async def test_invalid_citation_in_earlier_stage_also_prevents_completion():
    settings = load_settings()
    tool = CorpusTool()
    model = ScriptedModel(
        DecisionProposal(
            kind="answer",
            answer="/api/maritime/sources/laws/invented?snapshot=" + "a" * 64,
            complete=False,
            remaining_work=["继续总结"],
        ),
        DecisionProposal(kind="answer", answer="整理完毕。"),
        settings=settings,
    )
    harness = build_harness(
        model=model,
        settings=settings,
        events=MemoryEventSink(),
        tools={tool.spec.name: (tool.spec, tool)},
    )
    result = await harness.run(RunInput(text="先阶段成果后总结"))
    assert result.status == "failed"


@pytest.mark.parametrize(
    "prefix", ["/api/maritime/%73ources/laws/", "/maritime/sources/laws/"]
)
async def test_encoded_and_short_route_citations_are_checked(prefix):
    settings = load_settings()
    tool = CorpusTool()
    model = ScriptedModel(
        *[
            DecisionProposal(
                kind="answer",
                answer=f"[伪造]({prefix}missing?snapshot=" + "a" * 64 + ")",
            )
            for _ in range(3)
        ],
        settings=settings,
    )
    harness = build_harness(
        model=model,
        settings=settings,
        events=MemoryEventSink(),
        tools={tool.spec.name: (tool.spec, tool)},
    )
    assert (await harness.run(RunInput(text="引用"))).status == "failed"


async def test_short_citation_is_expanded_from_actual_snapshot(tmp_path):
    corpus = MaritimeCorpus(tmp_path / "corpus.db")
    key = corpus.remember_source(
        {"id": "id#中文", "kind": "laws", "title": "真实片段", "text": "不可改写的原文"}
    )
    tool = CorpusTool(corpus)
    settings = load_settings()
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer=f"规则依据 [[ZX:{key[:16]}]]"),
        settings=settings,
    )
    harness = build_harness(
        model=model,
        settings=settings,
        events=MemoryEventSink(),
        tools={tool.spec.name: (tool.spec, tool)},
    )
    result = await harness.run(RunInput(text="引用"))
    assert result.status == "completed" and f"?snapshot={key}" in result.answer
    assert "/api/maritime/sources/laws/id%23" in result.answer
    assert "[[" not in result.answer
