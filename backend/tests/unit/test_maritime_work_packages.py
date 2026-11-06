import pytest

from backend.application.maritime.catalog import builtin_skills
from backend.application.maritime.work_packages import (
    WorkPackageStore,
    WorkPackageTool,
    validate_work,
)
from backend.contracts.modules.loop import ActionRequest
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus


def claim(amount="1.01"):
    return {
        "skill_id": "claim-calculation",
        "title": "演示明细",
        "sections": {
            "amounts": [
                {
                    "项目": "测试货损",
                    "数量": "3",
                    "单价": "0.335",
                    "币种": "USD",
                    "金额": amount,
                }
            ],
            "uncertainties": [
                {
                    "条件": "责任",
                    "当前值": "待核",
                    "缺失依据": "合同",
                    "影响": "是否应赔",
                }
            ],
        },
    }


def test_claim_work_recalculates_and_rejects_wrong_amount():
    assert validate_work(claim())["checks"]["totals"] == {"USD": "1.01"}
    with pytest.raises(ValueError, match="金额"):
        validate_work(claim("1.00"))
    with pytest.raises(ValueError):
        validate_work(claim("NaN"))


@pytest.mark.parametrize(
    "method",
    [s for s in builtin_skills() if s["id"] != "claim-calculation"],
    ids=lambda s: s["id"],
)
def test_each_method_requires_its_own_deliverable_contract(method):
    sections = {
        s["key"]: [{col: "待核" for col in s["columns"]}]
        for s in method["deliverables"]
    }
    result = validate_work(
        {"skill_id": method["id"], "title": "结构验收样例", "sections": sections}
    )
    assert [s["key"] for s in result["sections"]] == [
        s["key"] for s in method["deliverables"]
    ]
    del sections[method["deliverables"][0]["key"]][0][
        method["deliverables"][0]["columns"][0]
    ]
    with pytest.raises(ValueError, match="列名"):
        validate_work({"skill_id": method["id"], "title": "缺列", "sections": sections})


@pytest.mark.asyncio
async def test_tool_saves_only_validated_payload_in_trusted_run(tmp_path):
    store = WorkPackageStore(tmp_path / "packages.db")
    tool = WorkPackageTool(store, MaritimeCorpus(tmp_path / "corpus.db"))
    request = ModuleRequest(
        run_id="trusted-run",
        call_id="call",
        input=ActionRequest(tool_name=tool.spec.name, arguments=claim("999")),
    )
    failed = await tool.execute_context(request)
    assert failed.outcome == "error" and store.list("trusted-run") == []
    request.input.arguments = claim()
    result = await tool.execute_context(request)
    assert result.outcome == "ok" and result.side_effects == [
        "saved_private_run_deliverable"
    ]
    assert WorkPackageStore(store.path).list("trusted-run")[0]["checks"]["totals"] == {
        "USD": "1.01"
    }
    assert store.list("another-run") == []


def test_unresolved_citation_in_work_is_rejected(tmp_path):
    value = {
        "skill_id": "law-research",
        "title": "检索样例",
        "sections": {
            "rules": [
                {
                    "争点": "待核",
                    "规则": "待核",
                    "适用条件": "待核",
                    "时间版本": "待核",
                    "依据": "[[ZX:aaaaaaaaaaaaaaaa]]",
                }
            ],
            "application": [],
        },
    }
    with pytest.raises(ValueError, match="rules第1行【依据】引用未能解析"):
        validate_work(value, MaritimeCorpus(tmp_path / "none.db"))


def test_schema_repair_hint_shows_rows_instead_of_metadata():
    import json

    value = claim()
    value["sections"]["待核"] = []
    with pytest.raises(ValueError) as failure:
        validate_work(value)
    message = str(failure.value)
    assert "备注放notes" in message and "不要包key/name/columns/rows" in message
    example = json.loads(message[message.index("{") :])
    assert isinstance(example["amounts"], list) and "项目" in example["amounts"][0]
    assert not {"key", "name", "columns", "rows"} & set(example["amounts"][0])


def test_builtin_history_remains_readable_after_method_upgrade(tmp_path):
    from backend.application.maritime.service import MaritimeError, MaritimeOffice

    office = MaritimeOffice(tmp_path / "office.db")
    assert office.skill("reader", "law-research")["version"] == 2
    old = office.skill("reader", "law-research", 1)
    assert old["version"] == 1 and "先识别争点" in old["instructions"]
    with pytest.raises(MaritimeError):
        office.skill("reader", "law-research", 999)


def test_typed_rows_preserve_checks_without_chinese_field_matching():
    value = claim()
    value["sections"] = [
        {"key": "amounts", "rows": [["测试货损", "3", "0.335", "USD", "1.01"]]},
        {"key": "uncertainties", "rows": [["责任", "待核", "合同", "是否应赔"]]},
    ]
    assert validate_work(value)["checks"]["totals"] == {"USD": "1.01"}
    value["sections"][0]["rows"][0].pop()
    with pytest.raises(ValueError, match="需要5格"):
        validate_work(value)


@pytest.mark.asyncio
async def test_repeated_invalid_work_stops_instead_of_spending_run_budget(tmp_path):
    from backend.contracts.schemas.core import PortError

    tool = WorkPackageTool(
        WorkPackageStore(tmp_path / "work.db"), MaritimeCorpus(tmp_path / "corpus.db")
    )
    request = ModuleRequest(
        run_id="invalid-run",
        call_id="c",
        input=ActionRequest(tool_name=tool.spec.name, arguments=claim("999")),
    )
    assert (await tool.execute_context(request)).outcome == "error"
    assert (await tool.execute_context(request)).outcome == "error"
    with pytest.raises(PortError) as stopped:
        await tool.execute_context(request)
    assert stopped.value.error.code == "work_package_validation"
    assert stopped.value.error.retryable is False
    assert "连续三次" in stopped.value.error.message
    assert tool.store.list("invalid-run") == []


def test_evidence_full_dates_reject_inversion_without_inventing_unknown_dates():
    value = {"skill_id": "evidence-review", "title": "时间顺序", "sections": {"timeline": [
        {"时间": "2026年10月8日", "事件": "卸货", "来源": "原文", "原文位置": "第1段", "确定程度": "材料记载"},
        {"时间": "2026-10-07", "事件": "甲称检验完成", "来源": "邮件", "原文位置": "第2段", "确定程度": "单方陈述"}], "gaps": []}}
    with pytest.raises(ValueError, match="未按先后"):
        validate_work(value)
    value["sections"]["timeline"].reverse()
    assert validate_work(value)["checks"]["complete_date_order"] == "checked_when_parseable"
    value["sections"]["timeline"][0]["时间"] = "日期未知"
    assert validate_work(value)["sections"][0]["rows"][0]["时间"] == "日期未知"
    value["sections"]["timeline"][0]["时间"] = "2026年10月9日之前（具体日期未知）"
    assert validate_work(value)["sections"][0]["rows"][0]["时间"].startswith("2026年10月9日之前")
