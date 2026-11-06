"""Decision/action recovery through real encoding; no paid calls here."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import (
    ActionRequest,
    ActionResult,
    DecisionProposal,
    RunInput,
    ToolSpec,
)
from backend.contracts.modules.materials import MaterialInput
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.infrastructure.tools.material_read import MaterialReadTool
from backend.infrastructure.tools.material_search import MaterialSearchTool
from backend.tests.support import ScriptedModel, context_model


class ReadTool:
    def __init__(
        self, name="read_record", *, reuse=True, failure=None, slow=False, corrupt=False
    ):
        self.spec = ToolSpec(
            name=name,
            description="Read a record",
            parameters={"type": "object"},
            read_only=True,
            reuse_identical_results=reuse,
        )
        self.calls = []
        self.failure, self.slow, self.corrupt = failure, slow, corrupt

    async def execute(self, request):
        self.calls.append(request)
        if self.slow:
            await asyncio.Event().wait()
        if self.failure:
            raise self.failure
        return ActionResult(
            tool_name=request.tool_name,
            action_id="wrong" if self.corrupt else request.action_id,
            result={"value": "same observation"},
        )


def action(tool="read_record", **arguments):
    return DecisionProposal(
        kind="action",
        action_requests=[ActionRequest(tool_name=tool, arguments=arguments)],
    )


def harness(model, *tools, settings=None):
    settings = settings or load_settings()
    sink = MemoryEventSink()
    return build_harness(
        model=model,
        settings=settings,
        events=sink,
        streams=sink,
        model_context=context_model(settings),
        tools={t.spec.name: (t.spec, t) for t in tools},
    ), sink


async def test_repeated_observation_does_not_remove_other_tools():
    first, other = ReadTool(), ReadTool("read_other")
    model = ScriptedModel(
        action(),
        action(),
        action("read_other"),
        DecisionProposal(kind="answer", answer="both sources considered"),
    )
    agent, _ = harness(model, first, other)
    result = await agent.run(RunInput(text="compare sources"))
    assert result.status == "completed"
    assert (len(first.calls), len(other.calls)) == (2, 1)
    assert model.requests[2].input.purpose == "main"
    assert {t.name for t in model.requests[2].input.tools} >= {
        "read_record",
        "read_other",
    }
    assert any(
        i.updates.get("repeated_actions")
        for i in model.requests[2].input.perceived.records
    )


async def test_repeated_request_is_not_executed_again_and_is_honestly_reported():
    tool = ReadTool()
    model = ScriptedModel(
        action(),
        action(),
        action(),
        DecisionProposal(kind="answer", answer="partial result"),
    )
    agent, _ = harness(model, tool)
    result = await agent.run(RunInput(text="work"))
    assert result.status == "completed" and len(tool.calls) == 2
    last = [
        i for i in model.requests[-1].input.perceived.items if i.type == "tool_result"
    ][-1]
    assert last.body["outcome"] == "error"
    assert last.body["result"]["status"] == "not_executed"
    assert last.body["result"]["previous_action_id"] == tool.calls[-1].action_id
    assert model.requests[-1].input.purpose == "finalization"


@pytest.mark.parametrize("reuse,different_args", [(False, False), (True, True)])
async def test_polling_and_changed_queries_are_not_suppressed(reuse, different_args):
    tool = ReadTool(reuse=reuse)
    model = ScriptedModel(
        *(action(record=i if different_args else 0) for i in range(3)),
        DecisionProposal(kind="answer", answer="done"),
    )
    agent, _ = harness(model, tool)
    result = await agent.run(RunInput(text="work"))
    assert result.status == "completed" and len(tool.calls) == 3
    assert all(r.input.purpose == "main" for r in model.requests)


@pytest.mark.parametrize("code", ["tool_failed", "rag_search_failed"])
async def test_read_only_operational_failure_is_an_error_observation(code):
    tool = ReadTool(failure=PortError(load_settings().error(code)))
    model = ScriptedModel(
        action(), DecisionProposal(kind="answer", answer="source unavailable")
    )
    agent, sink = harness(model, tool)
    result = await agent.run(RunInput(text="work"))
    assert result.status == "completed" and len(tool.calls) == 1
    receipt = [
        i for i in model.requests[-1].input.perceived.items if i.type == "tool_result"
    ][0]
    assert receipt.body["outcome"] == "error" and receipt.body["error"]["code"] == code
    assert any(
        e.module == "action" and e.status == "failed" and e.error_code == code
        for e in sink.events
    )


async def test_generic_tool_exception_does_not_disclose_exception_text():
    tool = ReadTool(failure=RuntimeError("private connection/password detail"))
    model = ScriptedModel(
        action(), DecisionProposal(kind="answer", answer="unavailable")
    )
    agent, _ = harness(model, tool)
    result = await agent.run(RunInput(text="work"))
    assert result.status == "completed"
    assert "private connection" not in model.requests[-1].model_dump_json()


async def test_configured_error_text_is_used_instead_of_adapter_diagnostics():
    error = load_settings().error("tool_failed")
    error.message = "private connection/password detail"
    tool = ReadTool(failure=PortError(error))
    model = ScriptedModel(
        action(), DecisionProposal(kind="answer", answer="unavailable")
    )
    agent, _ = harness(model, tool)
    assert (await agent.run(RunInput(text="work"))).status == "completed"
    assert "private connection" not in model.requests[-1].model_dump_json()


@pytest.mark.parametrize("read_only,expected", [(True, "completed"), (False, "failed")])
async def test_timeout_recovery_requires_explicit_read_only_metadata(
    read_only, expected
):
    settings = load_settings()
    settings.runtime.action_timeout_seconds = 0.02
    tool = ReadTool(slow=True)
    tool.spec.read_only = read_only
    model = ScriptedModel(
        action(), DecisionProposal(kind="answer", answer="source timed out")
    )
    agent, _ = harness(model, tool, settings=settings)
    result = await agent.run(RunInput(text="work"))
    assert result.status == expected and len(tool.calls) == 1
    if read_only:
        assert model.requests[-1].input.perceived.records[-2].source == "action"
    else:
        assert result.error.code == "timeout" and len(model.requests) == 1


@pytest.mark.parametrize(
    "failure,corrupt,code",
    [
        (None, True, "missing_output"),
        ("memory_scope", False, "memory_scope"),
        ("invalid_context", False, "invalid_context"),
    ],
)
async def test_broken_scope_or_receipt_remains_fatal(failure, corrupt, code):
    tool = ReadTool(
        corrupt=corrupt,
        failure=PortError(load_settings().error(failure)) if failure else None,
    )
    model = ScriptedModel(action())
    agent, _ = harness(model, tool)
    result = await agent.run(RunInput(text="work"))
    assert result.status == "failed" and result.error.code == code
    assert len(model.requests) == 1


async def test_duplicate_action_ids_do_not_execute_twice():
    tool = ReadTool()
    request = ActionRequest(tool_name=tool.spec.name, arguments={})
    model = ScriptedModel(
        DecisionProposal(kind="action", action_requests=[request, request]),
        DecisionProposal(kind="answer", answer="corrected decision"),
    )
    agent, _ = harness(model, tool)
    result = await agent.run(RunInput(text="work"))
    assert result.status == "completed" and tool.calls == []


async def test_resume_rebuilds_repetition_state_from_real_receipts():
    tool = ReadTool()
    model = ScriptedModel(
        action(),
        action(),
        DecisionProposal(kind="needs_input", missing_inputs=["next?"]),
    )
    agent, _ = harness(model, tool)
    paused = await agent.run(RunInput(text="work"), session_id="s", run_id="r")
    assert paused.status == "needs_input"
    resumed_model = ScriptedModel(
        action(), DecisionProposal(kind="answer", answer="use previous receipt")
    )
    resumed, _ = harness(resumed_model, tool)
    result = await resumed.run(
        RunInput(text="continue"),
        session_id="s",
        run_id="r",
        recovery=paused.execution_state,
    )
    assert result.status == "completed" and len(tool.calls) == 2
    receipts = result.execution_state["tool_results"]
    assert receipts[-1]["error"]["code"] == "tool_repeated"


async def test_batched_error_and_success_reach_actual_provider_as_complete_pairs():
    sent = []
    settings = load_settings()
    tool = ReadTool()

    def reply(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if len(sent) == 1:
            calls = [
                {
                    "id": name,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                }
                for name, tool_name in [
                    ("bad", "missing_tool"),
                    ("good", tool.spec.name),
                ]
            ]
            message, reason = {"tool_calls": calls}, "tool_calls"
        else:
            results = [m for m in packet["messages"] if m["role"] == "tool"]
            assert [m["tool_call_id"] for m in results] == ["bad", "good"]
            bad, good = [json.loads(m["content"]) for m in results]
            assert bad["outcome"] == "error" and bad["error"]["code"] == "unknown_tool"
            assert good["value"] == "same observation"
            message, reason = (
                {"content": "One source read; the other tool was unavailable."},
                "stop",
            )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": reason, "message": message}]}
        )

    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(reply),
    ) as model:
        agent, _ = harness(model, tool)
        result = await agent.run(RunInput(text="work"))
    assert result.status == "completed" and len(sent) == 2 and len(tool.calls) == 1


async def test_actual_material_navigation_distinguishes_empty_and_invalid_cursor(
    tmp_path,
):
    settings = load_settings()
    settings.materials.cache_root = str(tmp_path / "cache")
    path = tmp_path / "record.txt"
    path.write_text("The listed ship is Sea Breeze.", encoding="utf-8")
    catalog = MaterialCatalog(settings)
    await catalog.ingest(
        [MaterialInput(uri=str(path), material_id="record")], run_id="r"
    )
    search, read = (
        MaterialSearchTool(catalog, settings),
        MaterialReadTool(catalog, settings),
    )
    empty = await search.execute_context(
        ModuleRequest(
            run_id="r",
            call_id="s",
            input=ActionRequest(
                tool_name=search.spec.name, arguments={"query": "ZZZ-MISSING-999"}
            ),
        )
    )
    assert empty.outcome == "empty" and empty.result["status"] == "no_match"
    invalid = await read.execute_context(
        ModuleRequest(
            run_id="r",
            call_id="t",
            input=ActionRequest(
                tool_name=read.spec.name,
                arguments={"material_id": "record", "cursor": "not-a-cursor"},
            ),
        )
    )
    assert invalid.outcome == "error" and invalid.result["status"] == "invalid_cursor"
    assert empty.result["matches"] == []


@pytest.mark.parametrize("wire_truncated", [False, True])
async def test_rag_relevance_filtering_is_not_a_partial_operation(wire_truncated):
    from backend.contracts.modules.dynamic_rag import RagOutput
    from backend.contracts.schemas.core import ModuleResponse
    from backend.infrastructure.tools.rag import RagSearchTool
    from backend.tests.unit.test_dynamic_rag import evidence

    item = evidence()
    settings = load_settings()
    if wire_truncated:
        item.chunk_text = "source text " * 2000
        settings.rag_tool.result_budget_chars = 1500

    class Retrieval:
        async def handle(self, request):
            return ModuleResponse(
                status="ok",
                output=RagOutput(
                    items=[item],
                    omitted_ids=["irrelevant candidate"],
                    sufficiency="sufficient",
                    stop_reason="sufficient",
                    elapsed_seconds=0,
                ),
            )

    tool = RagSearchTool(Retrieval(), settings)
    result = await tool.execute_context(
        ModuleRequest(
            run_id="r",
            call_id="read",
            input=ActionRequest(
                tool_name=tool.spec.name, arguments={"query": "lookup"}
            ),
        )
    )
    assert result.outcome == ("partial" if wire_truncated else "ok")
    if not wire_truncated:
        assert result.result["sufficiency"] == "sufficient" and result.result["items"]


@pytest.mark.parametrize("outcome", ["partial", "empty", "error"])
async def test_non_success_observations_keep_data_in_actual_model_messages(outcome):
    tool = ReadTool()

    async def execute(request):
        return ActionResult(
            tool_name=request.tool_name,
            action_id=request.action_id,
            outcome=outcome,
            result={"excerpt": "already obtained text"},
        )

    tool.execute = execute
    sent = []

    def reply(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if len(sent) == 1:
            message = {
                "tool_calls": [
                    {
                        "id": "read",
                        "type": "function",
                        "function": {"name": tool.spec.name, "arguments": "{}"},
                    }
                ]
            }
            reason = "tool_calls"
        else:
            receipt = next(m for m in packet["messages"] if m["role"] == "tool")
            observed = json.loads(receipt["content"])
            assert observed["outcome"] == outcome
            assert observed["data"]["excerpt"] == "already obtained text"
            message, reason = (
                {"content": "Only the supported part is delivered."},
                "stop",
            )
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": reason}]}
        )

    async with DeepSeekModel(
        load_settings(),
        SecretStr("offline"),
        MemoryEventSink(),
        transport=httpx.MockTransport(reply),
    ) as model:
        agent, _ = harness(model, tool)
        result = await agent.run(RunInput(text="work"))
    assert result.status == "completed" and len(sent) == 2
