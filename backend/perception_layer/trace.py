"""把机器感知记录翻译成可检查说明，并做最低限度的回答验收。"""

from __future__ import annotations

import re

from .contracts import PerceptionPackage


def render_trace(trace: dict) -> str:
    macro, history = trace["macro"], trace["history"]
    modes = {"full": "完整阅读", "hybrid": "混合阅读", "retrieval": "检索阅读"}
    lines = [
        f"本轮收到 {macro['material_count']} 份材料，其中 {macro['current_material_count']} 份是本轮新增。",
        (
            f"本对话关联 {macro.get('linked_resource_count', 0)} 份资源库资料，"
            f"本轮检索采用其中 {macro.get('selected_resource_unit_count', 0)} 个内容单元。"
        ),
        f"解析后形成 {macro['content_unit_count']} 个内容单元，采用{modes.get(macro['mode'], macro['mode'])}模式。",
        (
            f"最终选择 {macro['selected_unit_count']} 个内容单元，约 {macro['selected_evidence_tokens']} 个 Token；"
            f"证据预算为 {macro['evidence_budget_tokens']}，为回答预留 {macro['answer_reserve_tokens']}。"
        ),
        (
            f"历史共有 {history['total_messages']} 条消息，本轮保留 {history['recent_messages']} 条近期消息，"
            f"另找回 {history['retrieved_older_messages']} 条较早相关消息。"
        ),
    ]
    if trace["plan"]["requests"]:
        lines.append(
            "检索围绕以下问题进行："
            + "；".join(item["purpose"] for item in trace["plan"]["requests"])
            + "。"
        )
    if trace["plan"].get("web_requests"):
        web = trace.get("web_search", {})
        if web.get("status") == "ready":
            lines.append(
                f"外部搜索返回 {web.get('result_count', 0)} 条结果，"
                f"其中 {web.get('selected_result_count', 0)} 条进入本轮上下文。"
            )
        elif web.get("status") == "degraded":
            lines.append("外部搜索部分失败，已保留成功结果并记录失败查询。")
        else:
            lines.append("本轮需要外部搜索，但搜索服务尚未配置或没有可用结果。")
    broken = trace["micro"]["broken_boundary_ids"]
    lines.append(
        "分块边界检查通过。"
        if not broken
        else f"发现 {len(broken)} 个可能被硬切开的内容单元，需要回看原文。"
    )
    lines.append(
        "所有入选内容都保留材料和位置。"
        if trace["micro"]["all_selected_units_traceable"]
        else "存在无法回到原件的内容，本轮不应进入推理。"
    )
    lines.append(
        "上下文预算检查通过。"
        if macro["within_context_budget"]
        else "上下文预算超限，需要减少证据后重试。"
    )
    return "\n".join(lines)


def evaluate_answer(answer: str, package: PerceptionPackage) -> dict:
    """只做机械检查；回答是否真正解决问题仍由对照材料和人工判断。"""
    source_names = sorted({unit.filename for unit in package.selected_units})
    mentioned = [name for name in source_names if name in answer]
    repeated_request = bool(
        re.search(r"(?:请|需要|能否).{0,12}(?:继续|再|补充|提供).{0,12}材料", answer)
    )
    internal_reference_leak = bool(
        re.search(r"(?:source-[a-f0-9]+|chunk\d+|unit-[a-f0-9]+|perception-[a-f0-9]+)", answer, re.I)
    )
    return {
        "answer_present": bool(answer.strip()),
        "answer_characters": len(answer.strip()),
        "selected_source_count": len(source_names),
        "sources_named_in_answer": mentioned,
        "asks_for_more_material": repeated_request,
        "internal_reference_leak": internal_reference_leak,
        "engineering_checks_passed": bool(answer.strip())
        and package.trace["macro"]["within_context_budget"]
        and not internal_reference_leak,
        "professional_quality_requires_review": True,
    }
