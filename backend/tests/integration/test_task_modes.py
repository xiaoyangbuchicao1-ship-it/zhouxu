import pytest

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import ActionRequest, DecisionProposal, RunInput
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.execution.runtime import (
    DockerRuntime,
    ExecutionStore,
    execution_tools,
)
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.support import ScriptedModel


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "chat", "work"])
async def test_mode_controls_model_tool_inventory_without_starting_environment(
    tmp_path, mode
):
    runtime = DockerRuntime(store=ExecutionStore(tmp_path))
    settings = load_settings()
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer="直接回答"), settings=settings
    )
    harness = build_harness(
        model=model,
        settings=settings,
        events=MemoryEventSink(),
        tools=execution_tools(runtime),
    )
    result = await harness.run(
        RunInput(text="一个简短问题", facts={} if mode is None else {"task_mode": mode})
    )
    assert result.status == "completed"
    exposed = {t.name for t in model.requests[0].input.tools}
    assert ("bash" in exposed) == (mode == "work")
    assert ("write" in exposed) == (mode == "work")
    assert not runtime.worlds


@pytest.mark.asyncio
async def test_chat_cannot_call_hidden_execution_tool_even_by_name(tmp_path):
    runtime = DockerRuntime(store=ExecutionStore(tmp_path))
    tool = execution_tools(runtime)["bash"][1]
    for facts in ({}, {"task_mode": "chat"}, {"task_mode": "invalid"}):
        with pytest.raises(PortError) as exc:
            await tool.execute_context(
                ModuleRequest(
                    run_id="chat",
                    call_id="test",
                    input=ActionRequest(
                        tool_name="bash", arguments={"command": "echo should-not-run"}
                    ),
                    context={"user_facts": facts},
                )
            )
        assert exc.value.error.code == "execution_not_authorized"
    assert not runtime.worlds and not tmp_path.joinpath("files.sqlite3").exists()


def test_http_mode_is_validated_and_saved_on_run(tmp_path):
    from backend.tests.integration.test_workspace_api import client_for

    client, database = client_for(tmp_path)
    try:
        with client:
            account = client.post(
                "/auth/register",
                json={"email": "modes@example.test", "password": "long-password-12"},
            ).json()
            headers = {"Authorization": "Bearer " + account["session"]["token"]}
            chat = client.post(
                "/workspace/conversations", json={}, headers=headers
            ).json()["conversation"]
            chat_id = chat["conversation_id"]
            assert chat["task_mode"] == "chat"
            response = client.post(
                f"/workspace/conversations/{chat_id}/runs",
                json={
                    "conversation_id": chat_id,
                    "client_request_id": "chat-1",
                    "text": "mode test",
                    "facts": {},
                },
                headers=headers,
            )
            assert response.status_code == 202
            assert response.json()["run"]["payload"]["facts"]["task_mode"] == "chat"

            mismatch = client.post(
                f"/workspace/conversations/{chat_id}/runs",
                json={
                    "conversation_id": chat_id,
                    "client_request_id": "chat-work-mismatch",
                    "text": "mode test",
                    "facts": {"task_mode": "work"},
                },
                headers=headers,
            )
            assert mismatch.status_code == 409
            assert mismatch.json()["detail"] == "conversation_mode_locked"

            invalid = client.post(
                f"/workspace/conversations/{chat_id}/runs",
                json={
                    "conversation_id": chat_id,
                    "client_request_id": "chat-invalid",
                    "text": "mode test",
                    "facts": {"task_mode": "invalid"},
                },
                headers=headers,
            )
            assert invalid.status_code == 422

            work = client.post(
                "/workspace/conversations",
                json={"task_mode": "work"},
                headers=headers,
            ).json()["conversation"]
            work_id = work["conversation_id"]
            assert work["task_mode"] == "work"
            response = client.post(
                f"/workspace/conversations/{work_id}/runs",
                json={
                    "conversation_id": work_id,
                    "client_request_id": "work-1",
                    "text": "mode test",
                    "facts": {"task_mode": "work"},
                },
                headers=headers,
            )
            assert response.status_code == 202
            assert response.json()["run"]["payload"]["facts"]["task_mode"] == "work"
    finally:
        database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("original,incoming", [("chat", "work"), ("work", "chat")])
async def test_steering_does_not_change_existing_run_execution_permission(
    original, incoming
):
    import asyncio

    from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
    from backend.tests.unit.test_scheduler import ScriptedExecutor

    executor = ScriptedExecutor()
    executor.block_first = True
    scheduler = InMemoryRunScheduler(executor)
    first = await scheduler.submit(
        session_id="mode-session",
        payload=RunInput(text="original", facts={"task_mode": original}),
    )
    await asyncio.wait_for(executor.started.wait(), 1)
    await scheduler.steer(
        session_id="mode-session",
        run_id=first.run_id,
        payload=RunInput(text="steer", facts={"task_mode": incoming}),
    )
    executor.release.set()
    await scheduler.wait_for_idle(session_id="mode-session")
    result = await scheduler.get_run(session_id="mode-session", run_id=first.run_id)
    assert result.payload.facts["task_mode"] == original
