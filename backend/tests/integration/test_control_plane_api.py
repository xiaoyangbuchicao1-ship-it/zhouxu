"""HTTP contract tests for the lifecycle control plane."""

from fastapi.testclient import TestClient

from backend.contracts.modules.loop import RunResult
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import HarnessControlService


class ScriptedExecutor:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, run, control):
        text = run.payload.text
        self.calls.append(text)
        if text == "补充信息":
            if run.answers:
                return RunResult(
                    run_id=run.run_id,
                    status="completed",
                    answer=f"收到：{next(iter(run.answers.values()))}",
                    elapsed_seconds=0,
                )
            return RunResult(
                run_id=run.run_id,
                status="needs_input",
                missing_inputs=["请提供材料页码"],
                elapsed_seconds=0,
            )
        return RunResult(
            run_id=run.run_id,
            status="completed",
            answer=f"完成：{text}",
            elapsed_seconds=0,
        )


def client_for(executor):
    service = HarnessControlService(executor)
    return TestClient(create_app(control_service=service))


def test_submit_snapshot_and_ndjson_terminal_event():
    with client_for(ScriptedExecutor()) as client:
        submitted = client.post(
            "/harness/sessions/local/runs",
            json={"text": "普通问题"},
        )
        assert submitted.status_code == 202
        body = submitted.json()
        assert body["dispatch"]["session_id"] == "local"
        run_id = body["dispatch"]["run_id"]

        events = client.get(
            "/harness/sessions/local/events", params={"run_id": run_id}
        )
        assert events.status_code == 200
        lines = [line for line in events.text.splitlines() if line]
        assert any('"type":"lifecycle"' in line for line in lines)
        run = client.get(
            f"/harness/runs/{run_id}", params={"session_id": "local"}
        )
        assert run.status_code == 200
        assert run.json()["status"] == "completed"
        assert run.json()["output"] == "完成：普通问题"


def test_resume_waiting_run_through_public_contract():
    with client_for(ScriptedExecutor()) as client:
        submitted = client.post(
            "/harness/sessions/local/runs", json={"text": "补充信息"}
        )
        run_id = submitted.json()["dispatch"]["run_id"]
        waiting = client.get(
            "/harness/sessions/local/events", params={"run_id": run_id}
        )
        assert waiting.status_code == 200

        snapshot = client.get(
            f"/harness/runs/{run_id}", params={"session_id": "local"}
        ).json()
        request_id = snapshot["pending_request_ids"][0]
        request = client.get(
            f"/harness/runs/{run_id}/input-requests/{request_id}",
            params={"session_id": "local"},
        )
        assert request.status_code == 200
        assert request.json()["request"]["question"] == "请提供材料页码"
        resumed = client.post(
            f"/harness/runs/{run_id}/resume",
            json={
                "session_id": "local",
                "request_id": request_id,
                "answer": "第 3 页",
            },
        )
        assert resumed.status_code == 200
        client.get(
            "/harness/sessions/local/events", params={"run_id": run_id}
        )
        completed = client.get(
            f"/harness/runs/{run_id}", params={"session_id": "local"}
        ).json()
        assert completed["status"] == "completed"
        assert completed["output"] == "收到：第 3 页"


def test_commands_report_conflicts_and_session_snapshot():
    executor = ScriptedExecutor()
    with client_for(executor) as client:
        submitted = client.post(
            "/harness/sessions/local/runs", json={"text": "普通问题"}
        )
        run_id = submitted.json()["dispatch"]["run_id"]
        # The run is terminal by the time the event stream returns; steering it
        # must not silently create a second execution.
        response = client.post(
            f"/harness/runs/{run_id}/steer",
            json={"session_id": "local", "text": "改写"},
        )
        assert response.status_code == 409
        session = client.get("/harness/sessions/local")
        assert session.status_code == 200
        assert session.json()["snapshot"]["session_id"] == "local"


def test_health_does_not_require_model_credentials():
    app = create_app(control_service=HarnessControlService(ScriptedExecutor()))
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

