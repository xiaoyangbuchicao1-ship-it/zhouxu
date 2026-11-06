"""Opt-in Docker regression: real Harness/tool execution, scripted model decisions."""
import asyncio
import os

import pytest

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import ActionRequest, DecisionProposal, RunInput
from backend.infrastructure.execution.runtime import DockerRuntime, ExecutionStore, execution_tools
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.support import ScriptedModel

pytestmark = pytest.mark.skipif(os.environ.get('ZHOUXU_E2E_DOCKER') != '1', reason='Explicit Docker execution opt-in')


class ObservedRuntime(DockerRuntime):
    def __init__(self, root):
        super().__init__(image='zhouxu-task-runtime:0.2', store=ExecutionStore(root))
        self.receipts = []
        self.names = []

    async def operate(self, run_id, op, value):
        result = await super().operate(run_id, op, value)
        self.receipts.append((op, value.copy(), result))
        self.names.extend(n for n in self.worlds.values() if n not in self.names)
        return result


def action(name, **arguments):
    return DecisionProposal(kind='action', action_requests=[ActionRequest(tool_name=name, arguments=arguments)])


async def test_harness_repairs_real_python_error_then_presents_snapshot(tmp_path):
    runtime = ObservedRuntime(tmp_path / 'store')
    settings = load_settings()

    class RecoveryModel(ScriptedModel):
        async def decide(self, request):
            reply = await super().decide(request)
            if reply.kind == 'action' and reply.action_requests[0].tool_name == 'edit':
                read = next(result for op, _, result in reversed(runtime.receipts) if op == 'read')
                reply.action_requests[0].arguments['expected_sha256'] = read['sha256']
            return reply

    model = RecoveryModel(
        action('write', path='task.py', content="from pathlib import Path\nraise RuntimeError('review_expected_failure')\nPath('result.txt').write_text('verified recovery')\n"),
        action('bash', command='python task.py'),
        action('read', path='task.py'),
        action('edit', path='task.py', old_text="raise RuntimeError('review_expected_failure')", new_text='pass', expected_sha256='filled_from_actual_read'),
        action('bash', command='python task.py && python -c "from pathlib import Path; assert Path(\'result.txt\').read_text() == \'verified recovery\'"'),
        action('present', path='result.txt'),
        DecisionProposal(kind='answer', answer='文件已生成并检查。'), settings=settings,
    )
    harness = build_harness(model=model, settings=settings, events=MemoryEventSink(), tools=execution_tools(runtime))
    try:
        result = await harness.run(RunInput(text='执行测试脚本，实际失败后修复，再检查并交付文件。',facts={'task_mode':'work'}), run_id='independent-recovery')
        assert result.status == 'completed'
        commands = [r for op, _, r in runtime.receipts if op == 'bash']
        assert commands[0]['ok'] is False and commands[0]['exit_code'] != 0
        assert 'review_expected_failure' in commands[0]['stderr']
        assert commands[1]['ok'] is True and commands[1]['exit_code'] == 0
        # Harness stages an empty material index through the same real write seam.
        task_receipts = [(op, args, value) for op, args, value in runtime.receipts if args.get('path') != 'inputs/index.json']
        assert [op for op, _, _ in task_receipts] == ['write','bash','read','edit','bash','present']
        package = runtime.store.list('independent-recovery')[0]
        assert ExecutionStore(tmp_path / 'store').get('independent-recovery', package['id'])[1] == b'verified recovery'
        assert not runtime.worlds
        for name in runtime.names:
            code, _, _ = await runtime.command(['inspect', name])
            assert code != 0
    finally:
        for run_id in list(runtime.worlds):
            await runtime.close_run(run_id)


async def test_read_cannot_override_operation_and_fifo_does_not_block(tmp_path):
    runtime = ObservedRuntime(tmp_path / 'store')
    try:
        denied = await runtime.operate('negative','read',{'op':'write','path':'injected.txt','content':'bad'})
        assert denied == {'ok':False,'error':'invalid_operation_fields'}
        assert not runtime.worlds
        made = await runtime.operate('negative','bash',{'command':'mkfifo pipe; printf retained > normal.txt'})
        assert made['ok']
        result = await asyncio.wait_for(runtime.operate('negative','read',{'path':'pipe'}), timeout=3)
        assert result['ok'] is False and 'regular_single_link' in result['error']
        retained = await runtime.operate('negative','read',{'path':'normal.txt'})
        assert retained['text'] == 'retained'
    finally:
        for run_id in list(runtime.worlds):
            await runtime.close_run(run_id)


async def test_harness_repeated_bash_error_stops_before_claimed_completion(tmp_path):
    runtime = ObservedRuntime(tmp_path / 'repeat-store')
    settings = load_settings()
    model = ScriptedModel(*[
        action('bash',command=f"python -c \"raise IndexError('independent_repeat')\" # attempt {i}")
        for i in range(5)
    ], DecisionProposal(kind='answer',answer='must not execute'), settings=settings)
    harness = build_harness(model=model, settings=settings, events=MemoryEventSink(), tools=execution_tools(runtime))
    try:
        result = await harness.run(RunInput(text='执行失败脚本',facts={'task_mode':'work'}),run_id='independent-repeat')
        assert result.status == 'failed' and result.error.code == 'execution_repeated_failure'
        commands = [r for op, _, r in runtime.receipts if op == 'bash']
        assert len(commands)==5 and commands[0]['exit_code']!=0
        assert 'repair_required' in commands[1]
        assert commands[-1]['error']=='execution_repeated_failure'
        assert len(model.requests)==5 and not runtime.worlds
        assert runtime.store.list('independent-repeat')==[]
    finally:
        for run_id in list(runtime.worlds):await runtime.close_run(run_id)


async def test_chat_hidden_write_is_rejected_before_runtime_invocation(tmp_path):
    runtime = ObservedRuntime(tmp_path / 'chat-store')
    settings = load_settings()
    model = ScriptedModel(action('write',path='bad.txt',content='bad'),DecisionProposal(kind='answer',answer='must not execute'),settings=settings)
    harness = build_harness(model=model,settings=settings,events=MemoryEventSink(),tools=execution_tools(runtime))
    result=await harness.run(RunInput(text='聊天',facts={'task_mode':'chat'}),run_id='independent-chat')
    assert result.status=='failed'
    assert not runtime.worlds and runtime.receipts==[]


@pytest.mark.parametrize('initial,changed',[('chat','work'),('work','chat')])
async def test_steer_keeps_original_execution_mode(initial,changed):
    from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
    from backend.contracts.modules.loop import RunResult
    class BlockedExecutor:
        def __init__(self):self.started=asyncio.Event()
        async def execute(self,run,control):
            self.started.set()
            while not control.cancelled:await asyncio.sleep(0.01)
            return RunResult(run_id=run.run_id,status='interrupted',elapsed_seconds=0)
    executor=BlockedExecutor();scheduler=InMemoryRunScheduler(executor)
    try:
        submitted=await scheduler.submit(session_id='mode-session',payload=RunInput(text='first',facts={'task_mode':initial}))
        await asyncio.wait_for(executor.started.wait(),3)
        await scheduler.steer(session_id='mode-session',run_id=submitted.run_id,payload=RunInput(text='changed',facts={'task_mode':changed}))
        run=await scheduler.get_run(session_id='mode-session',run_id=submitted.run_id)
        assert RunInput.model_validate(run.payload).facts['task_mode']==initial
    finally:await scheduler.shutdown()
