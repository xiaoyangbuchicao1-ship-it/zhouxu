from backend.contracts.modules.memory import TaskState, TaskStep
from backend.infrastructure.database.in_memory_memory import InMemoryMemoryRepository
from backend.modules.memory.progress import TaskProgress


async def test_task_revision_and_step_validation():
    repository = InMemoryMemoryRepository()
    progress = TaskProgress(repository)
    first = TaskState(
        task_id="task-a",
        session_id="session-a",
        goal="goal",
        steps=[TaskStep(step_id="step-1", title="first")],
        current_step_id="step-1",
    )
    accepted = await progress.update(first, expected_revision=0)
    stale = await progress.update(first, expected_revision=0)
    invalid = await progress.update(
        first.model_copy(update={"current_step_id": "missing"}),
        expected_revision=1,
    )
    assert accepted.accepted and accepted.task_state.revision == 1
    assert stale.conflict and stale.task_state.revision == 1
    assert invalid.conflict and invalid.notices == ["unknown_current_step"]


async def test_concurrent_task_updates_use_compare_and_swap_and_server_revision():
    import asyncio

    repository = InMemoryMemoryRepository()
    state = TaskState(session_id="s", task_id="t", revision=999)
    first = await repository.save_task(state, expected_revision=0)
    assert first.accepted and first.task_state.revision == 1
    results = await asyncio.gather(
        *[
            repository.save_task(
                first.task_state.model_copy(update={"goal": str(i)}),
                expected_revision=1,
            )
            for i in range(2)
        ]
    )
    assert sum(r.accepted for r in results) == 1
    assert sum(r.conflict for r in results) == 1
    stale = await repository.save_task(first.task_state, expected_revision=None)
    assert stale.conflict and stale.task_state.revision == 2


async def test_same_task_name_can_exist_in_two_sessions_without_state_disclosure():
    repository = InMemoryMemoryRepository()
    for session in ("a", "b"):
        response = await repository.save_task(
            TaskState(session_id=session, task_id="shared", goal=session),
            expected_revision=0,
        )
        assert response.accepted
    foreign = await repository.save_task(
        TaskState(session_id="c", task_id="shared"), expected_revision=1
    )
    assert foreign.conflict and foreign.task_state is None
    assert (await repository.get_task(session_id="a", task_id="shared")).goal == "a"
    assert (await repository.get_task(session_id="b", task_id="shared")).goal == "b"
