"""Acceptance tests for the first backend contract hardening unit.

These tests intentionally exercise the public Workspace boundary and the
durable lifecycle contracts.  They do not use fake business data or touch the
agent modules.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.application.accounts.service import AccountService
from backend.application.workspace.conversations import WorkspaceService
from backend.application.workspace.materials import MaterialService
from backend.config.settings import load_settings
from backend.contracts.events.streaming import StreamEvent
from backend.contracts.modules.loop import RunInput, RunResult
from backend.harness.lifecycle.scheduler import (
    InMemoryRunScheduler,
    SchedulerCommandError,
)
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import HarnessControlService


class CompletedExecutor:
    async def execute(self, run, control):
        return RunResult(
            run_id=run.run_id,
            status="completed",
            answer=f"完成：{run.payload.text}",
            elapsed_seconds=0,
        )


class NeedsInputExecutor:
    async def execute(self, run, control):
        return RunResult(
            run_id=run.run_id,
            status="needs_input",
            missing_inputs=["请选择适用法域"],
            elapsed_seconds=0,
        )


def make_client(tmp_path):
    settings = load_settings()
    settings = settings.model_copy(
        update={
            "materials": settings.materials.model_copy(
                update={"storage_root": str(tmp_path / "files")}
            ),
            "database": settings.database.model_copy(
                update={"url": f"sqlite:///{(tmp_path / 'contract.db').as_posix()}"}
            ),
        }
    )
    database = Database(settings.database)
    database.initialize()
    accounts = AccountService(database, settings.database, settings.auth)
    materials = MaterialService(database, settings)
    app = create_app(
        control_service=HarnessControlService(CompletedExecutor()),
        account_service=accounts,
        material_service=materials,
    )
    return TestClient(app), database


def auth(client, email):
    result = client.post(
        "/auth/register",
        json={"email": email, "password": "long-password-12"},
    ).json()
    return result, {"Authorization": f"Bearer {result['session']['token']}"}


def test_database_contract_enables_fk_and_real_idempotency_index(tmp_path):
    _, database = make_client(tmp_path)
    try:
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            indexes = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_list('conversation_runs')"
                ).fetchall()
            }
            assert "uq_conversation_runs_idempotency" in indexes
    finally:
        database.close()


def test_legacy_conversation_table_migration_preserves_rows_and_adds_project_fk(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE accounts (
            id VARCHAR(32) PRIMARY KEY, email TEXT, display_name TEXT,
            password_hash TEXT, status TEXT, created_at DATETIME, updated_at DATETIME
        );
        CREATE TABLE projects (
            id VARCHAR(32) PRIMARY KEY, account_id VARCHAR(32), name TEXT,
            created_at DATETIME, updated_at DATETIME
        );
        CREATE TABLE conversations (
            id VARCHAR(32) PRIMARY KEY, account_id VARCHAR(32) NOT NULL,
            title VARCHAR(200) NOT NULL, pinned BOOLEAN NOT NULL,
            archived_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        );
        INSERT INTO accounts VALUES ('account-legacy', 'legacy@example.com', '', 'x', 'active', '2020', '2020');
        INSERT INTO conversations VALUES ('conversation-legacy', 'account-legacy', '保留', 0, NULL, '2020', '2020');
        """
    )
    raw.commit()
    raw.close()
    settings = load_settings()
    database = Database(
        settings.database.model_copy(update={"url": f"sqlite:///{path.as_posix()}"})
    )
    try:
        database.initialize()
        with database.engine.connect() as connection:
            fk_tables = {
                row[2]
                for row in connection.exec_driver_sql(
                    "PRAGMA foreign_key_list('conversations')"
                ).fetchall()
            }
            assert "projects" in fk_tables
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM conversations"
            ).scalar() == 1
            connection.commit()
            with pytest.raises(IntegrityError):
                with connection.begin():
                    connection.execute(
                        text(
                            "INSERT INTO conversations "
                            "(id, account_id, project_id, title, pinned, created_at, updated_at) "
                            "VALUES ('bad', 'account-legacy', 'missing-project', 'x', 0, '2020', '2020')"
                        )
                    )
    finally:
        database.close()


def test_database_idempotency_key_keeps_one_durable_run(tmp_path):
    _, database = make_client(tmp_path)
    try:
        settings = load_settings()
        account = AccountService(database, settings.database, settings.auth).register(
            email="durable-idempotency@example.com", password="long-password-12"
        )
        workspace = WorkspaceService(database)
        conversation = workspace.create(account_id=account.account.id)
        common = {
            "account_id": account.account.id,
            "conversation_id": conversation.id,
            "question": "同一请求",
            "status": "queued",
            "client_request_id": "durable-1",
        }
        workspace.record_run(run_id="run-a", **common)
        workspace.record_run(run_id="run-b", status="completed", answer="唯一结果", **{
            key: value for key, value in common.items() if key != "status"
        })
        rows = workspace.list_runs(account_id=account.account.id, conversation_id=conversation.id)
        assert len(rows) == 1
        assert rows[0].run_id == "run-a" and rows[0].status == "completed"
    finally:
        database.close()


def test_queue_contract_requires_idempotency_key(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            _, headers = auth(client, "queue-contract@example.com")
            cid = client.post(
                "/workspace/conversations", headers=headers, json={}
            ).json()["conversation"]["conversation_id"]
            response = client.post(
                f"/workspace/conversations/{cid}/runs",
                headers=headers,
                json={"conversation_id": cid, "text": "缺少幂等键"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "client_request_id_required"
    finally:
        database.close()


def test_workbench_lifecycle_snapshot_follows_stream_cursor(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            _, headers = auth(client, "stream-order@example.com")
            cid = client.post(
                "/workspace/conversations", headers=headers, json={}
            ).json()["conversation"]["conversation_id"]
            submitted = client.post(
                f"/workspace/conversations/{cid}/runs",
                headers=headers,
                json={
                    "conversation_id": cid,
                    "client_request_id": "stream-order-1",
                    "text": "顺序",
                },
            ).json()["run"]
            client.app.state.event_hub.emit_stream(
                StreamEvent(run_id=submitted["run_id"], type="delta", text="答案")
            )
            response = client.get(
                f"/workspace/conversations/{cid}/events",
                headers=headers,
                params={"run_id": submitted["run_id"]},
            )
            lines = [json.loads(line) for line in response.text.splitlines() if line]
            assert [line["type"] for line in lines] == ["delta", "lifecycle"]
            assert lines[-1]["sequence"] == lines[0]["sequence"] == 1
    finally:
        database.close()


def test_foreign_keys_reject_orphan_conversation(tmp_path):
    _, database = make_client(tmp_path)
    try:
        with pytest.raises(IntegrityError):
            with database.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO conversations "
                        "(id, account_id, title, pinned, created_at, updated_at) "
                        "VALUES ('orphan-conversation', 'missing-account', 'x', 0, :now, :now)"
                    ),
                    {"now": datetime.now(timezone.utc)},
                )
    finally:
        database.close()


def test_project_creation_move_delete_preserves_conversation(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            _, headers = auth(client, "project-contract@example.com")
            project = client.post(
                "/workspace/projects", headers=headers, json={"name": "案件 A"}
            )
            assert project.status_code == 201
            project_id = project.json()["project_id"]
            created = client.post(
                "/workspace/conversations",
                headers=headers,
                json={"title": "归组对话", "project_id": project_id},
            )
            assert created.status_code == 201
            conversation_id = created.json()["conversation"]["conversation_id"]
            assert created.json()["conversation"]["project_id"] == project_id
            moved = client.put(
                f"/workspace/conversations/{conversation_id}/project",
                headers=headers,
                json={"project_id": None},
            )
            assert moved.status_code == 200
            assert moved.json()["conversation"]["project_id"] is None
            remounted = client.put(
                f"/workspace/conversations/{conversation_id}/project",
                headers=headers,
                json={"project_id": project_id},
            )
            assert remounted.status_code == 200
            deleted = client.delete(f"/workspace/projects/{project_id}", headers=headers)
            assert deleted.status_code == 204
            detail = client.get(
                f"/workspace/conversations/{conversation_id}", headers=headers
            )
            assert detail.status_code == 200
            assert detail.json()["conversation"]["project_id"] is None
    finally:
        database.close()


def test_accounts_cannot_read_each_others_workspace_run_material_or_project(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            _, first_headers = auth(client, "isolation-first@example.com")
            _, second_headers = auth(client, "isolation-second@example.com")
            project = client.post(
                "/workspace/projects", headers=first_headers, json={"name": "私有案件"}
            ).json()
            conversation = client.post(
                "/workspace/conversations",
                headers=first_headers,
                json={"project_id": project["project_id"]},
            ).json()["conversation"]
            cid = conversation["conversation_id"]
            uploaded = client.post(
                f"/workspace/conversations/{cid}/materials",
                headers=first_headers,
                files={"file": ("scope.txt", b"private", "text/plain")},
            ).json()["material"]
            submitted = client.post(
                f"/workspace/conversations/{cid}/runs",
                headers=first_headers,
                json={
                    "conversation_id": cid,
                    "client_request_id": "scope-run-1",
                    "text": "private",
                    "material_ids": [uploaded["material_id"]],
                },
            )
            rid = submitted.json()["run"]["run_id"]
            assert client.get(f"/workspace/conversations/{cid}", headers=second_headers).status_code == 404
            assert client.get(f"/workspace/runs/{rid}", headers=second_headers, params={"conversation_id": cid}).status_code == 404
            assert client.get("/workspace/projects", headers=second_headers).json()["projects"] == []
            assert client.get("/workspace/materials", headers=second_headers).json()["materials"] == []
    finally:
        database.close()


def test_material_passages_transitions_uploaded_to_ready(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            _, headers = auth(client, "material-state@example.com")
            cid = client.post("/workspace/conversations", headers=headers, json={}).json()["conversation"]["conversation_id"]
            uploaded = client.post(
                f"/workspace/conversations/{cid}/materials",
                headers=headers,
                files={"file": ("state.txt", b"ready content", "text/plain")},
            ).json()["material"]
            assert uploaded["status"] == "uploaded"
            passages = client.get(
                f"/workspace/materials/{uploaded['material_id']}/passages",
                headers=headers,
            )
            assert passages.status_code == 200
            with database.engine.connect() as connection:
                assert connection.execute(
                    text("SELECT status FROM materials WHERE id=:id"),
                    {"id": uploaded["material_id"]},
                ).scalar() == "ready"
    finally:
        database.close()


def test_material_parse_failure_is_persisted_as_failed(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            _, headers = auth(client, "material-failed@example.com")
            uploaded = client.post(
                "/workspace/materials",
                headers=headers,
                files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
            ).json()["material"]
            response = client.get(
                f"/workspace/materials/{uploaded['material_id']}/passages",
                headers=headers,
            )
            assert response.status_code == 200
            with database.engine.connect() as connection:
                assert connection.execute(
                    text("SELECT status FROM materials WHERE id=:id"),
                    {"id": uploaded["material_id"]},
                ).scalar() == "failed"
    finally:
        database.close()


@pytest.mark.asyncio
async def test_waiting_run_only_resumes_and_active_run_only_steers():
    waiting = InMemoryRunScheduler(NeedsInputExecutor())
    dispatch = await waiting.submit(
        session_id="conversation-a",
        account_id="account-a",
        payload=RunInput(text="wait"),
        client_request_id="wait-1",
    )
    await waiting.wait_for_idle(session_id="conversation-a")
    state = await waiting.get_run(session_id="conversation-a", run_id=dispatch.run_id)
    assert state.status == "waiting_user"
    with pytest.raises(SchedulerCommandError, match="only the active Run"):
        await waiting.steer(
            session_id="conversation-a",
            run_id=dispatch.run_id,
            payload=RunInput(text="steer while waiting"),
        )
    resumed = await waiting.resume(
        session_id="conversation-a",
        run_id=dispatch.run_id,
        request_id=state.pending_request_ids[0],
        answer={"value": "maritime"},
    )
    assert resumed.run_id == dispatch.run_id


def test_workspace_archive_restore_has_explicit_semantics(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            _, headers = auth(client, "archive-contract@example.com")
            cid = client.post("/workspace/conversations", headers=headers, json={}).json()["conversation"]["conversation_id"]
            archived = client.post(f"/workspace/conversations/{cid}/archive", headers=headers)
            assert archived.status_code == 204
            assert client.get("/workspace/conversations", headers=headers).json()["conversations"] == []
            restored = client.post(f"/workspace/conversations/{cid}/restore", headers=headers)
            assert restored.status_code == 200
            assert restored.json()["conversation"]["archived_at"] is None
            assert client.get("/workspace/conversations", headers=headers).json()["conversations"]
    finally:
        database.close()


def test_permanent_delete_refuses_persisted_active_run(tmp_path):
    client, database = make_client(tmp_path)
    try:
        with client:
            registered, headers = auth(client, "delete-active@example.com")
            cid = client.post(
                "/workspace/conversations", headers=headers, json={}
            ).json()["conversation"]["conversation_id"]
            WorkspaceService(database).record_run(
                account_id=registered["account"]["id"],
                conversation_id=cid,
                run_id="active-run",
                question="仍在运行",
                status="running",
            )
            deleted = client.delete(f"/workspace/conversations/{cid}", headers=headers)
            assert deleted.status_code == 409
            archived = client.post(f"/workspace/conversations/{cid}/archive", headers=headers)
            assert archived.status_code == 204
    finally:
        database.close()


def test_archived_conversation_accepts_terminal_run_writeback(tmp_path):
    _, database = make_client(tmp_path)
    try:
        settings = load_settings()
        account = AccountService(database, settings.database, settings.auth).register(
            email="archive-writeback@example.com", password="long-password-12"
        )
        account_id = account.account.id
        workspace = WorkspaceService(database)
        conversation = workspace.create(account_id=account_id, title="活动运行")
        workspace.record_run(
            account_id=account_id,
            conversation_id=conversation.id,
            run_id="run-archive-1",
            question="进行中",
            status="running",
        )
        workspace.archive(account_id=account_id, conversation_id=conversation.id)
        workspace.record_run(
            account_id=account_id,
            conversation_id=conversation.id,
            run_id="run-archive-1",
            question="进行中",
            status="completed",
            answer="终态答案",
            include_archived=True,
        )
        runs = workspace.list_runs(
            account_id=account_id,
            conversation_id=conversation.id,
            include_archived=True,
        )
        assert [(run.status, run.answer) for run in runs] == [("completed", "终态答案")]
    finally:
        database.close()

