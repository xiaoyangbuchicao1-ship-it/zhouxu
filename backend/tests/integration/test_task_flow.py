"""Contract tests for committed stages, durable streams and restart ownership."""
import asyncio
import json
import time

import httpx
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.contracts.events.streaming import StreamEvent, visible_text
from backend.contracts.harness.lifecycle import (
    PersistedSession,
    RunEnvelope,
    SessionSnapshot,
)
from backend.contracts.modules.loop import RunInput, RunResult
from backend.harness.lifecycle.executor import HarnessRunExecutor
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.persistence.run_store import SQLiteRunStore
from backend.tests.integration.test_explicit_decisions import (
    explicit_settings,
    stream_message,
)
from backend.tests.integration.test_workspace_api import client_for


async def test_stage_is_committed_and_automatically_continues_same_run():
    settings, events = explicit_settings(), MemoryEventSink()
    settings.runtime.max_rounds = 1  # exercise yielding across executor invocations
    packets = []

    def respond(request):
        packets.append(json.loads(request.content))
        if len(packets) == 1:
            value = {"text": "第一部分：已核对原件。", "complete": False, "remaining_work": ["整理对照结果"]}
        else:
            assert "整理对照结果" in json.dumps(packets[-1], ensure_ascii=False)
            assert packets[-1]["messages"][-1]["name"] == "task_continuation_state"
            assert "第一部分：已核对原件。" in packets[-1]["messages"][-1]["content"]
            value = {"text": "第二部分：差异共两项。", "complete": True}
        return stream_message(settings.model.delivery_tool, json.dumps(value, ensure_ascii=False))

    async with DeepSeekModel(settings, SecretStr("offline"), events, transport=httpx.MockTransport(respond)) as model:
        agent = build_harness(model=model, settings=settings, events=events, streams=events)
        scheduler = InMemoryRunScheduler(HarnessRunExecutor(agent))
        task = await scheduler.submit(session_id="stages", payload=RunInput(text="核对材料并整理对照结果"))
        await scheduler.wait_for_idle(session_id="stages")
        run = await scheduler.get_run(session_id="stages", run_id=task.run_id)
    assert run.status == "completed"
    assert len(packets) == 2
    assert run.output == "第一部分：已核对原件。\n\n第二部分：差异共两项。"
    assert visible_text(events.stream_events) == run.output
    assert run.execution_state["delivered_stages"] == ["第一部分：已核对原件。"]


async def test_durable_events_resume_exactly_once_after_store_recreation(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = SQLiteRunStore(path)
    first = store.append_event(StreamEvent(run_id="r", type="delta", text="阶段一"))
    store = SQLiteRunStore(path)
    second = store.append_event(StreamEvent(run_id="r", type="delta", text="完成"))
    assert second.sequence == first.sequence + 1
    assert [e.text for e in store.stream_events_since(run_id="r", after_sequence=first.sequence)] == ["完成"]
    assert store.visible("r") == ("阶段一完成", second.sequence)


async def test_startup_recovers_active_but_not_manually_stopped_tasks(tmp_path):
    store = SQLiteRunStore(tmp_path / "runtime.sqlite3")
    runs = [RunEnvelope(run_id=name, session_id="c", payload=RunInput(text=name), status=status, execution_state={"delivered_stages": ["已保存"]}) for name, status in [("active", "running"), ("manual", "stopped")]]
    await store.save(PersistedSession(snapshot=SessionSnapshot(session_id="c", active_run_id="active", runs=runs)))
    calls = []

    class Executor:
        async def execute(self, run, control):
            calls.append(run.run_id)
            assert run.execution_state["delivered_stages"] == ["已保存"]
            return RunResult(run_id=run.run_id, status="completed", answer="续做完成", elapsed_seconds=0)

    scheduler = InMemoryRunScheduler(Executor(), store)
    await scheduler.recover()  # no browser read triggers recovery
    await scheduler.recover()  # startup/reconnect is idempotent
    await scheduler.wait_for_idle(session_id="c")
    assert calls == ["active"]
    assert (await scheduler.get_run(session_id="c", run_id="manual")).status == "stopped"


async def test_continue_preserves_checkpoint_instead_of_resubmitting(tmp_path):
    store = SQLiteRunStore(tmp_path / "runtime.sqlite3")
    run = RunEnvelope(run_id="same", session_id="c", payload=RunInput(text="工作"), status="stopped", execution_state={"marker": "已完成步骤"})
    await store.save(PersistedSession(snapshot=SessionSnapshot(session_id="c", runs=[run])))

    class Executor:
        async def execute(self, run, control):
            assert run.execution_state == {"marker": "已完成步骤"}
            await asyncio.sleep(0)
            return RunResult(run_id=run.run_id, status="completed", answer="完成剩余部分", elapsed_seconds=0)

    scheduler = InMemoryRunScheduler(Executor(), store)
    a = await scheduler.continue_run(session_id="c", run_id="same")
    b = await scheduler.continue_run(session_id="c", run_id="same")
    await scheduler.wait_for_idle(session_id="c")
    assert a.run_id == b.run_id == "same"
    assert len((await scheduler.snapshot(session_id="c")).runs) == 1

def test_message_attachments_scope_and_background_history_are_distinct(tmp_path):
    client, database = client_for(tmp_path)
    try:
        with client:
            auth = client.post('/auth/register', json={'email': 'attachments@example.com', 'password': 'long-password-12'}).json()
            headers = {'Authorization': f"Bearer {auth['session']['token']}"}
            c = client.post('/workspace/conversations', headers=headers, json={}).json()['conversation']['conversation_id']
            ids = []
            for index in range(2):
                item = client.post(f'/workspace/conversations/{c}/materials', headers=headers, files={'file': (f'source-{index}.txt', b'material text', 'text/plain')}).json()['material']
                ids.append(item['material_id'])
                submitted = client.post(f'/workspace/conversations/{c}/runs', headers=headers, json={'conversation_id': c, 'client_request_id': str(index), 'text': f'task {index}', 'attached_material_ids': [ids[-1]]})
                assert submitted.status_code == 202
                assert set(submitted.json()['run']['payload']['material_ids']) == set(ids)
                for _ in range(100):
                    detail = client.get(f'/workspace/conversations/{c}', headers=headers).json()
                    if len([m for m in detail['messages'] if m['role'] == 'assistant']) == index + 1:
                        break
                    time.sleep(0.005)
                else:
                    raise AssertionError('background result was not persisted')
            user_messages = [m for m in detail['messages'] if m['role'] == 'user']
            assert [[f['material_id'] for f in m['materials']] for m in user_messages] == [[ids[0]], [ids[1]]]
            assert len(detail['materials']) == 2
            second = client.post('/auth/register', json={'email': 'other-attachments@example.com', 'password': 'long-password-12'}).json()
            other_headers = {'Authorization': f"Bearer {second['session']['token']}"}
            other_c = client.post('/workspace/conversations', headers=other_headers, json={}).json()['conversation']['conversation_id']
            denied = client.post(f'/workspace/conversations/{other_c}/runs', headers=other_headers, json={'conversation_id': other_c, 'client_request_id': 'cross-owner', 'text': 'read', 'attached_material_ids': ids})
            assert denied.status_code == 404
    finally:
        database.close()
