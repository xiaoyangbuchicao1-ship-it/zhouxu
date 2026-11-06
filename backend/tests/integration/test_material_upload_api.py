"""Workspace material ownership and real parser/context integration."""

from fastapi.testclient import TestClient

from backend.application.accounts.service import AccountService
from backend.application.workspace.materials import MaterialService
from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import DecisionProposal, RunResult
from backend.harness.lifecycle.executor import HarnessRunExecutor
from backend.infrastructure.database.connection import Database
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import HarnessControlService
from backend.tests.support import ScriptedModel


class CompletedExecutor:
    async def execute(self, run, control):
        return RunResult(
            run_id=run.run_id, status="completed", answer="收到", elapsed_seconds=0,
        )


def client_for(tmp_path, model=None):
    settings = load_settings()
    settings.database.url = f"sqlite:///{(tmp_path / 'materials.db').as_posix()}"
    settings.materials.storage_root = str(tmp_path / "files")
    settings.materials.cache_root = str(tmp_path / "cache")
    database = Database(settings.database)
    database.initialize()
    accounts = AccountService(database, settings.database, settings.auth)
    materials = MaterialService(database, settings)
    executor = CompletedExecutor()
    if model is not None:
        harness = build_harness(
            model=model, settings=settings, events=MemoryEventSink(),
        )
        executor = HarnessRunExecutor(harness, material_service=materials)
    app = create_app(
        control_service=HarnessControlService(executor),
        account_service=accounts, material_service=materials, settings=settings,
    )
    return TestClient(app), database


def conversation(client, email):
    registered = client.post(
        "/auth/register",
        json={"email": email, "password": "long-password-12"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['session']['token']}"}
    created = client.post(
        "/workspace/conversations", headers=headers, json={},
    )
    assert created.status_code == 201
    return headers, created.json()["conversation"]["conversation_id"]


def upload(client, headers, cid):
    response = client.post(
        f"/workspace/conversations/{cid}/materials", headers=headers,
        files={"file": ("case.txt", "承运人应当妥善保管货物。".encode(), "text/plain")},
    )
    assert response.status_code == 201
    return response.json()["material"]


def test_upload_submit_and_unbind_material_with_owner_boundary(tmp_path):
    client, database = client_for(tmp_path)
    try:
        with client:
            first, cid = conversation(client, "first@example.com")
            second, other_cid = conversation(client, "second@example.com")
            material = upload(client, first, cid)
            mid = material["material_id"]
            assert material["name"] == "case.txt" and material["size_bytes"] > 0
            assert "uri" not in material and "storage" not in material
            assert client.post(
                f"/workspace/conversations/{cid}/materials", headers=second,
                files={"file": ("other.txt", b"x", "text/plain")},
            ).status_code == 404
            assert client.post(
                f"/workspace/conversations/{other_cid}/runs", headers=second,
                json={"conversation_id": other_cid, "client_request_id": "foreign",
                      "text": "越权", "material_ids": [mid]},
            ).status_code == 404
            path = f"/workspace/conversations/{cid}/materials/{mid}"
            assert client.delete(path, headers=second).status_code == 404
            assert client.delete(path, headers=first).status_code == 204
            assert client.get(
                f"/workspace/conversations/{cid}/materials", headers=first,
            ).json()["materials"] == []
            # Removing a Conversation binding preserves the owned library original.
            assert mid in {m["material_id"] for m in client.get(
                "/workspace/materials", headers=first,
            ).json()["materials"]}
            assert client.get("/workspace/materials", headers=second).json()["materials"] == []
    finally:
        database.close()


def test_uploaded_material_reaches_context_in_followup_without_reupload(tmp_path):
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer="应当妥善保管货物。"),
        DecisionProposal(kind="answer", answer="同一材料仍可用。"),
    )
    client, database = client_for(tmp_path, model)
    try:
        with client:
            headers, cid = conversation(client, "harness@example.com")
            material = upload(client, headers, cid)
            base = f"/workspace/conversations/{cid}"
            for index in range(2):
                submitted = client.post(
                    f"{base}/runs", headers=headers,
                    json={"conversation_id": cid, "client_request_id": f"turn-{index}",
                          "text": "材料说了什么？"},
                )
                assert submitted.status_code == 202
                rid = submitted.json()["run"]["run_id"]
                streamed = client.get(
                    f"{base}/events", headers=headers, params={"run_id": rid},
                )
                assert streamed.status_code == 200
                snapshot = client.get(
                    f"/workspace/runs/{rid}", headers=headers,
                    params={"conversation_id": cid},
                )
                assert snapshot.status_code == 200
                assert snapshot.json()["run"]["status"] == "completed"
            assert len(model.requests) == 2
            for request in model.requests:
                value = request.input
                item = next(i for i in value.perceived.items
                            if i.id == f"material:{material['material_id']}")
                assert "承运人应当妥善保管货物" in str(item.body)
                assert {"read_material", "search_material"} <= {t.name for t in value.tools}
    finally:
        database.close()
