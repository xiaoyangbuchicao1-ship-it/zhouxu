import asyncio
from uuid import uuid4

import pytest

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import (
    ActionRequest,
    ActionResult,
    DecisionProposal,
    FeedbackRecord,
    MemoryContext,
    MemoryInput,
    PerceptionInput,
    ReflectionResult,
    RunInput,
)
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse, PortError
from backend.harness.lifecycle.control import RunControl
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.modules.memory.service import InMemoryMemory
from backend.modules.perception.service import TextPerception
from backend.tests.support import FixtureLookup, ScriptedModel, context_model


def setup(model, *, settings=None, tool=None, events=None):
    sink = events or MemoryEventSink()
    harness = build_harness(
        model=model,
        settings=settings or load_settings(),
        events=sink,
        model_context=context_model(settings or load_settings()),
        tools={tool.spec.name: (tool.spec, tool)} if tool else None,
    )
    return harness, sink


async def test_tool_result_reaches_next_model_call_and_memory_is_released():
    receipt = uuid4().hex
    tool = FixtureLookup(receipt)
    model = ScriptedModel(
        DecisionProposal(
            kind="action",
            action_requests=[
                ActionRequest(
                    tool_name=tool.spec.name, arguments={"record_id": "sample"}
                )
            ],
        ),
        DecisionProposal(kind="answer", answer=receipt),
    )
    harness, events = setup(model, tool=tool)
    result = await harness.run(RunInput(text="读取测试记录"))
    assert result.status == "completed" and result.answer == receipt
    assert len(tool.calls) == 1 and len(model.requests) == 2
    feedback = model.requests[1].input.perceived.records
    assert model.requests[0].input.perceived.records == []
    assert not any(item.source == "user" for item in feedback)
    assert model.requests[1].input.perceived.facts == {}
    assert any(
        item.source == "action"
        and item.updates["action"]["result"]["receipt"] == receipt
        for item in feedback
    )
    assert any(item.source == "reflection" for item in feedback)
    calls = [e for e in events.events if e.phase == "started" and e.call_id]
    assert len({e.call_id for e in calls}) == len(calls)
    assert {e.run_id for e in events.events} == {result.run_id}
    recalled = await harness.modules["memory"].handle(
        ModuleRequest(
            run_id=result.run_id, call_id="check", input=MemoryInput(operation="recall")
        )
    )
    assert recalled.output.items == []


async def test_direct_answer_does_not_force_a_tool_call():
    harness, sink = setup(
        ScriptedModel(DecisionProposal(kind="answer", answer="直接回答"))
    )
    assert (await harness.run(RunInput(text="你好"))).status == "completed"
    assert not any(e.module == "action" for e in sink.events)


async def test_missing_information_is_not_fabricated():
    harness, _ = setup(
        ScriptedModel(
            DecisionProposal(kind="needs_input", missing_inputs=["请提供材料"])
        )
    )
    result = await harness.run(RunInput(text="分析材料"))
    assert result.status == "needs_input" and result.missing_inputs == ["请提供材料"]


async def test_empty_input_does_not_spend_a_model_call():
    model = ScriptedModel()
    harness, _ = setup(model)
    assert (await harness.run(RunInput(text="  "))).status == "needs_input"
    assert model.requests == []


async def test_unknown_tool_returns_an_error_receipt_without_execution():
    tool = FixtureLookup("unused")
    model = ScriptedModel(
        DecisionProposal(
            kind="action",
            action_requests=[ActionRequest(tool_name="not_registered", arguments={})],
        ),
        DecisionProposal(kind="answer", answer="该工具不可用，未执行。"),
    )
    harness, _ = setup(model, tool=tool)
    result = await harness.run(RunInput(text="test"))
    assert result.status == "completed"
    receipt = next(
        i for i in model.requests[1].input.perceived.items if i.type == "tool_result"
    )
    assert (
        receipt.body["outcome"] == "error"
        and receipt.body["error"]["code"] == "unknown_tool"
    )
    assert tool.calls == []


async def test_invalid_tool_arguments_can_be_corrected_before_execution():
    tool = FixtureLookup("unused")
    model = ScriptedModel(
        DecisionProposal(
            kind="action",
            action_requests=[ActionRequest(tool_name=tool.spec.name, arguments={})],
        ),
        DecisionProposal(
            kind="action",
            action_requests=[
                ActionRequest(
                    tool_name=tool.spec.name, arguments={"record_id": "sample"}
                )
            ],
        ),
        DecisionProposal(kind="answer", answer="unused"),
    )
    harness, _ = setup(
        model,
        tool=tool,
    )
    result = await harness.run(RunInput(text="test"))
    assert result.status == "completed" and len(tool.calls) == 1
    receipt = next(
        i for i in model.requests[1].input.perceived.items if i.type == "tool_result"
    )
    assert (
        receipt.body["outcome"] == "error"
        and receipt.body["error"]["code"] == "invalid_arguments"
    )


async def test_transient_model_failure_retries_with_new_call_id():
    settings = load_settings()
    model = ScriptedModel(
        PortError(settings.error("model_unavailable", retryable=True)),
        DecisionProposal(kind="answer", answer="recovered"),
    )
    harness, _ = setup(model)
    assert (await harness.run(RunInput(text="test"))).answer == "recovered"
    assert model.requests[0].call_id != model.requests[1].call_id


async def test_retry_budget_is_finite():
    error = PortError(load_settings().error("model_unavailable", retryable=True))
    model = ScriptedModel(error, error)
    harness, _ = setup(model)
    result = await harness.run(RunInput(text="test"))
    assert result.error.code == "model_unavailable" and len(model.requests) == 2


async def test_no_automatic_retry_of_failed_tool():
    class FailingTool(FixtureLookup):
        async def execute(self, request):
            self.calls.append(request)
            raise PortError(load_settings().error("tool_failed", retryable=True))

    tool = FailingTool("unused")
    harness, _ = setup(
        ScriptedModel(
            DecisionProposal(
                kind="action",
                action_requests=[
                    ActionRequest(
                        tool_name=tool.spec.name, arguments={"record_id": "sample"}
                    )
                ],
            )
        ),
        tool=tool,
    )
    result = await harness.run(RunInput(text="test"))
    assert result.status == "failed" and len(tool.calls) == 1


async def test_cancel_before_run_does_not_call_model():
    model = ScriptedModel()
    harness, _ = setup(model)
    control = RunControl()
    control.cancel()
    assert (await harness.run(RunInput(text="test"), control)).status == "cancelled"
    assert model.requests == []


async def test_cancel_during_model_does_not_start_requested_tool():
    started, release = asyncio.Event(), asyncio.Event()
    tool = FixtureLookup("unused")

    class WaitingModel:
        async def decide(self, request):
            started.set()
            await release.wait()
            return DecisionProposal(
                kind="action",
                action_requests=[
                    ActionRequest(
                        tool_name=tool.spec.name, arguments={"record_id": "sample"}
                    )
                ],
            )

    harness, _ = setup(WaitingModel(), tool=tool)
    control = RunControl()
    task = asyncio.create_task(harness.run(RunInput(text="test"), control))
    await asyncio.wait_for(started.wait(), 1)
    control.cancel()
    assert (
        not task.done()
    )  # Cooperative stop must not claim an in-flight call vanished.
    release.set()
    assert (await task).status == "cancelled" and tool.calls == []


async def test_timeout_is_not_a_success():
    class SlowModel:
        async def decide(self, request):
            await asyncio.Event().wait()

    settings = load_settings()
    settings.runtime.call_timeout_seconds = 0.02
    settings.runtime.model_call_timeout_seconds = 0.02
    harness, _ = setup(SlowModel(), settings=settings)
    result = await harness.run(RunInput(text="test"))
    assert result.status == "failed" and result.error.code == "timeout"


async def test_model_generation_has_separate_total_timeout():
    class GeneratingModel:
        async def decide(self, request):
            await asyncio.sleep(0.04)
            return DecisionProposal(kind="answer", answer="完整结果")

    settings = load_settings()
    settings.runtime.call_timeout_seconds = 0.02
    settings.runtime.model_call_timeout_seconds = 0.2
    harness, _ = setup(GeneratingModel(), settings=settings)
    result = await harness.run(RunInput(text="工作"))
    assert result.status == "completed" and result.answer == "完整结果"


async def test_optional_telemetry_failure_does_not_break_answer():
    class BrokenSink:
        def emit(self, event):
            raise OSError("offline")

    harness, _ = setup(
        ScriptedModel(DecisionProposal(kind="answer", answer="ok")), events=BrokenSink()
    )
    assert (await harness.run(RunInput(text="test"))).answer == "ok"


async def test_concurrent_runs_do_not_share_memory():
    class EchoTool(FixtureLookup):
        async def execute(self, request):
            return ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                result={"receipt": request.arguments["record_id"]},
            )

    class EchoModel:
        async def decide(self, request):
            own = request.input.perceived.goal
            assert request.input.perceived.run_id == request.run_id
            observations = [
                item.updates["action"]["result"]["receipt"]
                for item in request.input.perceived.records
                if item.source == "action"
            ]
            await asyncio.sleep(0)
            if not observations:
                return DecisionProposal(
                    kind="action",
                    action_requests=[
                        ActionRequest(
                            tool_name="lookup_test_record",
                            arguments={"record_id": own},
                        )
                    ],
                )
            assert observations == [own]
            return DecisionProposal(kind="answer", answer=observations[0])

    harness, _ = setup(EchoModel(), tool=EchoTool("unused"))
    a, b = await asyncio.gather(
        harness.run(RunInput(text="A")), harness.run(RunInput(text="B"))
    )
    assert (a.answer, b.answer) == ("A", "B") and a.run_id != b.run_id


async def test_memory_recall_is_a_copy_and_unknown_optional_fields_are_ignored():
    memory = InMemoryMemory()
    await memory.handle(
        ModuleRequest(
            run_id="one",
            call_id="write",
            input=MemoryInput(
                operation="record",
                items=[FeedbackRecord(source="user", summary="original")],
            ),
        )
    )
    recalled = await memory.handle(
        ModuleRequest(
            run_id="one", call_id="read", input=MemoryInput(operation="recall")
        )
    )
    recalled.output.items[0].summary = "changed"
    fresh = await memory.handle(
        ModuleRequest(
            run_id="one", call_id="read2", input=MemoryInput(operation="recall")
        )
    )
    assert fresh.output.items[0].summary == "original"
    assert (
        ModuleRequest(
            run_id="one", call_id="valid", input={}, future_optional="ok"
        ).input
        == {}
    )


@pytest.mark.parametrize(
    "decision,expected",
    [("retry", "completed"), ("fail", "failed"), ("continue", "failed")],
)
async def test_reflection_controls_revision_and_round_limit(decision, expected):
    class Review:
        def __init__(self):
            self.count = 0

        async def handle(self, request):
            self.count += 1
            return ModuleResponse(
                status="ok",
                output=ReflectionResult(
                    decision=decision if self.count == 1 else "complete"
                ),
            )

    settings = load_settings()
    if decision == "continue":
        settings.runtime.max_rounds = 1
    harness, _ = setup(
        ScriptedModel(
            DecisionProposal(kind="answer", answer="draft"),
            DecisionProposal(kind="answer", answer="revised"),
        ),
        settings=settings,
    )
    harness.modules["reflection"] = Review()
    result = await harness.run(RunInput(text="test"))
    assert result.status == expected
    if decision == "retry":
        assert result.answer == "revised"
    if decision == "continue":
        assert result.error.code == "round_limit"


def perception_request(items, *, scope="test"):
    return ModuleRequest(
        run_id="test",
        call_id="perception",
        input=PerceptionInput(
            current=RunInput(
                text="read sample",
                facts={"declared": "user-value"},
                constraints=["read only"],
            ),
            memory=MemoryContext(scope=scope, items=items),
        ),
    )


async def test_perception_deduplicates_by_identity_without_erasing_distinct_feedback():
    first = FeedbackRecord(source="reflection", summary="same note")
    second = FeedbackRecord(source="reflection", summary="same note")
    current = perception_request([]).input.current
    user = FeedbackRecord(source="user", updates=current.model_dump())
    request = perception_request([user, first, first.model_copy(deep=True), second])
    result = await TextPerception(load_settings(), context_model()).handle(request)
    assert result.status == "ok"
    assert [item.record_id for item in result.output.records] == [
        first.record_id,
        second.record_id,
    ]
    assert result.output.goal == current.text
    assert result.output.constraints == ["read only"]
    assert result.output.facts == {"declared": "user-value"}
    result.output.records[0].summary = "changed downstream"
    assert first.summary == "same note"


async def test_perception_rejects_conflicting_identity_and_foreign_scope():
    record = FeedbackRecord(source="reflection", summary="original")
    changed = record.model_copy(update={"summary": "different"})
    perception = TextPerception(load_settings(), context_model())
    conflict = await perception.handle(perception_request([record, changed]))
    assert conflict.status == "failed" and conflict.error.code == "invalid_context"
    foreign = await perception.handle(perception_request([], scope="other-run"))
    assert foreign.status == "failed" and foreign.error.code == "memory_scope"


@pytest.mark.parametrize(
    "problem", ["orphan", "missing", "wrong_id", "wrong_name", "interleaved"]
)
async def test_perception_does_not_send_broken_tool_exchanges_to_model(problem):
    action = ActionRequest(
        tool_name="lookup_test_record", arguments={"record_id": "sample"}
    )
    proposal = FeedbackRecord(
        source="reasoning",
        updates={
            "proposal": DecisionProposal(
                kind="action",
                action_requests=[action],
            ).model_dump()
        },
    )
    result = ActionResult(
        tool_name=action.tool_name,
        action_id=action.action_id,
        result={"receipt": "value"},
    )
    if problem == "wrong_id":
        result.action_id = "unrelated"
    if problem == "wrong_name":
        result.tool_name = "different-tool"
    observation = FeedbackRecord(
        source="action", updates={"action": result.model_dump()}
    )
    records = [proposal, observation]
    if problem == "orphan":
        records = [observation]
    if problem == "missing":
        records = [proposal]
    if problem == "interleaved":
        records = [
            proposal,
            FeedbackRecord(source="reflection", summary="too early"),
            observation,
        ]
    response = await TextPerception(load_settings(), context_model()).handle(
        perception_request(records)
    )
    assert response.status == "failed" and response.error.code == "invalid_context"
