import json

import pytest

from backend.config.settings import load_settings
from backend.contracts.modules.dynamic_rag import EvidenceReview, RagQueryPlan
from backend.contracts.modules.loop import DecisionProposal, RunInput
from backend.infrastructure.tools.rag import RagSearchTool
from backend.modules.dynamic_rag.query_plan import article_locations, prepare_query
from backend.modules.dynamic_rag.service import DynamicRag
from backend.tests.integration.test_rag_harness import build, call, proposal
from backend.tests.support import ScriptedModel
from backend.tests.unit.test_dynamic_rag import Reviewer, Search, evidence, request


@pytest.mark.parametrize("text", ["51-53", "第五十一条至第五十三条", "第51至53条"])
def test_location_ranges(text):
    assert article_locations(text, 20) == ["第五十一条", "第五十二条", "第五十三条"]


@pytest.mark.parametrize(
    "text", ["100-1", "0", "9999-10001", "1-9999", "后面三条", "50 OR 1=1"]
)
def test_invalid_optional_locations_do_not_block_query(text):
    plan = prepare_query(
        RagQueryPlan(query="question", location=text), load_settings().rag
    )
    assert plan.article_nos == [] and "invalid_location_ignored" in plan.notices


def test_dates_are_not_article_ranges_and_model_anchors_merge():
    plan = prepare_query(
        RagQueryPlan(
            query="2023-2026 国际海运条例 无船承运 终止经营",
            document_name="国际海运条例",
            anchors=["终止经营", "备案"],
        ),
        load_settings().rag,
    )
    assert not plan.article_nos
    assert plan.anchors[:2] == ["终止经营", "备案"]


def test_multiple_document_mentions_do_not_silently_pick_one_scope():
    plan = prepare_query(
        RagQueryPlan(query="海商法 防治船舶污染海洋环境管理条例 赔偿责任"),
        load_settings().rag,
    )
    assert plan.document_name is None
    plan = prepare_query(
        RagQueryPlan(query="国际海运条例第三十二条与第三十一条"), load_settings().rag
    )
    assert plan.article_nos == ["第三十二条", "第三十一条"]


def test_invalid_optional_field_types_use_defaults_without_losing_query():
    plan = RagQueryPlan.model_validate(
        dict(
            query="海商法第50条",
            document_name={},
            location=[],
            content_query=12,
            anchors=["航线", None, "航线"],
            version_scope="invented",
            limit=-5,
        )
    )
    assert (
        plan.anchors == ["航线"] and plan.limit is None and plan.version_scope == "all"
    )
    assert prepare_query(plan, load_settings().rag).article_nos == ["第五十条"]


def test_tool_schema_only_exposes_query_plan_not_database_filters_or_runtime_ids():
    cfg = load_settings()
    schema = RagSearchTool(DynamicRag(Search([]), cfg), cfg).spec.parameters
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {
        "query",
        "document_name",
        "location",
        "content_query",
        "anchors",
        "version_scope",
        "limit",
    }


async def test_out_of_scope_followup_returns_to_main_without_futile_search():
    search = Search([[evidence()], [evidence("b")]])
    reviewer = Reviewer(
        [
            EvidenceReview(decision="search", next_query="其他条例 第90条 缺失内容"),
            EvidenceReview(decision="sufficient", selected_ids=["b"]),
        ]
    )
    result = await DynamicRag(search, load_settings(), reviewer).handle(
        request(
            "测试条例 第51至53条",
            document_name="测试条例",
            location="51-53",
            version_scope="historical",
        )
    )
    assert len(search.plans) == 1
    assert result.output.stop_reason == "scope_change_required"
    assert result.output.sufficiency == "insufficient"
    assert [item.chunk_id for item in result.output.items] == ["a"]
    objective = json.loads(reviewer.calls[0][0])
    assert objective["retrieval_task"]["article_nos"] == [
        "第五十一条",
        "第五十二条",
        "第五十三条",
    ]


async def test_second_turn_keeps_original_question_and_selected_prior_user_words():
    cfg = load_settings()
    search = Search([[evidence()]])
    reviewer = Reviewer([EvidenceReview(decision="sufficient", selected_ids=["a"])])
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer="历史模型回答不能成为证据"),
        proposal(
            call(
                cfg,
                query="海商法第五十一至五十三条",
                document_name="海商法",
                location="51-53",
            )
        ),
        DecisionProposal(kind="answer", answer="done"),
    )
    harness = build(model, DynamicRag(search, cfg, reviewer), cfg)
    await harness.run(RunInput(text="海商法第五十条是什么？"), session_id="s")
    result = await harness.run(RunInput(text="那后面3条呢？"), session_id="s")
    assert result.status == "completed"
    objective = json.loads(reviewer.calls[0][0])
    assert objective["request"] == "那后面3条呢？"
    assert objective["conversation_context"][0]["text"] == "海商法第五十条是什么？"
    assert "历史模型回答" not in reviewer.calls[0][0]
    assert search.plans[0].article_nos == ["第五十一条", "第五十二条", "第五十三条"]
