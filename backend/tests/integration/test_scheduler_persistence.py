from backend.contracts.harness.lifecycle import (
    PersistedSession,
    RunEnvelope,
    SessionSnapshot,
)
from backend.contracts.modules.loop import RunResult
from backend.harness.lifecycle.control import RunControl
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
from backend.infrastructure.persistence.run_store import JsonRunStore


class PersistedScript:
    def __init__(self, *, waiting: bool = False, answer: str = "done"):
        self.waiting = waiting
        self.answer = answer

    async def execute(self, run: RunEnvelope, control: RunControl) -> RunResult:
        if self.waiting and not run.answers:
            return RunResult(
                run_id=run.run_id,
                status="needs_input",
                missing_inputs=["请补充确认"],
                elapsed_seconds=0,
            )
        return RunResult(
            run_id=run.run_id,
            status="completed",
            answer=self.answer,
            elapsed_seconds=0,
        )


async def test_waiting_run_survives_scheduler_recreation(tmp_path):
    store = JsonRunStore(tmp_path / "runs.json")
    first_scheduler = InMemoryRunScheduler(PersistedScript(waiting=True), store)
    submitted = await first_scheduler.submit(
        session_id="persistent", account_id="account-1", payload="case"
    )
    await first_scheduler.wait_for_idle(session_id="persistent")
    waiting = await first_scheduler.get_run(
        session_id="persistent", run_id=submitted.run_id
    )
    assert waiting.status == "waiting_user"
    assert waiting.account_id == "account-1"

    second_scheduler = InMemoryRunScheduler(
        PersistedScript(answer="resumed"), store
    )
    restored = await second_scheduler.get_run(
        session_id="persistent", run_id=submitted.run_id
    )
    assert restored.status == "waiting_user"
    assert restored.account_id == "account-1"
    await second_scheduler.resume(
        session_id="persistent",
        run_id=submitted.run_id,
        request_id=restored.pending_request_ids[0],
        answer="确认",
    )
    await second_scheduler.wait_for_idle(session_id="persistent")
    completed = await second_scheduler.get_run(
        session_id="persistent", run_id=submitted.run_id
    )
    assert completed.status == "completed"
    assert completed.output == "resumed"
    assert completed.account_id == "account-1"


async def test_recovery_automatically_continues_running_task(tmp_path):
    store = JsonRunStore(tmp_path / "runs.json")
    running = RunEnvelope(
        run_id="running",
        session_id="crashed",
        payload="side-effecting-work",
        status="running",
    )
    await store.save(
        PersistedSession(
            snapshot=SessionSnapshot(
                session_id="crashed",
                active_run_id="running",
                runs=[running],
            )
        )
    )
    scheduler = InMemoryRunScheduler(PersistedScript(), store)
    recovered = await scheduler.get_run(session_id="crashed", run_id="running")
    assert recovered.status in {"running", "queued", "completed"}
    await scheduler.wait_for_idle(session_id="crashed")
    recovered = await scheduler.get_run(session_id="crashed", run_id="running")
    assert recovered.status == "completed"
    assert recovered.error is None


async def test_recovery_continues_running_task_before_queued_task(tmp_path):
    store = JsonRunStore(tmp_path / "runs.json")
    running = RunEnvelope(
        run_id="running",
        session_id="crashed-queue",
        payload="in-flight",
        status="running",
    )
    queued = RunEnvelope(
        run_id="queued",
        session_id="crashed-queue",
        payload="queued-work",
        status="queued",
    )
    await store.save(
        PersistedSession(
            snapshot=SessionSnapshot(
                session_id="crashed-queue",
                active_run_id="running",
                queued_run_ids=["queued"],
                runs=[running, queued],
            )
        )
    )
    scheduler = InMemoryRunScheduler(PersistedScript(answer="queued-done"), store)
    await scheduler.wait_for_idle(session_id="crashed-queue")
    recovered_running = await scheduler.get_run(
        session_id="crashed-queue", run_id="running"
    )
    recovered_queued = await scheduler.get_run(
        session_id="crashed-queue", run_id="queued"
    )
    assert recovered_running.status == "completed"
    assert recovered_queued.status == "completed"
    assert recovered_queued.output == "queued-done"

