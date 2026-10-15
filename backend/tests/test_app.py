import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app import Store, create_app
from perception_layer.engine import PerceptionEngine
from perception_layer.planning import RuleBasedSearchPlanner
from perception_layer.retrieval import LocalHybridSearch


class RecordingResponder:
    def __init__(self):
        self.calls = []
        self.fail_next = False
        self.fail_stream_next = False

    def answer(self, messages):
        self.calls.append(messages)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("模型暂时不可用")
        return f"第 {len(self.calls)} 次回答"

    def stream(self, messages):
        self.calls.append(messages)
        yield "允许装货"
        if self.fail_stream_next:
            self.fail_stream_next = False
            raise RuntimeError("模型回答中断")
        yield " 24 小时"


class CountingEncoder:
    name = "test-counting-encoder"

    def __init__(self):
        self.calls = []

    def encode(self, texts, *, query=False):
        values = list(texts)
        self.calls.append((values, query))
        return [[float("指定邮箱" in text), 1.0] for text in values]


def register(client: TestClient, email: str) -> dict:
    response = client.post("/auth/register", json={
        "email": email,
        "password": "correct-horse-battery-staple",
        "display_name": "测试用户",
    })
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['session']['token']}"}


def test_chat_keeps_materials_and_full_history_after_reopening(tmp_path: Path):
    responder = RecordingResponder()
    with TestClient(create_app(tmp_path, responder)) as client:
        owner = register(client, "owner@example.invalid")
        created = client.post("/conversations", json={}, headers=owner)
        assert created.status_code == 201
        conversation_id = created.json()["id"]
        upload = client.post(
            f"/conversations/{conversation_id}/materials?filename=合同.md",
            content="允许装货 24 小时。".encode(),
            headers={**owner, "Content-Type": "text/markdown"},
        )
        assert upload.status_code == 201
        first = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "请核对计算"}, headers=owner,
        )
        assert first.status_code == 201
        second = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "继续解释"}, headers=owner,
        )
        assert second.status_code == 201
        assert "允许装货 24 小时" in responder.calls[1][0]["content"]
        assert [message["content"] for message in responder.calls[1][1:]] == [
            "请核对计算", "第 1 次回答", "继续解释",
        ]
        reopened = client.get(f"/conversations/{conversation_id}", headers=owner)
        assert reopened.status_code == 200
        assert [item["role"] for item in reopened.json()["messages"]] == [
            "user", "assistant", "user", "assistant",
        ]
        assert reopened.json()["messages"][0]["materials"][0]["filename"] == "合同.md"
        trace = client.get(
            f"/conversations/{conversation_id}/perception/latest", headers=owner,
        )
        assert trace.status_code == 200
        assert trace.json()["status"] == "completed"
        assert trace.json()["trace"]["macro"]["mode"] == "full"
        assert "完整阅读模式" in trace.json()["trace_text"]
        assert trace.json()["answer_evaluation"]["answer_present"] is True


def test_accounts_cannot_read_each_others_conversations(tmp_path: Path):
    with TestClient(create_app(tmp_path, RecordingResponder())) as client:
        owner = register(client, "owner@example.invalid")
        other = register(client, "other@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        assert client.get("/conversations", headers=other).json()["conversations"] == []
        assert client.get(f"/conversations/{conversation_id}", headers=other).status_code == 404
        assert client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "越权访问"}, headers=other,
        ).status_code == 404
        assert client.post(
            f"/conversations/{conversation_id}/materials?filename=x.txt",
            content=b"x", headers=other,
        ).status_code == 404
        assert client.get(f"/conversations/{conversation_id}").status_code == 401


def test_delete_conversation_removes_history_files_and_links_but_keeps_shared_resources(tmp_path: Path):
    with TestClient(create_app(tmp_path, RecordingResponder())) as client:
        owner = register(client, "owner@example.invalid")
        other = register(client, "other@example.invalid")
        project = client.post("/projects", json={"name": "测试项目"}, headers=owner).json()
        chat_id = client.post("/conversations", json={"project_id": project["id"]}, headers=owner).json()["id"]
        kept_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        foreign_id = client.post("/conversations", json={}, headers=other).json()["id"]
        for conversation_id in (chat_id, kept_id, foreign_id):
            assert client.post(
                f"/conversations/{conversation_id}/materials?filename=附件.md",
                content=b"contract text", headers=owner if conversation_id != foreign_id else other,
            ).status_code == 201
        resource = client.post("/library/resources?filename=参考.md", content=b"shared reference", headers=owner).json()
        for conversation_id in (chat_id, kept_id):
            assert client.put(f"/conversations/{conversation_id}/resources/{resource['id']}", headers=owner).status_code == 204
        client.put(f"/conversations/{chat_id}/pin", headers=owner)
        answer = client.post(f"/conversations/{chat_id}/messages", json={"content": "检查合同"}, headers=owner)
        assert answer.status_code == 201
        run_id = answer.json()["perception_run_id"]
        workspace = tmp_path / "act_runs" / f"{run_id}-code-example"
        workspace.mkdir(parents=True)
        (workspace / "output.txt").write_text("private derived output")
        unrelated_workspace = workspace.parent / "unrelated-run"
        unrelated_workspace.mkdir()

        url = f"/conversations/{chat_id}"
        assert client.delete(url).status_code == 401
        assert client.delete(url, headers=other).status_code == 404
        assert (tmp_path / "files" / chat_id).exists()
        assert client.delete(url, headers=owner).status_code == 204
        assert client.get(url, headers=owner).status_code == 404
        assert client.delete(url, headers=owner).status_code == 404
        assert client.post(f"{url}/messages", json={"content": "继续"}, headers=owner).status_code == 404
        assert not (tmp_path / "files" / chat_id).exists()
        assert not workspace.exists()
        assert unrelated_workspace.exists()
        assert (tmp_path / "files" / kept_id).exists()
        assert (tmp_path / "files" / foreign_id).exists()
        assert client.get(f"/conversations/{kept_id}", headers=owner).status_code == 200
        assert client.get(f"/conversations/{foreign_id}", headers=other).status_code == 200
        assert client.get("/projects", headers=owner).json()["projects"][0]["conversation_count"] == 0
        assert client.get("/library/resources", headers=owner).json()["resources"][0]["conversation_count"] == 1
        assert client.get(f"/library/resources/{resource['id']}/chunks", headers=owner).status_code == 200
        with sqlite3.connect(tmp_path / "chat.sqlite") as db:
            for table in ("messages", "materials", "perception_runs", "conversation_resources"):
                assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE conversation_id=?", (chat_id,)).fetchone()[0] == 0
    # Recreate the app to prove deletion survives a new server instance.
    with TestClient(create_app(tmp_path, RecordingResponder())) as client:
        assert client.get(url, headers=owner).status_code == 404
        assert [chat["id"] for chat in client.get("/conversations", headers=owner).json()["conversations"]] == [kept_id]


def test_failed_delete_transaction_preserves_history_and_original_files(tmp_path: Path):
    with TestClient(create_app(tmp_path, RecordingResponder()), raise_server_exceptions=False) as client:
        owner = register(client, "owner@example.invalid")
        chat_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        client.post(f"/conversations/{chat_id}/materials?filename=附件.txt", content=b"original", headers=owner)
        with sqlite3.connect(tmp_path / "chat.sqlite") as db:
            db.execute("CREATE TRIGGER prevent_delete BEFORE DELETE ON conversations BEGIN SELECT RAISE(ABORT, 'test rollback'); END;")
        assert client.delete(f"/conversations/{chat_id}", headers=owner).status_code == 500
        assert client.get(f"/conversations/{chat_id}", headers=owner).json()["pending_materials"]
        assert next((tmp_path / "files" / chat_id).iterdir()).read_bytes() == b"original"
        with sqlite3.connect(tmp_path / "chat.sqlite") as db:
            db.execute("DROP TRIGGER prevent_delete")
        assert client.delete(f"/conversations/{chat_id}", headers=owner).status_code == 204


def test_delete_rejects_storage_escape_before_touching_history(tmp_path: Path, monkeypatch):
    with TestClient(create_app(tmp_path, RecordingResponder())) as client:
        owner = register(client, "owner@example.invalid")
        chat_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        target = tmp_path / "files" / chat_id
        original_resolve = Path.resolve
        def escaped_resolve(path, *args, **kwargs):
            return tmp_path.parent / "outside" if path == target else original_resolve(path, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", escaped_resolve)
        assert client.delete(f"/conversations/{chat_id}", headers=owner).status_code == 503
        assert client.get(f"/conversations/{chat_id}", headers=owner).status_code == 200


def test_in_flight_answer_cannot_recreate_deleted_conversation(tmp_path: Path):
    class DeletingResponder(RecordingResponder):
        def answer(self, messages):
            Store(tmp_path).delete_conversation(chat_id, account_id)
            return "已经生成，但聊天已删除"
    with TestClient(create_app(tmp_path, DeletingResponder())) as client:
        owner = register(client, "owner@example.invalid")
        chat_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        with sqlite3.connect(tmp_path / "chat.sqlite") as db:
            account_id = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat_id,)).fetchone()[0]
        result = client.post(f"/conversations/{chat_id}/messages", json={"content": "你好"}, headers=owner)
        assert result.status_code == 404
        assert client.get("/conversations", headers=owner).json()["conversations"] == []


def test_model_failure_preserves_question_and_pending_material_for_retry(tmp_path: Path):
    responder = RecordingResponder()
    with TestClient(create_app(tmp_path, responder)) as client:
        owner = register(client, "owner@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        upload_url = f"/conversations/{conversation_id}/materials?filename=contract.md"
        original = client.post(upload_url, content=b"rate is 12000", headers=owner).json()
        responder.fail_next = True
        failed = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "计算一下"}, headers=owner,
        )
        assert failed.status_code == 502
        failed_trace = client.get(
            f"/conversations/{conversation_id}/perception/latest", headers=owner,
        ).json()
        assert failed_trace["status"] == "failed"
        reopened = client.get(f"/conversations/{conversation_id}", headers=owner).json()
        assert reopened["messages"] == []
        assert reopened["pending_materials"][0]["id"] == original["id"]
        repeated = client.post(upload_url, content=b"rate is 12000", headers=owner).json()
        assert repeated["id"] == original["id"]
        retry = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "计算一下"}, headers=owner,
        )
        assert retry.status_code == 201
        assert len(client.get(f"/conversations/{conversation_id}", headers=owner).json()["messages"]) == 2


def test_pending_material_can_be_removed_only_by_owner(tmp_path: Path):
    with TestClient(create_app(tmp_path, RecordingResponder())) as client:
        owner = register(client, "owner@example.invalid")
        other = register(client, "other@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        material = client.post(
            f"/conversations/{conversation_id}/materials?filename=draft.md",
            content=b"temporary draft", headers=owner,
        ).json()
        delete_url = f"/conversations/{conversation_id}/materials/{material['id']}"
        assert client.delete(delete_url, headers=other).status_code == 404
        assert client.delete(delete_url, headers=owner).status_code == 204
        assert client.get(f"/conversations/{conversation_id}", headers=owner).json()["pending_materials"] == []
        assert client.delete(delete_url, headers=owner).status_code == 404


def test_stream_sends_deltas_and_saves_completed_answer(tmp_path: Path):
    responder = RecordingResponder()
    with TestClient(create_app(tmp_path, responder)) as client:
        owner = register(client, "owner@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        response = client.post(
            f"/conversations/{conversation_id}/messages/stream",
            json={"content": "合同约定多久？"}, headers=owner,
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        progress = [event["activity"] for event in events if event["type"] == "progress"]
        assert progress[0]["label"] == "已整理本轮信息"
        assert progress[-1]["label"] == "回答已完成"
        assert "".join(
            event["text"] for event in events if event["type"] == "delta"
        ) == "允许装货 24 小时"
        assert events[-1]["assistant_message"]["content"] == "允许装货 24 小时"
        reopened = client.get(
            f"/conversations/{conversation_id}", headers=owner,
        ).json()["messages"]
        assert [item["role"] for item in reopened] == ["user", "assistant"]
        assert reopened[-1]["activity"][-1]["label"] == "回答已完成"


def test_interrupted_stream_is_not_saved_and_can_be_retried(tmp_path: Path):
    responder = RecordingResponder()
    responder.fail_stream_next = True
    with TestClient(create_app(tmp_path, responder)) as client:
        owner = register(client, "owner@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        url = f"/conversations/{conversation_id}/messages/stream"
        first = [json.loads(line) for line in client.post(
            url, json={"content": "请解释"}, headers=owner,
        ).text.splitlines()]
        assert [event["type"] for event in first][-1] == "error"
        assert any(event["type"] == "progress" for event in first)
        assert any(event["type"] == "delta" for event in first)
        assert client.get(f"/conversations/{conversation_id}", headers=owner).json()["messages"] == []
        retry = [json.loads(line) for line in client.post(
            url, json={"content": "请解释"}, headers=owner,
        ).text.splitlines()]
        assert retry[-1]["type"] == "done"
        assert len(client.get(f"/conversations/{conversation_id}", headers=owner).json()["messages"]) == 2


def test_resource_library_is_reusable_scoped_and_retrieved_only_when_linked(tmp_path: Path):
    responder = RecordingResponder()
    with TestClient(create_app(tmp_path, responder)) as client:
        owner = register(client, "owner@example.invalid")
        other = register(client, "other@example.invalid")
        payload = (
            "# 通知条款\n\n第八条 NOR 应发送至合同指定邮箱。"
            "发送给港口代理人的副本不能替代指定邮箱送达。"
        ).encode("utf-8")
        uploaded = client.post(
            "/library/resources?filename=租船合同参考.md",
            content=payload,
            headers={**owner, "Content-Type": "text/markdown"},
        )
        assert uploaded.status_code == 201
        resource = uploaded.json()
        assert resource["status"] == "indexed"
        assert resource["chunk_count"] >= 1
        repeated = client.post(
            "/library/resources?filename=另一个名字.md", content=payload, headers=owner,
        ).json()
        assert repeated["id"] == resource["id"]
        assert client.get("/library/resources", headers=other).json()["resources"] == []
        assert client.get(
            f"/library/resources/{resource['id']}/chunks", headers=other,
        ).status_code == 404

        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        assert client.put(
            f"/conversations/{conversation_id}/resources/{resource['id']}", headers=other,
        ).status_code == 404
        assert client.put(
            f"/conversations/{conversation_id}/resources/{resource['id']}", headers=owner,
        ).status_code == 204
        detail = client.get(f"/conversations/{conversation_id}", headers=owner).json()
        assert detail["linked_resources"][0]["filename"] == "租船合同参考.md"

        answer = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "NOR 应该发给谁？"}, headers=owner,
        )
        assert answer.status_code == 201
        system_context = responder.calls[-1][0]["content"]
        assert "## 资源库参考资料" in system_context
        assert "指定邮箱" in system_context
        assert "不能当作当前案件已经发生的事实" in system_context
        trace = client.get(
            f"/conversations/{conversation_id}/perception/latest", headers=owner,
        ).json()["trace"]
        assert trace["macro"]["linked_resource_count"] == 1
        assert trace["macro"]["selected_resource_unit_count"] >= 1
        assert any(
            hit["source_scope"] == "knowledge_resource"
            for hit in trace["search"]["hits"]
        )

        assert client.delete(
            f"/conversations/{conversation_id}/resources/{resource['id']}", headers=owner,
        ).status_code == 204
        assert client.get(
            f"/conversations/{conversation_id}", headers=owner,
        ).json()["linked_resources"] == []


def test_resource_embeddings_are_built_on_upload_and_reused_on_query(tmp_path: Path):
    encoder = CountingEncoder()
    perception = PerceptionEngine(
        planner=RuleBasedSearchPlanner(),
        search=LocalHybridSearch(encoder),
    )
    with TestClient(create_app(tmp_path, RecordingResponder(), perception)) as client:
        owner = register(client, "embedding-owner@example.invalid")
        resource = client.post(
            "/library/resources?filename=通知规则.md",
            content="# 第八条\n\n通知应当发送到合同指定邮箱。".encode("utf-8"),
            headers=owner,
        ).json()
        assert len(encoder.calls) == 1
        assert encoder.calls[0][1] is False
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        client.put(
            f"/conversations/{conversation_id}/resources/{resource['id']}", headers=owner,
        )
        response = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "通知应该送到哪里？"}, headers=owner,
        )
        assert response.status_code == 201
        assert len(encoder.calls) == 2
        assert encoder.calls[1] == (["通知应该送到哪里？"], True)


def test_projects_and_pins_are_real_owner_scoped_navigation_state(tmp_path: Path):
    with TestClient(create_app(tmp_path, RecordingResponder())) as client:
        owner = register(client, "owner@example.invalid")
        other = register(client, "other@example.invalid")
        project = client.post(
            "/projects", json={"name": "租船费用争议"}, headers=owner,
        )
        assert project.status_code == 201
        project_id = project.json()["id"]
        conversation = client.post(
            "/conversations", json={"project_id": project_id}, headers=owner,
        ).json()
        conversation_id = conversation["id"]
        assert conversation["project_id"] == project_id
        assert client.put(
            f"/conversations/{conversation_id}/pin", headers=owner,
        ).status_code == 204
        listed = client.get("/conversations", headers=owner).json()["conversations"][0]
        assert listed["pinned"] == 1
        assert listed["project_id"] == project_id
        assert client.get("/projects", headers=owner).json()["projects"][0][
            "conversation_count"
        ] == 1
        assert client.get("/projects", headers=other).json()["projects"] == []
        assert client.put(
            f"/conversations/{conversation_id}/pin", headers=other,
        ).status_code == 404
        assert client.delete(
            f"/projects/{project_id}", headers=owner,
        ).status_code == 204
        assert client.get(
            f"/conversations/{conversation_id}", headers=owner,
        ).json()["conversation"]["project_id"] is None
