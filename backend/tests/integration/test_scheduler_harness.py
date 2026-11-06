from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.model_context import DeploymentLimits
from backend.contracts.modules.loop import ActionRequest, DecisionProposal, RunInput
from backend.contracts.modules.materials import MaterialInput
from backend.harness.lifecycle.executor import HarnessRunExecutor
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.support import ScriptedModel


def build_scheduler(*replies):
    events = MemoryEventSink()
    harness = build_harness(
        model=ScriptedModel(*replies),
        settings=load_settings(),
        events=events,
    )
    executor = HarnessRunExecutor(harness)
    return InMemoryRunScheduler(executor), executor, events


async def test_scheduler_resumes_a_real_harness_run_after_needs_input():
    model = ScriptedModel(
        DecisionProposal(kind="needs_input", missing_inputs=["请提供页码"]),
        DecisionProposal(kind="answer", answer="已读取第 2 页"),
    )
    events = MemoryEventSink()
    harness = build_harness(model=model, settings=load_settings(), events=events)
    scheduler = InMemoryRunScheduler(HarnessRunExecutor(harness))

    submitted = await scheduler.submit(
        session_id="session-1", payload=RunInput(text="读取事故报告")
    )
    await scheduler.wait_for_idle(session_id="session-1")
    waiting = await scheduler.get_run(
        session_id="session-1", run_id=submitted.run_id
    )
    assert waiting.status == "waiting_user"
    assert len(waiting.pending_request_ids) == 1

    await scheduler.resume(
        session_id="session-1",
        run_id=submitted.run_id,
        request_id=waiting.pending_request_ids[0],
        answer="第 2 页",
    )
    await scheduler.wait_for_idle(session_id="session-1")
    completed = await scheduler.get_run(
        session_id="session-1", run_id=submitted.run_id
    )
    assert completed.status == "completed"
    assert completed.output == "已读取第 2 页"
    assert [request.run_id for request in model.requests] == [
        submitted.run_id,
        submitted.run_id,
    ]
    assert model.requests[1].input.perceived.facts["user_answers"]


async def test_waiting_material_run_and_independent_question_use_separate_runs():
    model = ScriptedModel(
        DecisionProposal(kind="needs_input", missing_inputs=["请提供页码"]),
        DecisionProposal(kind="answer", answer="独立问题已回答"),
        DecisionProposal(kind="answer", answer="材料问题已完成"),
    )
    events = MemoryEventSink()
    harness = build_harness(model=model, settings=load_settings(), events=events)
    scheduler = InMemoryRunScheduler(HarnessRunExecutor(harness))

    first = await scheduler.submit(
        session_id="session-2", payload=RunInput(text="读取事故报告")
    )
    await scheduler.wait_for_idle(session_id="session-2")
    first_waiting = await scheduler.get_run(session_id="session-2", run_id=first.run_id)

    second = await scheduler.submit(
        session_id="session-2", payload=RunInput(text="什么是共同海损")
    )
    await scheduler.wait_for_idle(session_id="session-2")
    second_done = await scheduler.get_run(session_id="session-2", run_id=second.run_id)
    assert second_done.status == "completed"
    assert second_done.output == "独立问题已回答"

    await scheduler.resume(
        session_id="session-2",
        run_id=first.run_id,
        request_id=first_waiting.pending_request_ids[0],
        answer="第 2 页",
    )
    await scheduler.wait_for_idle(session_id="session-2")
    first_done = await scheduler.get_run(session_id="session-2", run_id=first.run_id)
    assert first_done.status == "completed"
    assert first_done.output == "材料问题已完成"
    assert [request.run_id for request in model.requests] == [
        first.run_id,
        second.run_id,
        first.run_id,
    ]


async def test_scheduler_preserves_material_read_chain_through_real_harness(tmp_path):
    path = tmp_path / "case.txt"
    path.write_text("普通记录。" * 25000 + "承运人应当妥善保管货物。", encoding="utf-8")
    settings = load_settings()
    settings.deployment_limits[settings.model.name] = DeploymentLimits(total_tokens=64000)
    settings.materials.cache_root = str(tmp_path / "cache")
    material_id = "case"
    model = ScriptedModel(
        DecisionProposal(
            kind="action",
            action_requests=[
                ActionRequest(
                    tool_name="read_material",
                    arguments={
                        "material_id": material_id,
                        "mode": "read",
                        "position": "end",
                    },
                )
            ],
        ),
        DecisionProposal(kind="answer", answer="材料已读取"),
        settings=settings,
    )
    events = MemoryEventSink()
    harness = build_harness(model=model, settings=settings, events=events)
    scheduler = InMemoryRunScheduler(HarnessRunExecutor(harness))

    submitted = await scheduler.submit(
        session_id="session-material",
        payload=RunInput(
            text="请读取附件",
            materials=[MaterialInput(uri=str(path), material_id=material_id)],
        ),
    )
    await scheduler.wait_for_idle(session_id="session-material")
    completed = await scheduler.get_run(
        session_id="session-material", run_id=submitted.run_id
    )
    assert completed.status == "completed" and completed.output == "材料已读取"
    assert len(model.requests) == 2
    assert any(tool.name == "read_material" for tool in model.requests[0].input.tools)
    assert any(
        item.type == "tool_result" and "承运人" in str(item.body)
        for item in model.requests[1].input.perceived.items
    )

