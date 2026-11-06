from fastapi.testclient import TestClient

from backend.application.accounts.service import AccountService
from backend.application.workspace.materials import MaterialService
from backend.config.settings import load_settings
from backend.contracts.modules.loop import RunResult
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


def project_client(tmp_path):
    settings = load_settings()
    database_settings = settings.database.model_copy(
        update={"url": f"sqlite:///{(tmp_path / 'workspace.db').as_posix()}"}
    )
    database = Database(database_settings)
    database.initialize()
    accounts = AccountService(database, database_settings, settings.auth)
    materials = MaterialService(database, settings)
    app = create_app(
        control_service=HarnessControlService(CompletedExecutor()),
        account_service=accounts,
        material_service=materials,
    )
    return TestClient(app), database


def test_projects_are_durable_and_account_scoped(tmp_path):
    client, database = project_client(tmp_path)
    try:
        with client:
            first = client.post(
                "/auth/register",
                json={"email": "projects-one@example.com", "password": "long-password-12"},
            ).json()
            second = client.post(
                "/auth/register",
                json={"email": "projects-two@example.com", "password": "long-password-12"},
            ).json()
            first_headers = {"Authorization": f"Bearer {first['session']['token']}"}
            second_headers = {"Authorization": f"Bearer {second['session']['token']}"}

            created = client.post(
                "/workspace/projects",
                headers=first_headers,
                json={"name": "海事合同审计"},
            )
            assert created.status_code == 201
            project = created.json()
            project_id = project["project_id"]
            assert project["name"] == "海事合同审计"
            assert client.get("/workspace/projects", headers=second_headers).json()["projects"] == []

            conversation = client.post(
                "/workspace/conversations",
                headers=first_headers,
                json={"title": "真实合同会话"},
            ).json()["conversation"]
            conversation_id = conversation["conversation_id"]
            moved = client.put(
                f"/workspace/conversations/{conversation_id}/project",
                headers=first_headers,
                json={"project_id": project_id},
            )
            assert moved.status_code == 200
            assert moved.json()["conversation"]["project_id"] == project_id
            assert client.get("/workspace/projects", headers=first_headers).json()["projects"][0]["conversation_count"] == 1

            renamed = client.patch(
                f"/workspace/projects/{project_id}",
                headers=first_headers,
                json={"name": "合同审计归档"},
            )
            assert renamed.status_code == 200
            assert renamed.json()["name"] == "合同审计归档"

            pinned = client.patch(
                f"/workspace/conversations/{conversation_id}",
                headers=first_headers,
                json={"pinned": True},
            )
            assert pinned.status_code == 200
            refreshed = client.get("/workspace/conversations", headers=first_headers)
            assert refreshed.json()["conversations"][0]["pinned"] is True

            removed = client.put(
                f"/workspace/conversations/{conversation_id}/project",
                headers=first_headers,
                json={"project_id": None},
            )
            assert removed.status_code == 200
            assert removed.json()["conversation"]["project_id"] is None

            moved_again = client.put(
                f"/workspace/conversations/{conversation_id}/project",
                headers=first_headers,
                json={"project_id": project_id},
            )
            assert moved_again.status_code == 200
            assert client.patch(
                f"/workspace/projects/{project_id}",
                headers=second_headers,
                json={"name": "越权改名"},
            ).status_code == 404
            assert client.delete(
                f"/workspace/projects/{project_id}", headers=second_headers
            ).status_code == 404
            assert client.put(
                f"/workspace/conversations/{conversation_id}/project",
                headers=second_headers,
                json={"project_id": project_id},
            ).status_code == 404

            deleted = client.delete(
                f"/workspace/projects/{project_id}", headers=first_headers
            )
            assert deleted.status_code == 204
            listed = client.get("/workspace/conversations", headers=first_headers)
            assert listed.status_code == 200
            assert listed.json()["conversations"][0]["conversation_id"] == conversation_id
            assert listed.json()["conversations"][0]["project_id"] is None
            assert client.get("/workspace/projects", headers=first_headers).json()["projects"] == []

            uploaded = client.post(
                "/workspace/materials",
                headers=first_headers,
                files={"file": ("contract.txt", "第一条：承运人应妥善保管货物。".encode(), "text/plain")},
            )
            assert uploaded.status_code == 201
            material_id = uploaded.json()["material"]["material_id"]
            passages = client.get(
                f"/workspace/materials/{material_id}/passages", headers=first_headers
            )
            assert passages.status_code == 200
            assert passages.json()["chunks"][0]["text"]
            assert client.get(
                f"/workspace/materials/{material_id}/passages", headers=second_headers
            ).status_code == 404
    finally:
        database.close()
