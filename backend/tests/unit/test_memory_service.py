from backend.contracts.modules.loop import FeedbackRecord
from backend.contracts.modules.memory import MemoryInput, MemoryRecord
from backend.contracts.schemas.core import ModuleRequest
from backend.modules.memory.service import InMemoryMemory


def request(run_id, call_id, value):
    return ModuleRequest(run_id=run_id, call_id=call_id, input=value)


async def test_record_is_idempotent_and_conflicts_are_reported():
    memory = InMemoryMemory()
    record = MemoryRecord(
        record_id="record-1",
        session_id="session-a",
        run_id="run-1",
        source="user",
        summary="same",
    )
    first = await memory.handle(
        request(
            "run-1",
            "write-1",
            MemoryInput(operation="record", session_id="session-a", items=[record]),
        )
    )
    duplicate = await memory.handle(
        request(
            "run-1",
            "write-2",
            MemoryInput(operation="record", session_id="session-a", items=[record]),
        )
    )
    conflict = await memory.handle(
        request(
            "run-1",
            "write-3",
            MemoryInput(
                operation="record",
                session_id="session-a",
                items=[record.model_copy(update={"summary": "changed"})],
            ),
        )
    )
    assert first.output.write.accepted_record_ids == ["record-1"]
    assert duplicate.output.write.duplicate_record_ids == ["record-1"]
    assert conflict.output.write.conflict_record_ids == ["record-1"]
    assert [item.summary for item in duplicate.output.items] == ["same"]


async def test_sessions_are_isolated_and_close_run_keeps_history():
    memory = InMemoryMemory()
    for session, run, text in [("a", "run-a", "A"), ("b", "run-b", "B")]:
        await memory.handle(
            request(
                run,
                f"write-{session}",
                MemoryInput(
                    operation="record",
                    session_id=session,
                    items=[
                        MemoryRecord(
                            session_id=session,
                            run_id=run,
                            source="user",
                            summary=text,
                        )
                    ],
                ),
            )
        )
    closed = await memory.handle(
        request(
            "run-a",
            "close",
            MemoryInput(operation="close_run", session_id="a"),
        )
    )
    other = await memory.handle(
        request("run-b", "read", MemoryInput(operation="recall", session_id="b"))
    )
    assert closed.output.items == []
    assert [item.summary for item in other.output.items] == ["B"]


async def test_legacy_feedback_record_is_accepted():
    memory = InMemoryMemory()
    response = await memory.handle(
        request(
            "run-legacy",
            "write",
            MemoryInput(
                operation="record",
                session_id="ephemeral:run-legacy",
                items=[FeedbackRecord(source="user", summary="legacy")],
            ),
        )
    )
    assert response.status == "ok"
    assert response.output.items[0].summary == "legacy"


async def test_conflicting_batch_is_atomic_and_failed():
    memory = InMemoryMemory()
    stored = MemoryRecord(record_id="same", source="user", summary="original")
    await memory.handle(
        request("r", "first", MemoryInput(operation="record", items=[stored]))
    )
    response = await memory.handle(
        request(
            "r",
            "conflict",
            MemoryInput(
                operation="record",
                items=[
                    MemoryRecord(record_id="new", source="user"),
                    stored.model_copy(update={"summary": "different"}),
                ],
            ),
        )
    )
    assert response.status == "failed"
    assert response.output.write.accepted_record_ids == []
    assert memory.repository.count_records() == 1


async def test_serialized_retry_is_idempotent_and_write_success_is_separate_from_recall():
    memory = InMemoryMemory(max_chars=1)
    value = MemoryInput(
        operation="record", items=[MemoryRecord(source="user", summary="large")]
    )
    first = await memory.handle(request("r", "write", value))
    retry = await memory.handle(
        request("r", "retry", MemoryInput.model_validate_json(value.model_dump_json()))
    )
    assert first.status == retry.status == "ok"
    assert first.output.blocked_reason is None
    assert "required_context_budget_recovered" in first.output.notices
    assert retry.output.write.duplicate_record_ids == [value.items[0].record_id]
    recall = await memory.handle(request("r", "read", MemoryInput(operation="recall")))
    assert recall.status == "ok"


async def test_purge_cleans_other_run_indexes_and_only_target_session():
    memory = InMemoryMemory()
    for session, run in [("a", "one"), ("a", "two"), ("b", "one")]:
        await memory.handle(
            request(
                run,
                "write",
                MemoryInput(
                    operation="record",
                    session_id=session,
                    items=[MemoryRecord(source="user", retention="session")],
                ),
            )
        )
    await memory.handle(
        request(
            "one",
            "purge",
            MemoryInput(operation="close_run", session_id="a", purge_session=True),
        )
    )
    assert await memory.repository.list_records(session_id="a", run_id="two") == []
    assert memory.repository.count_records(session_id="a") == 0
    assert memory.repository.count_records(session_id="b") == 1
