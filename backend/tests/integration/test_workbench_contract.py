import pytest
from fastapi.testclient import TestClient

from backend.application.accounts.service import AccountService
from backend.application.workspace.materials import MaterialService
from backend.config.settings import load_settings
from backend.contracts.events.streaming import StreamEvent
from backend.contracts.modules.loop import RunInput, RunResult
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
from backend.infrastructure.database.connection import Database
from backend.infrastructure.persistence.run_store import JsonRunStore
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import HarnessControlService


class CompletedExecutor:
    async def execute(self, run, control):
        return RunResult(run_id=run.run_id, status="completed", answer="ok:" + run.payload.text, elapsed_seconds=0)


class PartialExecutor:
    async def execute(self, run, control):
        from backend.contracts.schemas.core import Error
        return RunResult(
            run_id=run.run_id,
            status="failed",
            answer="已生成部分",
            error=Error(code="round_limit", message="已到达本次运行的循环上限。"),
            completed_units=6,
            total_units=6,
            current_unit="round:6",
            elapsed_seconds=0,
        )


def make_client(tmp_path):
    settings = load_settings().model_copy(update={
        "database": load_settings().database.model_copy(update={"url": f"sqlite:///{(tmp_path / 'workbench.db').as_posix()}"}),
        "materials": load_settings().materials.model_copy(update={"storage_root": str(tmp_path / "files")}),
    })
    database = Database(settings.database)
    database.initialize()
    accounts = AccountService(database, settings.database, settings.auth)
    materials = MaterialService(database, settings)
    app = create_app(control_service=HarnessControlService(CompletedExecutor()), account_service=accounts, material_service=materials)
    return TestClient(app), database


def register(client):
    auth = client.post("/auth/register", json={"email": "contract@example.com", "password": "long-password-12"}).json()
    return {"Authorization": f"Bearer {auth['session']['token']}"}


def test_workbench_submission_is_idempotent_and_retry_is_related(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            headers = register(client)
            cid = client.post("/workspace/conversations", headers=headers, json={}).json()["conversation"]["conversation_id"]
            body = {"conversation_id": cid, "client_request_id": "request-1", "text": "one", "facts": {}, "constraints": [], "material_ids": []}
            first = client.post(f"/workspace/conversations/{cid}/runs", headers=headers, json=body)
            duplicate = client.post(f"/workspace/conversations/{cid}/runs", headers=headers, json=body)
            assert first.status_code == duplicate.status_code == 202
            assert first.json()["run"]["run_id"] == duplicate.json()["run"]["run_id"]
            retry = client.post(f"/workspace/conversations/{cid}/runs", headers=headers, json={**body, "client_request_id": "request-2", "text": "retry", "retry_of_run_id": first.json()["run"]["run_id"]})
            assert retry.status_code == 202
            assert retry.json()["run"]["run_id"] != first.json()["run"]["run_id"]
            assert retry.json()["run"]["retry_of_run_id"] == first.json()["run"]["run_id"]
    finally:
        database.close()


def test_workbench_event_cursor_and_partial_history(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            headers = register(client)
            cid = client.post("/workspace/conversations", headers=headers, json={}).json()["conversation"]["conversation_id"]
            body = {"conversation_id": cid, "client_request_id": "request-1", "text": "one", "facts": {}, "constraints": [], "material_ids": []}
            submitted = client.post(f"/workspace/conversations/{cid}/runs", headers=headers, json=body).json()
            rid = submitted["run"]["run_id"]
            client.app.state.event_hub.emit_stream(StreamEvent(run_id=rid, type="delta", text="一"))
            client.app.state.event_hub.emit_stream(StreamEvent(run_id=rid, type="delta", text="二"))
            first = client.get(f"/workspace/conversations/{cid}/events", headers=headers, params={"run_id": rid, "after_sequence": 0})
            second = client.get(f"/workspace/conversations/{cid}/events", headers=headers, params={"run_id": rid, "after_sequence": 1})
            assert '"sequence":1' in first.text and '"sequence":2' in first.text
            assert '"sequence":1' not in second.text and '"sequence":2' in second.text
    finally:
        database.close()


@pytest.mark.asyncio
async def test_failed_run_persists_partial_output_across_store_reload(tmp_path):
    path = tmp_path / "runs.json"
    first = InMemoryRunScheduler(PartialExecutor(), JsonRunStore(path))
    dispatch = await first.submit(session_id="conversation", account_id="account", payload=RunInput(text="x"), client_request_id="req")
    await first.wait_for_idle(session_id="conversation")
    saved = await first.get_run(session_id="conversation", run_id=dispatch.run_id)
    assert saved.status == "failed"
    assert saved.partial_output == "已生成部分"
    assert saved.error is not None and saved.error.code == "round_limit"
    assert saved.completed_units == 6
    assert saved.total_units == 6
    assert saved.current_unit == "round:6"
    second = InMemoryRunScheduler(PartialExecutor(), JsonRunStore(path))
    restored = await second.get_run(session_id="conversation", run_id=dispatch.run_id)
    assert restored.partial_output == "已生成部分"
