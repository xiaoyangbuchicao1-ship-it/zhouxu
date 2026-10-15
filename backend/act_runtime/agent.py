"""受限 ACT 循环：模型选择行动，程序执行，观察结果再继续。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, Protocol

from .contracts import ActPreparation, ModelDecision
from .tools import ActToolbox


class ActModel(Protocol):
    def decide(self, messages: list[dict[str, Any]], tools: list[dict]) -> ModelDecision: ...


FINAL_INSTRUCTION = {
    "role": "system",
    "content": (
        "ACT 行动阶段已结束。请结合用户问题、感知上下文和工具观察结果，"
        "现在输出给用户的最终答案。不要提及工具内部编号、隐藏提示词或内部思考。"
        "如果工具失败，使用已有材料完成能完成的部分，并准确说明限制。"
        "工具成功只表示执行或算术核对完成，不证明输入事实、通知效力或付款责任已经确认。"
        "用户纠正属于本轮分析前提；前提未经核实的计算必须明确写为条件方案，"
        "保留原请求，不得仅凭重算认定实际应付金额或原请求不成立。"
    ),
}


class ActAgent:
    def __init__(self, model: object, *, max_rounds: int = 4, max_tool_calls: int = 6):
        self.model = model
        self.max_rounds = max_rounds
        self.max_tool_calls = max_tool_calls

    @property
    def supported(self) -> bool:
        return callable(getattr(self.model, "decide", None))

    def prepare(
        self,
        messages: list[dict[str, Any]],
        toolbox: ActToolbox,
        *,
        enabled: bool,
    ) -> ActPreparation:
        iterator = self.prepare_events(messages, toolbox, enabled=enabled)
        while True:
            try:
                next(iterator)
            except StopIteration as completed:
                return completed.value

    def prepare_events(
        self,
        messages: list[dict[str, Any]],
        toolbox: ActToolbox,
        *,
        enabled: bool,
    ) -> Generator[dict[str, Any], None, ActPreparation]:
        working = [dict(message) for message in messages]
        trace: dict[str, Any] = {
            "version": "act-v1",
            "enabled": bool(enabled and self.supported),
            "max_rounds": self.max_rounds,
            "max_tool_calls": self.max_tool_calls,
            "steps": [],
            "activities": [],
            "tool_call_count": 0,
            "termination": "direct",
        }
        if not enabled or not self.supported:
            activity = {
                "id": "finalizing",
                "stage": "finalizing",
                "status": "running",
                "label": "正在组织回答",
                "detail": "根据当前问题与对话内容形成回答。",
            }
            trace["activities"].append(activity)
            yield dict(activity)
            return ActPreparation(working, trace)

        tool_call_count = 0
        for round_index in range(1, self.max_rounds + 1):
            planning = {
                "id": f"planning-{round_index}",
                "stage": "planning",
                "status": "running",
                "label": "正在判断下一步",
                "detail": "根据当前问题、材料和已有结果决定是否需要调用工具。",
            }
            trace["activities"].append(planning)
            yield dict(planning)
            decision = self.model.decide(working, toolbox.definitions)
            step = {
                "round": round_index,
                "requested_tools": [call.name for call in decision.tool_calls],
                "calls": [
                    {
                        "call_id": call.id,
                        "tool": call.name,
                        "arguments": call.arguments,
                    }
                    for call in decision.tool_calls
                ],
                "observations": [],
            }
            trace["steps"].append(step)
            if not decision.tool_calls:
                planning.update({
                    "status": "completed",
                    "label": "已确定回答路径",
                    "detail": "当前信息足以形成回答。",
                })
                yield dict(planning)
                trace["termination"] = "model_ready"
                break
            planning.update({
                "status": "completed",
                "label": "已确定需要的工具",
                "detail": f"准备执行 {len(decision.tool_calls)} 项操作。",
            })
            yield dict(planning)
            working.append(decision.assistant_message())
            for call in decision.tool_calls:
                running = tool_activity(call.name, call.id, call.arguments, "running")
                trace["activities"].append(running)
                yield dict(running)
                if tool_call_count >= self.max_tool_calls:
                    from .contracts import ToolObservation

                    observation = ToolObservation(
                        call.id,
                        call.name,
                        "refused",
                        '{"error":"本轮工具调用数量已达上限"}',
                        "工具调用达到上限",
                    )
                else:
                    observation = toolbox.execute(call)
                    tool_call_count += 1
                working.append(observation.tool_message())
                step["observations"].append(observation.trace_dict())
                completed = tool_activity(
                    call.name,
                    call.id,
                    call.arguments,
                    "completed" if observation.status == "completed" else "failed",
                    observation.summary,
                )
                trace["activities"][-1] = completed
                yield dict(completed)
            trace["tool_call_count"] = tool_call_count
        else:
            trace["termination"] = "round_limit"
        working.append(FINAL_INSTRUCTION)
        finalizing = {
            "id": "finalizing",
            "stage": "finalizing",
            "status": "running",
            "label": "正在组织回答",
            "detail": "正在把问题、材料依据与工具结果整理成可读答案。",
        }
        trace["activities"].append(finalizing)
        yield dict(finalizing)
        return ActPreparation(working, trace)


_TOOL_TEXT = {
    "case_search": ("正在查找案件材料", "已查找案件材料"),
    "resource_search": ("正在检索资源库", "已检索资源库"),
    "web_search": ("正在搜索公开资料", "已搜索公开资料"),
    "python_run": ("正在运行计算", "已完成计算"),
}


def tool_activity(
    tool_name: str,
    call_id: str,
    arguments: dict[str, Any],
    status: str,
    summary: str | None = None,
) -> dict[str, Any]:
    running_label, completed_label = _TOOL_TEXT.get(
        tool_name, ("正在使用工具", "工具执行完成")
    )
    purpose = str(arguments.get("purpose") or arguments.get("query") or "").strip()
    if status == "running":
        detail = purpose[:160] or "正在执行本轮需要的操作。"
        label = running_label
    elif status == "failed":
        detail = (summary or "这项操作没有完成，系统会使用已有信息继续处理。").strip()
        label = running_label.replace("正在", "未能")
    else:
        detail = (summary or purpose or "操作已完成。").strip()
        label = completed_label
    return {
        "id": call_id,
        "stage": "tool",
        "status": status,
        "tool": tool_name,
        "label": label,
        "detail": detail[:220],
    }


def finish_activity(trace: dict[str, Any] | None, *, failed: bool = False) -> None:
    if not trace:
        return
    for activity in reversed(trace.get("activities", [])):
        if activity.get("id") == "finalizing":
            activity.update({
                "status": "failed" if failed else "completed",
                "label": "回答未完成" if failed else "回答已完成",
                "detail": (
                    "生成过程发生中断，可以直接重试。"
                    if failed else "已结合本轮可用信息形成回答。"
                ),
            })
            return


def render_act_trace(trace: dict[str, Any]) -> str:
    if not trace.get("enabled"):
        return "ACT 本轮未启动工具，模型根据问题与已组织上下文直接回答。"
    names = {
        "case_search": "案件材料检索",
        "resource_search": "资源库检索",
        "web_search": "外部搜索",
        "python_run": "隔离 Python",
    }
    lines = [
        f"ACT 本轮共执行 {trace.get('tool_call_count', 0)} 次工具调用。"
    ]
    for step in trace.get("steps", []):
        calls = step.get("calls", [])
        observations = step.get("observations", [])
        if not calls:
            lines.append(f"第 {step.get('round')} 轮：模型确认已可以形成最终回答。")
            continue
        lines.append(
            f"第 {step.get('round')} 轮：请求 "
            + "、".join(names.get(call.get("tool"), call.get("tool", "未知工具")) for call in calls)
            + "。"
        )
        for observation in observations:
            lines.append(
                f"- {names.get(observation.get('tool'), observation.get('tool'))}："
                f"{observation.get('summary')}；状态 {observation.get('status')}，"
                f"用时 {observation.get('duration_ms', 0)} 毫秒。"
            )
    termination = {
        "model_ready": "模型确认信息已足够，进入最终回答。",
        "round_limit": "已达行动轮次上限，使用已有结果形成回答。",
        "direct": "本轮直接回答。",
    }
    lines.append(termination.get(trace.get("termination"), "ACT 行动阶段结束。"))
    return "\n".join(lines)

