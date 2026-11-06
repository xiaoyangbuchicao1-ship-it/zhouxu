from datetime import datetime, timedelta, timezone

from backend.contracts.modules.memory import MemoryRecord
from backend.modules.memory.recall import DeterministicRecallPolicy


def record(record_id, *, run_id, kind="interaction", seconds=0):
    return MemoryRecord(
        record_id=record_id,
        session_id="session-a",
        run_id=run_id,
        source="user",
        kind=kind,
        summary=record_id,
        created_at=datetime.now(timezone.utc) + timedelta(seconds=seconds),
    )


def test_recall_prioritizes_current_run_and_reports_budget_omissions():
    policy = DeterministicRecallPolicy()
    result = policy.select(
        [
            record("old", run_id="old", seconds=1),
            record("correction", run_id="old", kind="correction", seconds=2),
            record("current", run_id="current", seconds=3),
            record("background", run_id="old", seconds=4),
        ],
        session_id="session-a",
        run_id="current",
        task_id=None,
        task_state=None,
        reference_ids=[],
        max_items=None,
        max_chars=500,
    )
    assert "current" in result.selected_item_ids
    assert result.omitted_item_ids
    assert result.selection_policy == "deterministic_layers"


def test_recall_preserves_stable_chronological_order_after_selection():
    policy = DeterministicRecallPolicy()
    result = policy.select(
        [record("one", run_id="run"), record("two", run_id="run", seconds=1)],
        session_id="session-a",
        run_id="run",
        task_id=None,
        task_state=None,
        reference_ids=[],
        max_items=None,
        max_chars=10000,
    )
    assert result.selected_item_ids == ["one", "two"]
