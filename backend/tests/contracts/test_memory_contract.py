from datetime import datetime, timezone

from backend.contracts.modules.loop import FeedbackRecord, MemoryContext
from backend.contracts.modules.memory import (
    MemoryInput,
    MemoryOutput,
    MemoryRecord,
    TaskState,
    TaskStep,
)


def test_memory_contracts_keep_open_body_and_stable_scope():
    record = MemoryRecord(
        session_id="session-a",
        run_id="run-a",
        source="user",
        kind="interaction",
        body={"maritime_specific_value": {"draft": True}},
        created_at=datetime.now(timezone.utc),
        future_optional="ignored",
    )
    assert record.body["maritime_specific_value"]["draft"] is True
    assert "future_optional" not in record.model_dump()

    recall = MemoryOutput(
        scope="run-a",
        session_id="session-a",
        run_id="run-a",
        items=[FeedbackRecord(source="user", summary="hello")],
    )
    assert recall.scope == "run-a" and recall.closed_run is False


def test_old_memory_context_and_clear_operation_remain_valid():
    context = MemoryContext(scope="run-a", items=[FeedbackRecord(source="user")])
    request = MemoryInput(operation="clear")
    assert context.items[0].source == "user"
    assert request.operation == "clear"


def test_task_state_allows_partial_plan():
    state = TaskState(
        task_id="task-a",
        session_id="session-a",
        goal="整理材料",
        steps=[TaskStep(step_id="step-1", title="读取材料")],
        current_step_id="step-1",
    )
    assert state.revision == 0 and state.steps[0].status == "pending"
