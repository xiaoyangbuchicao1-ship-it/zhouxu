from backend.config.settings import load_settings
from backend.contracts.model_context import DeploymentLimits
from backend.contracts.modules.loop import (
    ActionRequest,
    ActionResult,
    DecisionProposal,
    FeedbackRecord,
    MemoryContext,
    PerceptionInput,
    RunInput,
)
from backend.contracts.modules.perception import ContextItem
from backend.contracts.schemas.core import ModuleRequest
from backend.modules.perception.service import TextPerception
from backend.tests.support import context_model


def request(*records, candidates=None, text="检查资料"):
    return ModuleRequest(
        run_id="run",
        call_id="perception",
        input=PerceptionInput(
            current=RunInput(text=text),
            memory=MemoryContext(scope="run", items=list(records)),
            candidates=candidates or [],
        ),
    )


async def test_context_items_keep_source_role_and_resource_identity():
    resource = ContextItem(
        id="material-a",
        type="resource",
        source="workspace",
        kind="material.entry",
        role="user",
        body={"name": "附件 A", "description": "可按需读取"},
    )
    result = await TextPerception(load_settings(), context_model()).handle(
        request(candidates=[resource])
    )

    assert result.status == "ok"
    assert [item.id for item in result.output.items] == ["current-user", "material-a", "context-inventory"]
    assert result.output.items[1].source == "workspace"
    assert result.output.items[1].type == "resource"


async def test_tool_call_and_result_are_one_ordered_context_unit():
    call = ActionRequest(tool_name="lookup", arguments={"id": "A"}, action_id="call-1")
    proposal = FeedbackRecord(
        source="reasoning",
        updates={
            "proposal": DecisionProposal(
                kind="action", action_requests=[call]
            ).model_dump()
        },
    )
    result = FeedbackRecord(
        source="action",
        updates={
            "action": ActionResult(
                tool_name="lookup", action_id="call-1", result={"ok": True}
            ).model_dump()
        },
    )
    perceived = (
        await TextPerception(load_settings(), context_model()).handle(request(proposal, result))
    ).output

    assert [item.type for item in perceived.items] == [
        "message",
        "tool_call",
        "tool_result",
        "message",  # derived, non-authoritative working-context index
    ]
    assert perceived.items[2].links[0].target_id == proposal.record_id


async def test_budget_pressure_requests_recovery_without_silently_dropping_history():
    settings = load_settings()
    settings.deployment_limits[settings.model.name] = DeploymentLimits(total_tokens=32000)
    old = FeedbackRecord(source="reflection", summary="旧背景 " + "x" * 50000)
    recent = FeedbackRecord(source="reflection", summary="当前结论")

    response = await TextPerception(settings, context_model(settings)).handle(request(old, recent))

    assert response.status == "ok"
    assert response.output.needs_compaction
    assert old.record_id in [item.id for item in response.output.items]
    assert recent.record_id in [item.id for item in response.output.items]
    assert "context_budget_exceeded" in response.output.assembly.notices

