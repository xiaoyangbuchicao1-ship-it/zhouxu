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


def client_for(tmp_path):
    settings = load_settings()
    settings = settings.model_copy(
        update={"materials": settings.materials.model_copy(update={"storage_root": str(tmp_path / "files")})}
    )
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


def test_conversation_crud_and_account_isolation(tmp_path):
    client, database = client_for(tmp_path)
    try:
        with client:
            first = client.post(
                "/auth/register",
                json={"email": "workspace-one@example.com", "password": "long-password-12"},
            ).json()
            second = client.post(
                "/auth/register",
                json={"email": "workspace-two@example.com", "password": "long-password-12"},
            ).json()
            first_headers = {"Authorization": f"Bearer {first['session']['token']}"}
            second_headers = {"Authorization": f"Bearer {second['session']['token']}"}

            created = client.post(
                "/workspace/conversations",
                headers=first_headers,
                json={"title": "海商法研究"},
            )
            assert created.status_code == 201
            conversation = created.json()["conversation"]
            conversation_id = conversation["conversation_id"]
            assert conversation["title"] == "海商法研究"

            listed = client.get("/workspace/conversations", headers=first_headers)
            assert listed.status_code == 200
            assert [item["conversation_id"] for item in listed.json()["conversations"]] == [conversation_id]

            pinned = client.patch(
                f"/workspace/conversations/{conversation_id}",
                headers=first_headers,
                json={"pinned": True},
            )
            assert pinned.status_code == 200
            assert pinned.json()["conversation"]["pinned"] is True

            denied = client.patch(
                f"/workspace/conversations/{conversation_id}",
                headers=second_headers,
                json={"title": "越权"},
            )
            assert denied.status_code == 404
            assert client.get("/workspace/conversations", headers=second_headers).json()["conversations"] == []

            deleted = client.delete(
                f"/workspace/conversations/{conversation_id}", headers=first_headers
            )
            assert deleted.status_code == 204
            assert client.get("/workspace/conversations", headers=first_headers).json()["conversations"] == []
    finally:
        database.close()


def test_workspace_conversation_material_and_run_share_scope(tmp_path):
    client, database = client_for(tmp_path)
    try:
        with client:
            registered = client.post(
                "/auth/register",
                json={"email": "workspace-run@example.com", "password": "long-password-12"},
            ).json()
            account_id = registered["account"]["id"]
            headers = {"Authorization": f"Bearer {registered['session']['token']}"}
            created = client.post("/workspace/conversations", headers=headers, json={}).json()["conversation"]
            conversation_id = created["conversation_id"]
            base = f"/harness/sessions/{conversation_id}"

            uploaded = client.post(
                f"/workspace/conversations/{conversation_id}/materials",
                headers=headers,
                files={"file": ("note.txt", "承运人应当妥善保管货物。".encode(), "text/plain")},
            )
            assert uploaded.status_code == 201
            material_id = uploaded.json()["material"]["material_id"]
            scoped_materials = client.get(
                f"/workspace/conversations/{conversation_id}/materials", headers=headers
            )
            assert scoped_materials.status_code == 200
            assert scoped_materials.json()["materials"][0]["material_id"] == material_id

            submitted = client.post(
                f"{base}/runs",
                headers=headers,
                json={"text": "读取本轮材料", "material_ids": [material_id]},
            )
            assert submitted.status_code == 202
            run_id = submitted.json()["run"]["run_id"]
            client.get(f"{base}/events", headers=headers, params={"run_id": run_id})
            snapshot = client.get(
                f"/harness/runs/{run_id}", headers=headers, params={"session_id": conversation_id}
            )
            assert snapshot.status_code == 200
            assert snapshot.json()["session_id"] == conversation_id
            assert snapshot.json()["account_id"] == account_id
            assert snapshot.json()["status"] == "completed"
            listed = client.get("/workspace/conversations", headers=headers).json()["conversations"]
            assert listed[0]["run_count"] == 1
            assert listed[0]["title"] == "读取本轮材料"
            assert client.get("/workspace/materials", headers=headers).json()["materials"][0]["material_id"] == material_id
            unbound = client.delete(
                f"/workspace/conversations/{conversation_id}/materials/{material_id}", headers=headers
            )
            assert unbound.status_code == 204
            assert client.get(
                f"/workspace/conversations/{conversation_id}/materials", headers=headers
            ).json()["materials"] == []
    finally:
        database.close()


def test_conversation_detail_and_implicit_material_inheritance(tmp_path):
    client, database = client_for(tmp_path)
    try:
        with client:
            registered = client.post(
                "/auth/register",
                json={"email": "inherit@example.com", "password": "long-password-12"},
            ).json()
            headers = {"Authorization": f"Bearer {registered['session']['token']}"}
            conversation_id = client.post(
                "/workspace/conversations", headers=headers, json={}
            ).json()["conversation"]["conversation_id"]
            uploaded = client.post(
                f"/workspace/conversations/{conversation_id}/materials",
                headers=headers,
                files={"file": ("note.txt", b"persistent material", "text/plain")},
            )
            assert uploaded.status_code == 201
            first = client.post(
                f"/harness/sessions/{conversation_id}/runs",
                headers=headers,
                json={"text": "第一轮"},
            )
            assert first.status_code == 202
            first_id = first.json()["run"]["run_id"]
            client.get(
                f"/harness/sessions/{conversation_id}/events",
                headers=headers,
                params={"run_id": first_id},
            )
            second = client.post(
                f"/harness/sessions/{conversation_id}/runs",
                headers=headers,
                json={"text": "第二轮"},
            )
            assert second.status_code == 202
            second_run = second.json()["run"]
            assert second_run["payload"]["material_ids"]
            client.get(
                f"/harness/sessions/{conversation_id}/events",
                headers=headers,
                params={"run_id": second_run["run_id"]},
            )
            detail = client.get(
                f"/workspace/conversations/{conversation_id}", headers=headers
            )
            assert detail.status_code == 200
            assert [message["role"] for message in detail.json()["messages"]] == [
                "user", "assistant", "user", "assistant"
            ]
            assert len(detail.json()["materials"]) == 1
    finally:
        database.close()


def test_library_material_can_be_linked_then_used_by_conversation(tmp_path):
    client, database = client_for(tmp_path)
    try:
        with client:
            registered = client.post(
                "/auth/register",
                json={"email": "library-link@example.com", "password": "long-password-12"},
            ).json()
            headers = {"Authorization": f"Bearer {registered['session']['token']}"}
            conversation_id = client.post(
                "/workspace/conversations", headers=headers, json={}
            ).json()["conversation"]["conversation_id"]
            uploaded = client.post(
                "/workspace/materials",
                headers=headers,
                files={"file": ("library.txt", b"library material", "text/plain")},
            )
            assert uploaded.status_code == 201
            material_id = uploaded.json()["material"]["material_id"]
            linked = client.put(
                f"/workspace/conversations/{conversation_id}/materials/{material_id}",
                headers=headers,
            )
            assert linked.status_code == 204
            submitted = client.post(
                f"/harness/sessions/{conversation_id}/runs",
                headers=headers,
                json={"text": "使用资料库材料"},
            )
            assert submitted.status_code == 202
            assert material_id in submitted.json()["run"]["payload"]["material_ids"]
    finally:
        database.close()


def test_default_titles_follow_first_question_but_keep_custom_names(tmp_path):
    from backend.application.workspace.conversations import WorkspaceService
    from backend.infrastructure.database.models import ConversationModel
    client, database = client_for(tmp_path)
    try:
        with client:
            user = client.post("/auth/register", json={"email":"titles@example.test","password":"long-password-12"}).json()
            service = WorkspaceService(database)
            account = user["account"]["id"]
            chat = service.create(account_id=account)
            first = "  请核对提单条款\n  与承运人责任  "
            service.record_run(account_id=account, conversation_id=chat.id, run_id="first",question=first,status="completed")
            service.record_run(account_id=account, conversation_id=chat.id, run_id="second",question="后续问题",status="completed")
            assert service.get(account_id=account,conversation_id=chat.id).title == "请核对提单条款 与承运人责任"
            # Simulate historical conversations written before automatic naming.
            with database.session() as db:
                db.get(ConversationModel,chat.id).title = "新对话"
                db.commit()
            assert service.list(account_id=account)[0][0].title == "请核对提单条款 与承运人责任"
            assert service.get(account_id=account,conversation_id=chat.id).title == "请核对提单条款 与承运人责任"
            service.update(account_id=account,conversation_id=chat.id,title="我的合同研究")
            service.record_run(account_id=account,conversation_id=chat.id,run_id="third",question="第三问",status="completed")
            assert service.get(account_id=account,conversation_id=chat.id).title == "我的合同研究"
    finally:
        database.close()
