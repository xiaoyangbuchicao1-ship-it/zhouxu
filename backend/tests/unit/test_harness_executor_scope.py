import pytest

from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import RunInput, RunResult
from backend.contracts.modules.materials import MaterialInput
from backend.harness.lifecycle.control import RunControl
from backend.harness.lifecycle.executor import HarnessRunExecutor


class RecordingMaterialService:
    def __init__(self, material: MaterialInput):
        self.material = material
        self.calls: list[dict[str, object]] = []

    def resolve(self, *, account_id: str, session_id: str, material_ids: list[str], conversation_id: str):
        self.calls.append(
            {
                "account_id": account_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
                "material_ids": material_ids,
            }
        )
        return [self.material]


class RecordingHarness:
    def __init__(self):
        from backend.config.settings import load_settings
        self.settings = load_settings()
        self.inputs: list[RunInput] = []

    async def run(self, value, control, *, session_id, task_id, run_id, recovery, history, checkpoint):
        self.inputs.append(value)
        return RunResult(
            run_id=run_id,
            status="completed",
            answer="材料已交给 Harness",
            elapsed_seconds=0,
        )


async def test_executor_keeps_account_and_conversation_scopes_separate(tmp_path):
    material = MaterialInput(
        uri=str(tmp_path / "note.txt"),
        material_id="material-1",
        name="note.txt",
        media_type="text/plain",
    )
    service = RecordingMaterialService(material)
    harness = RecordingHarness()
    executor = HarnessRunExecutor(harness, material_service=service)
    run = RunEnvelope(
        account_id="account-1",
        session_id="conversation-1",
        conversation_id="conversation-1",
        payload=RunInput(text="读取材料", material_ids=["material-1"]),
    )

    result = await executor.execute(run, RunControl())

    assert result.status == "completed"
    assert service.calls == [
        {
            "account_id": "account-1",
            "session_id": "conversation-1",
            "conversation_id": "conversation-1",
            "material_ids": ["material-1"],
        }
    ]
    assert harness.inputs[0].material_ids == []
    assert harness.inputs[0].materials == [material]


async def test_executor_rejects_uploaded_material_without_account_identity():
    material = MaterialInput(
        uri="legacy.txt",
        material_id="legacy-material",
        name="legacy.txt",
        media_type="text/plain",
    )
    service = RecordingMaterialService(material)
    harness = RecordingHarness()
    executor = HarnessRunExecutor(harness, material_service=service)
    run = RunEnvelope(
        session_id="legacy-session",
        payload=RunInput(text="读取材料", material_ids=["legacy-material"]),
    )

    with pytest.raises(RuntimeError, match="material_account_identity_required"):
        await executor.execute(run, RunControl())
    assert service.calls == [] and harness.inputs == []
