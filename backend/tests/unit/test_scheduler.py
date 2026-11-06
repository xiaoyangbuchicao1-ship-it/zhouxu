import asyncio

import pytest

from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import RunResult
from backend.harness.lifecycle.control import RunControl
from backend.harness.lifecycle.scheduler import (
    InMemoryRunScheduler,
    SchedulerCommandError,
)


class ScriptedExecutor:
    def __init__(self):
        self.calls: list[tuple[str, int, object]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block_first = False

    async def execute(self, run: RunEnvelope, control: RunControl) -> RunResult:
        self.calls.append((run.run_id, run.revision, run.payload))
        self.started.set()
        if self.block_first and len(self.calls) == 1:
            await self.release.wait()
        if run.answers:
            answer = next(iter(run.answers.values()))
            return RunResult(
                run_id=run.run_id,
                status="completed",
                answer=f"resumed:{answer}",
                elapsed_seconds=0,
            )
        if run.payload == "needs":
            return RunResult(
                run_id=run.run_id,
                status="needs_input",
                missing_inputs=["请补充页码"],
                elapsed_seconds=0,
            )
        if run.payload == "steer-old" and run.revision == 0:
            while not control.cancelled:
                await asyncio.sleep(0)
            return RunResult(
                run_id=run.run_id,
                status="completed",
                answer="stale-answer",
                elapsed_seconds=0,
            )
        return RunResult(
            run_id=run.run_id,
            status="completed",
            answer=f"answer:{run.payload}",
            elapsed_seconds=0,
        )


async def test_waiting_run_releases_slot_and_resume_targets_original_run():
    executor = ScriptedExecutor()
    scheduler = InMemoryRunScheduler(executor)

    first = await scheduler.submit(session_id="s1", payload="needs")
    await scheduler.wait_for_idle(session_id="s1")
    first_state = await scheduler.get_run(session_id="s1", run_id=first.run_id)
    assert first_state.status == "waiting_user"
    assert len(first_state.pending_request_ids) == 1

    second = await scheduler.submit(session_id="s1", payload="independent")
    await scheduler.wait_for_idle(session_id="s1")
    assert (await scheduler.get_run(session_id="s1", run_id=second.run_id)).status == "completed"

    resumed = await scheduler.resume(
        session_id="s1",
        run_id=first.run_id,
        request_id=first_state.pending_request_ids[0],
        answer="第 2 页",
    )
    await scheduler.wait_for_idle(session_id="s1")
    final = await scheduler.get_run(session_id="s1", run_id=first.run_id)
    assert resumed.status == "running"
    assert final.status == "completed" and final.output == "resumed:第 2 页"


async def test_only_one_run_is_active_and_queued_order_is_preserved():
    executor = ScriptedExecutor()
    executor.block_first = True
    scheduler = InMemoryRunScheduler(executor)

    first = await scheduler.submit(session_id="s1", payload="first")
    await asyncio.wait_for(executor.started.wait(), 1)
    second = await scheduler.submit(session_id="s1", payload="second")
    snapshot = await scheduler.snapshot(session_id="s1")
    assert first.status == "running"
    assert second.status == "queued"
    assert snapshot.active_run_id == first.run_id
    assert snapshot.queued_run_ids == [second.run_id]

    executor.release.set()
    await scheduler.wait_for_idle(session_id="s1")
    assert [call[2] for call in executor.calls] == ["first", "second"]


async def test_steer_discards_stale_result_and_reuses_same_run():
    executor = ScriptedExecutor()
    scheduler = InMemoryRunScheduler(executor)

    submitted = await scheduler.submit(session_id="s1", payload="steer-old")
    await asyncio.wait_for(executor.started.wait(), 1)
    steered = await scheduler.steer(
        session_id="s1", run_id=submitted.run_id, payload="steer-new"
    )
    await scheduler.wait_for_idle(session_id="s1")
    final = await scheduler.get_run(session_id="s1", run_id=submitted.run_id)
    assert steered.status == "running"
    assert final.status == "completed" and final.output == "answer:steer-new"
    assert [call[0] for call in executor.calls] == [submitted.run_id, submitted.run_id]
    assert executor.calls[1][1] == 1


async def test_interrupt_does_not_publish_old_result_and_allows_queue_to_continue():
    executor = ScriptedExecutor()
    scheduler = InMemoryRunScheduler(executor)
    first = await scheduler.submit(session_id="s1", payload="steer-old")
    await asyncio.wait_for(executor.started.wait(), 1)
    second = await scheduler.submit(session_id="s1", payload="next")
    interrupted = await scheduler.interrupt(session_id="s1", run_id=first.run_id)
    await scheduler.wait_for_idle(session_id="s1")
    first_state = await scheduler.get_run(session_id="s1", run_id=first.run_id)
    second_state = await scheduler.get_run(session_id="s1", run_id=second.run_id)
    assert interrupted.status == "interrupted"
    assert first_state.output is None and first_state.status == "interrupted"
    assert second_state.status == "completed"


async def test_stop_is_terminal_for_waiting_run():
    executor = ScriptedExecutor()
    scheduler = InMemoryRunScheduler(executor)
    first = await scheduler.submit(session_id="s1", payload="needs")
    await scheduler.wait_for_idle(session_id="s1")
    stopped = await scheduler.stop(session_id="s1", run_id=first.run_id)
    assert stopped.status == "stopped"
    state = await scheduler.get_run(session_id="s1", run_id=first.run_id)
    with pytest.raises(SchedulerCommandError):
        await scheduler.resume(
            session_id="s1",
            run_id=first.run_id,
            request_id=state.pending_request_ids[0],
            answer="不应恢复",
        )


@pytest.mark.parametrize("terminal", ["stop", "interrupt"])
async def test_cancellation_keeps_existing_partial_output(terminal):
    executor = ScriptedExecutor()
    executor.block_first = True
    scheduler = InMemoryRunScheduler(executor)
    first = await scheduler.submit(session_id="s1", payload="first")
    await asyncio.wait_for(executor.started.wait(), 1)
    await scheduler.set_partial(
        session_id="s1", run_id=first.run_id, partial_output="已生成的一段"
    )
    if terminal == "stop":
        result = await scheduler.stop(session_id="s1", run_id=first.run_id)
    else:
        result = await scheduler.interrupt(session_id="s1", run_id=first.run_id)
    state = await scheduler.get_run(session_id="s1", run_id=first.run_id)
    executor.release.set()
    await scheduler.wait_for_idle(session_id="s1")
    assert result.status == ("stopped" if terminal == "stop" else "interrupted")
    assert state.partial_output == "已生成的一段"

