from dataclasses import replace
from pathlib import Path

from perception_layer.chunking import build_content_units
from perception_layer.contracts import ContentUnit, SearchPlan, SearchRequest
from perception_layer.engine import PerceptionEngine
from perception_layer.planning import RuleBasedSearchPlanner
from perception_layer.retrieval import LocalHybridSearch
from perception_layer.web_search import WebSearchHit


def material(identifier: str, filename: str, text: str, *, current: bool = False) -> dict:
    return {
        "id": identifier,
        "filename": filename,
        "extracted_text": text,
        "status": "read",
        "issue": None,
        "message_id": None if current else "message-old",
        "created_at": "2026-10-08T00:00:00+00:00",
    }


def test_structure_aware_chunks_keep_clause_and_table_meaning_together():
    source = material(
        "m1",
        "合同与记录.md",
        """# 租船合同

第六条 通知送达
NOR 应发送至指定邮箱。发送给代理人的副本不能替代指定邮箱送达。

第七条 计时
通知符合约定并送达后，等待十二小时开始计时。允许装货时间为连续二十四小时。

# 作业记录
第1行：事项 | 时间 | 备注
第2行：到港 | 2026-09-01 12:00 UTC | 船方记录
第3行：完成 | 2026-09-04 00:00 UTC | 未列中断
""",
    )
    units = build_content_units(source, max_tokens=80, merge_target_tokens=70)

    notice = next(unit for unit in units if "指定邮箱" in unit.text)
    timing = next(unit for unit in units if "等待十二小时" in unit.text)
    table = next(unit for unit in units if "第1行" in unit.text)

    assert notice.text.startswith("第六条 通知送达")
    assert "代理人的副本" in notice.text
    assert timing.text.startswith("第七条 计时")
    assert "允许装货时间" in timing.text
    assert "第2行" in table.text and "第3行" in table.text
    assert all(unit.locator and unit.material_id == "m1" for unit in units)
    assert all(
        unit.sentence_complete
        for unit in units
        if unit.boundary_reason in {"sentence_boundary", "hard_limit"}
    )


class FailingPlanner:
    def plan(self, *args, **kwargs):
        raise AssertionError("短案卷不应调用检索规划模型")


def test_small_case_reads_everything_without_planner_or_retrieval():
    engine = PerceptionEngine(planner=FailingPlanner(), evidence_budget_tokens=4_000)
    materials = [
        material("m1", "合同.md", "第六条 通知应发送至指定邮箱。"),
        material("m2", "邮件.eml", "To: agent@example.invalid\nSubject: NOR\n已发出。"),
    ]

    package = engine.prepare("通知是否按约送达？", [], materials)

    assert package.mode == "full"
    assert package.trace["macro"]["selected_unit_count"] == package.trace["macro"][
        "content_unit_count"
    ]
    assert "合同.md" in package.context and "邮件.eml" in package.context
    assert package.trace["plan"]["planner"] == "skipped_full_read"


class ControlledEncoder:
    name = "controlled-semantic-test"

    def encode(self, texts, *, query=False):
        vectors = []
        for text in texts:
            if query:
                vectors.append([1.0, 0.0])
            elif "邮箱在傍晚确认收件" in text:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


def test_semantic_search_recalls_meaning_without_shared_keywords():
    relevant = ContentUnit(
        "u1",
        "m1",
        "回执.eml",
        1,
        "正文",
        (),
        "text",
        "邮箱在傍晚确认收件。",
        "邮箱在傍晚确认收件。",
        10,
        "document_end",
        True,
    )
    distractor = ContentUnit(
        "u2",
        "m2",
        "其他.md",
        1,
        "正文",
        (),
        "text",
        "船舶完成装货。",
        "船舶完成装货。",
        8,
        "document_end",
        True,
    )
    search = LocalHybridSearch(ControlledEncoder())

    hits, trace = search.search(
        [SearchRequest("核对收件时点", "通知何时送达指定地址")],
        [distractor, relevant],
    )

    assert hits[0].unit.id == "u1"
    assert hits[0].semantic_rank == 1
    assert trace["semantic_encoder"] == "controlled-semantic-test"


def test_large_case_is_bounded_and_current_upload_is_forced_into_context():
    old_text = "\n\n".join(
        f"## 旧材料第{index}节\n旧事项{index}。" + "背景叙述。" * 80
        for index in range(20)
    )
    materials = [
        material("old", "历史案卷.md", old_text),
        material(
            "new",
            "新增回执.eml",
            "To: charterer.ops@example.invalid\nSubject: NOR\n指定邮箱于18:00收到通知。",
            current=True,
        ),
    ]
    engine = PerceptionEngine(
        planner=RuleBasedSearchPlanner(),
        search=LocalHybridSearch(),
        evidence_budget_tokens=700,
        answer_reserve_tokens=1_000,
        history_budget_tokens=1_000,
    )

    package = engine.prepare("新回执如何影响通知起算？", [], materials)
    macro = package.trace["macro"]

    assert package.mode in {"hybrid", "retrieval"}
    assert "新增回执.eml" in package.context
    assert "指定邮箱于18:00收到通知" in package.context
    assert macro["selected_evidence_tokens"] <= macro["evidence_budget_tokens"]
    assert macro["selected_unit_count"] < macro["content_unit_count"]
    assert macro["within_context_budget"] is True
    assert "上下文预算检查通过" in package.trace_text


def test_history_keeps_recent_messages_and_finds_an_older_related_turn():
    history = []
    for index in range(14):
        history.append(
            {
                "id": f"h{index}",
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    "指定邮箱回执说明18点收到。"
                    if index == 1
                    else f"无关往来记录 {index}。"
                ),
            }
        )
    engine = PerceptionEngine(history_budget_tokens=2_000)

    package = engine.prepare("之前指定邮箱几点收到？", history, [])

    assert package.trace["history"]["recent_messages"] == 8
    assert package.trace["history"]["retrieved_older_messages"] >= 1
    assert any("18点" in item["content"] for item in package.history_messages)


def test_micro_trace_exposes_chunk_boundaries_and_source_locations():
    engine = PerceptionEngine()
    package = engine.prepare(
        "核对费率",
        [],
        [material("m1", "合同.md", "# 费率\n超过允许时间按每24小时12,000美元计算。")],
    )

    chunk = package.trace["micro"]["chunks"][0]
    assert chunk["filename"] == "合同.md"
    assert chunk["locator"] == "费率"
    assert chunk["selected"] is True
    assert "12,000美元" in chunk["preview"]
    assert "分块边界检查通过" in package.trace_text


class FakeWebSearch:
    def search(self, requests, *, per_query_limit=5):
        if not requests:
            return [], {"status": "not_needed", "queries": [], "result_count": 0, "errors": []}
        return [
            WebSearchHit(
                "公开规则说明",
                "https://example.org/rule",
                "这是用于测试的公开规则摘要。",
                "2026-10-01",
            )
        ], {
            "status": "ready",
            "endpoint_configured": True,
            "queries": [{"purpose": item.purpose, "query": item.query} for item in requests],
            "result_count": 1,
            "errors": [],
        }


def test_web_search_is_a_separate_tool_and_never_masquerades_as_case_evidence():
    class WebPlanner:
        def plan(self, current_request, history, manifest):
            return SearchPlan((), (), "scripted_model", (SearchRequest("核对规则", current_request),))

    engine = PerceptionEngine(
        planner=WebPlanner(),
        web_search=FakeWebSearch(),
    )

    resource = material("reference", "规则.md", "参考资料，不是当前案件事实。")
    resource_units = [replace(unit, source_scope="knowledge_resource") for unit in build_content_units(resource)]
    package = engine.prepare("这个约定现在还适用吗？", [], [], [resource], resource_units)

    assert package.trace["plan"]["web_requests"]
    assert package.trace["web_search"]["status"] == "ready"
    assert "## 外部公开资料" in package.context
    assert "不能替代案件原件" in package.context
    assert "https://example.org/rule" in package.context
    assert "外部搜索返回 1 条结果" in package.trace_text


def test_linked_resources_do_not_force_retrieval_when_model_declines():
    class NoSearchPlanner:
        def plan(self, *args):
            return SearchPlan((), (), "scripted_model")

    resource = material("reference", "规则.md", "只在用户问题需要时读取的参考资料。")
    resource_units = [replace(unit, source_scope="knowledge_resource") for unit in build_content_units(resource)]
    package = PerceptionEngine(planner=NoSearchPlanner()).prepare("你好", [], [], [resource], resource_units)
    assert package.trace["plan"]["requests"] == []
    assert package.trace["search"]["engine"] == "not_needed"
    assert package.selected_units == []


def test_raw_query_fallback_keeps_history_without_guessing_external_intent():
    planner = RuleBasedSearchPlanner()
    plan = planner.plan(
        "现在怎么办？", [{"role": "user", "content": "争议是通知何时有效送达。"}], "材料清单"
    )
    assert "通知何时有效送达" in plan.requests[0].query
    assert "现在怎么办" in plan.requests[0].query
    assert plan.planner == "raw_query_fallback"
    assert planner.plan("请联网搜索最新规定", [], "").web_requests == ()


def test_long_table_chunks_repeat_header_without_breaking_rows():
    rows = ["第1行：事项 | 时间 | 说明"] + [
        f"第{index}行：事件{index} | 2026-09-{index:02d} | " + "完整记录。" * 12
        for index in range(2, 18)
    ]
    units = build_content_units(
        material("table", "装卸时间表.xlsx", "\n".join(rows)),
        max_tokens=90,
        merge_target_tokens=70,
    )

    assert len(units) > 1
    assert all(unit.text.startswith("第1行：事项 | 时间 | 说明") for unit in units)
    assert all(unit.kind == "table_row" for unit in units)
    assert all(unit.estimated_tokens <= 90 for unit in units)
