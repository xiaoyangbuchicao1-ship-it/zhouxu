import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from act_runtime import CodeRunResult, ModelDecision, ToolCall
from app import DeepSeekProResponder, create_app


class ScriptedPythonRunner:
    runtime_name = "test-isolated-python"

    def __init__(self):
        self.calls = []

    def run(self, code, files, *, run_key):
        self.calls.append({"code": code, "files": list(files), "run_key": run_key})
        if "1 / 0" in code:
            return CodeRunResult("failed", "", "ZeroDivisionError", 5, runtime=self.runtime_name)
        return CodeRunResult("completed", "12000\n", "", 4, runtime=self.runtime_name)


class RepairingActResponder:
    def __init__(self):
        self.decisions = 0
        self.observed_messages = []

    def decide(self, messages, tools):
        self.observed_messages.append(messages)
        self.decisions += 1
        if self.decisions == 1:
            return ModelDecision(
                "",
                (ToolCall("call-broken", "python_run", {
                    "purpose": "计算滞期费",
                    "code": "print(1 / 0)",
                }),),
            )
        if self.decisions == 2:
            assert any(
                message.get("role") == "tool" and "ZeroDivisionError" in message.get("content", "")
                for message in messages
            )
            return ModelDecision(
                "",
                (ToolCall("call-repaired", "python_run", {
                    "purpose": "修正后重新计算滞期费",
                    "code": "print(24 * 12000 / 24)",
                }),),
            )
        assert any(
            message.get("role") == "tool" and "12000" in message.get("content", "")
            for message in messages
        )
        return ModelDecision("已经可以回答", ())

    def answer(self, messages):
        assert any(
            message.get("role") == "tool" and "12000" in message.get("content", "")
            for message in messages
        )
        return "修正程序错误后，计算结果为 12,000 美元。"

    def stream(self, messages):
        yield self.answer(messages)


class CasualActResponder:
    def __init__(self):
        self.decisions = 0

    def decide(self, messages, tools):
        self.decisions += 1
        assert {item["function"]["name"] for item in tools} == {
            "case_search", "resource_search", "web_search", "python_run",
        }
        return ModelDecision("已有信息足以回答，无需工具。", ())

    def answer(self, messages):
        return "你好，我是舟序。"

    def stream(self, messages):
        yield self.answer(messages)


def register(client: TestClient, email: str) -> dict:
    response = client.post("/auth/register", json={
        "email": email,
        "password": "correct-horse-battery-staple",
        "display_name": "测试用户",
    })
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['session']['token']}"}


def test_act_observes_code_error_repairs_and_records_trace(tmp_path: Path):
    responder = RepairingActResponder()
    runner = ScriptedPythonRunner()
    with TestClient(create_app(tmp_path, responder, python_runner=runner)) as client:
        owner = register(client, "act-owner@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        client.post(
            f"/conversations/{conversation_id}/materials?filename=合同.md",
            content="费率为每24小时12000美元。".encode("utf-8"),
            headers=owner,
        )

        response = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "请计算超时24小时的金额"},
            headers=owner,
        )

        assert response.status_code == 201
        assert response.json()["assistant_message"]["content"].endswith("12,000 美元。")
        activity = response.json()["assistant_message"]["activity"]
        assert any(item.get("tool") == "python_run" for item in activity)
        assert activity[-1]["label"] == "回答已完成"
        assert len(runner.calls) == 2
        assert runner.calls[0]["files"][0].display_name == "合同.md"
        latest = client.get(
            f"/conversations/{conversation_id}/perception/latest", headers=owner
        ).json()
        act = latest["trace"]["act"]
        assert act["version"] == "act-v1"
        assert act["termination"] == "model_ready"
        assert act["tool_call_count"] == 2
        assert act["steps"][0]["calls"][0]["arguments"]["code"] == "print(1 / 0)"
        assert "ZeroDivisionError" in act["steps"][0]["observations"][0]["observation"]
        assert [step["observations"][0]["status"] for step in act["steps"][:2]] == [
            "failed", "completed",
        ]
        assert "ACT 本轮共执行 2 次工具调用" in latest["trace_text"]
        reopened = client.get(
            f"/conversations/{conversation_id}", headers=owner
        ).json()["messages"]
        assert reopened[-1]["activity"] == activity


def test_casual_chat_model_can_decline_all_tools(tmp_path: Path):
    responder = CasualActResponder()
    with TestClient(create_app(tmp_path, responder, python_runner=ScriptedPythonRunner())) as client:
        owner = register(client, "casual-owner@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        response = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "你好"},
            headers=owner,
        )
        assert response.status_code == 201
        assert responder.decisions == 1
        latest = client.get(
            f"/conversations/{conversation_id}/perception/latest", headers=owner
        ).json()
        assert latest["trace"]["act"]["enabled"] is True
        assert latest["trace"]["act"]["termination"] == "model_ready"
        assert latest["trace"]["act"]["tool_call_count"] == 0


def test_material_analysis_without_tool_request_still_offers_tools(tmp_path: Path):
    responder = CasualActResponder()
    with TestClient(create_app(tmp_path, responder, python_runner=ScriptedPythonRunner())) as client:
        owner = register(client, "direct-evidence@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        client.post(
            f"/conversations/{conversation_id}/materials?filename=合同.md",
            content="允许装货24小时，费率为每24小时12000美元。".encode("utf-8"),
            headers=owner,
        )
        response = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "请核对12000美元是如何计算出来的，并区分各类材料"},
            headers=owner,
        )
        assert response.status_code == 201
        assert responder.decisions == 1
        latest = client.get(
            f"/conversations/{conversation_id}/perception/latest", headers=owner
        ).json()
        assert latest["trace"]["act"]["enabled"] is True
        assert latest["trace"]["act"]["tool_call_count"] == 0


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", ["这笔滞期费到底是多少？", "你错了。", "再看一遍。"])
def test_no_trigger_words_do_not_block_model_actions(tmp_path: Path, streaming, question):
    responder = RepairingActResponder()
    runner = ScriptedPythonRunner()
    with TestClient(create_app(tmp_path, responder, python_runner=runner)) as client:
        owner = register(client, "open-actions@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        endpoint = f"/conversations/{conversation_id}/messages" + ("/stream" if streaming else "")
        response = client.post(endpoint, json={"content": question}, headers=owner)
        assert response.status_code == (200 if streaming else 201)
        if streaming:
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1]["type"] == "done"
            assert any(item["type"] == "progress" and item["activity"].get("tool") == "python_run" for item in events)
        assert responder.decisions == 3
        assert len(runner.calls) == 2
        latest = client.get(f"/conversations/{conversation_id}/perception/latest", headers=owner).json()
        assert latest["trace"]["act"]["tool_call_count"] == 2


class RecordingProResponder(DeepSeekProResponder):
    def __init__(self):
        super().__init__()
        self.thinking_modes = []

    def decide(self, messages, tools):
        return ModelDecision("可以直接回答。", ())

    def answer(self, messages, *, thinking=True):
        self.thinking_modes.append(thinking)
        return "这是用于路由检查的回答。"

    def stream(self, messages, *, thinking=True):
        self.thinking_modes.append(thinking)
        yield "这是用于路由检查的回答。"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", ["你好", "这笔费用是多少？", "深入分析这个问题"])
def test_answer_thinking_is_not_switched_by_user_words(tmp_path: Path, streaming, question):
    responder = RecordingProResponder()
    with TestClient(create_app(tmp_path, responder)) as client:
        owner = register(client, "thinking@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        endpoint = f"/conversations/{conversation_id}/messages" + ("/stream" if streaming else "")
        response = client.post(endpoint, json={"content": question}, headers=owner)
        assert response.status_code == (200 if streaming else 201)
        assert responder.thinking_modes == [True]


def test_model_choice_does_not_bypass_registered_tool_boundary(tmp_path: Path):
    class UnregisteredToolResponder(CasualActResponder):
        def decide(self, messages, tools):
            self.decisions += 1
            if self.decisions == 1:
                return ModelDecision("", (ToolCall("unknown-call", "delete_all_files", {}),))
            assert any(message.get("role") == "tool" and "不允许的工具" in message.get("content", "") for message in messages)
            return ModelDecision("该工具不可执行，使用已有信息回答。", ())

    runner = ScriptedPythonRunner()
    with TestClient(create_app(tmp_path, UnregisteredToolResponder(), python_runner=runner)) as client:
        owner = register(client, "tool-boundary@example.invalid")
        conversation_id = client.post("/conversations", json={}, headers=owner).json()["id"]
        response = client.post(f"/conversations/{conversation_id}/messages", json={"content": "继续处理"}, headers=owner)
        assert response.status_code == 201
        latest = client.get(f"/conversations/{conversation_id}/perception/latest", headers=owner).json()
        assert latest["trace"]["act"]["steps"][0]["observations"][0]["status"] == "refused"
        assert runner.calls == []

