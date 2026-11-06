from backend.infrastructure.execution import runtime
from backend.tests.integration.test_workspace_api import client_for


def test_download_requires_actual_run_owner_and_preserves_snapshot(
    tmp_path, monkeypatch
):
    client, database = client_for(tmp_path)
    store = runtime.ExecutionStore(tmp_path / "deliveries")
    monkeypatch.setattr(runtime, "ExecutionStore", lambda: store)
    try:
        with client:

            def register(email):
                result = client.post(
                    "/auth/register",
                    json={"email": email, "password": "long-password-12"},
                ).json()
                return {"Authorization": "Bearer " + result["session"]["token"]}

            owner = register("file-owner@example.test")
            other = register("file-other@example.test")
            cid = client.post(
                "/workspace/conversations", json={}, headers=owner
            ).json()["conversation"]["conversation_id"]
            response = client.post(
                f"/workspace/conversations/{cid}/runs",
                json={"text": "fixture execution", "conversation_id":cid,"client_request_id":"file-api-fixture"},
                headers=owner,
            )
            assert response.status_code == 202
            rid = response.json()["run"]["run_id"]
            item = store.save(rid, "草稿.txt", b"immutable delivery")
            path = f"/maritime/runs/{rid}/files/{item['id']}?conversation_id={cid}"
            result = client.get(path, headers=owner)
            assert result.status_code == 200 and result.content == b"immutable delivery"
            assert "attachment" in result.headers["content-disposition"]
            assert result.headers["x-content-type-options"] == "nosniff"
            assert client.get(path, headers=other).status_code == 404
            assert client.get(path).status_code == 401
            listed = client.get(
                f"/maritime/runs/{rid}/deliverables?conversation_id={cid}",
                headers=owner,
            )
            assert listed.json()["files"][0]["sha256"] == item["sha256"]
            office = store.save(rid, "验收.xlsx", b"immutable office")

            async def render_preview(self, name, data):
                assert name == "验收.xlsx"
                assert data == b"immutable office"
                return b"<!doctype html><html><body>preview</body></html>"

            monkeypatch.setattr(runtime.DockerRuntime, "preview", render_preview)
            preview_path = f"/maritime/runs/{rid}/files/{office['id']}/preview?conversation_id={cid}"
            preview = client.get(preview_path, headers=owner)
            assert preview.status_code == 200
            assert preview.content.startswith(b"<!doctype html>")
            assert preview.headers["content-type"].startswith("text/html")
            assert preview.headers["content-disposition"].startswith("inline;")
            assert client.get(preview_path, headers=other).status_code == 404
            assert (
                client.get(path.replace(cid, "wrong"), headers=owner).status_code == 404
            )
    finally:
        database.close()
