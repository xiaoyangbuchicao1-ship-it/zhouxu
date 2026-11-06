from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import DecisionProposal, RunInput
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.support import ScriptedModel


async def test_harness_keeps_history_between_runs_for_same_session():
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer="first"),
        DecisionProposal(kind="answer", answer="second"),
    )
    harness = build_harness(
        model=model,
        settings=load_settings(),
        events=MemoryEventSink(),
    )
    first = await harness.run(RunInput(text="first question"), session_id="session-a")
    second = await harness.run(RunInput(text="second question"), session_id="session-a")
    assert first.status == second.status == "completed"
    assert any(item.summary == "first question" for item in model.requests[1].input.perceived.records)


async def test_harness_does_not_share_explicit_sessions():
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer="a"),
        DecisionProposal(kind="answer", answer="b"),
    )
    harness = build_harness(
        model=model,
        settings=load_settings(),
        events=MemoryEventSink(),
    )
    await harness.run(RunInput(text="private a"), session_id="session-a")
    await harness.run(RunInput(text="private b"), session_id="session-b")
    assert not any(item.summary == "private a" for item in model.requests[1].input.perceived.records)
