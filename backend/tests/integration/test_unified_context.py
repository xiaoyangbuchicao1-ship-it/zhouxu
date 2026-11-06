"""Run/context regressions. Scripted transports are offline, not model quality tests."""

import asyncio
import json

import httpx
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.events.streaming import StreamEvent, visible_text
from backend.contracts.model_context import DeploymentLimits
from backend.contracts.modules.loop import ActionResult, RunInput, ToolSpec
from backend.contracts.modules.materials import MaterialInput, MaterialReadInput
from backend.contracts.modules.memory import MemoryRecord
from backend.harness.lifecycle.control import RunControl
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.infrastructure.parsing.read_view import read_view
from backend.infrastructure.parsing.tests.test_material_context import document


def completion(content=None, calls=None):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "tool_calls" if calls else "stop",
                    "message": {"content": content, "tool_calls": calls or []},
                }
            ]
        },
    )


def call(identity, query):
    return {
        "id": identity,
        "type": "function",
        "function": {
            "name": "lookup",
            "arguments": json.dumps({"query": query}),
        },
    }


SPEC = ToolSpec(
    name="lookup",
    description="Read fixture facts",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
    },
)


async def test_assistant_text_with_tools_reaches_next_model_turn():
    sent = []

    def transport(request):
        sent.append(json.loads(request.content))
        return (
            completion("已整理第一部分，接下来核对第二部分。", [call("a", "second")])
            if len(sent) == 1
            else completion("两部分结果已经汇总。")
        )

    class Tool:
        async def execute(self, request):
            return ActionResult(
                tool_name="lookup",
                action_id=request.action_id,
                result={"text": "第二部分资料"},
            )

    settings, events = load_settings(), MemoryEventSink()
    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        harness = build_harness(
            model=model,
            settings=settings,
            events=events,
            tools={"lookup": (SPEC, Tool())},
        )
        result = await harness.run(RunInput(text="整理两部分内容"))
    assert result.status == "completed"
    previous = next(m for m in sent[1]["messages"] if m.get("tool_calls"))
    assert previous["content"] == "已整理第一部分，接下来核对第二部分。"


async def test_distinct_quota_rejections_do_not_look_like_duplicate_evidence():
    sent = []

    def transport(request):
        sent.append(json.loads(request.content))
        return (
            completion(calls=[call("a", "one"), call("b", "two")])
            if len(sent) == 1
            else completion("已有结果可用，其余尚未核实。")
        )

    class Tool:
        async def execute(self, request):
            return ActionResult(
                tool_name="lookup",
                action_id=request.action_id,
                result={
                    "status": "failed",
                    "items": [],
                    "query": request.arguments["query"],
                    "stop_reason": "tool_call_limit",
                    "error": {"code": "limit", "message": "No remaining allowance"},
                },
            )

    settings, events = load_settings(), MemoryEventSink()
    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        result = await build_harness(
            model=model,
            settings=settings,
            events=events,
            tools={"lookup": (SPEC, Tool())},
        ).run(RunInput(text="分别处理两项"))
    assert result.status == "completed"
    assert len([m for m in sent[1]["messages"] if m["role"] == "tool"]) == 2


def test_short_document_has_full_continuous_view_despite_many_empty_paragraphs():
    catalog = MaterialCatalog(load_settings())
    parsed = document(*(["", "正文"] * 160), "最终位置的资料")
    catalog.bind(parsed, run_id="many-paragraphs")
    view = catalog.candidates("many-paragraphs")[0].body
    text = view.get("text", "") or "".join(u["text"] for u in view.get("units", []))
    assert text.endswith("最终位置的资料")
    assert not view.get("truncated")


def test_tail_read_is_bounded_and_keeps_source_offsets():
    parsed = document("前文" * 500, "末尾资料" * 50)
    result = read_view(
        parsed,
        MaterialReadInput(material_id=parsed.manifest.material_id, position="end"),
        limit=19,
        overview_limit=4,
    )
    assert "".join(unit.text for unit in result.units) == parsed.units[-1].text[-19:]
    assert result.coverage[0]["char_start"] == len(parsed.units[-1].text) - 19
    assert result.next_cursor is None
    assert result.notices


async def test_short_preview_can_answer_without_forcing_available_tools(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("完整材料：今日共有七艘船。", encoding="utf-8")
    settings, events = load_settings(), MemoryEventSink()
    settings.materials.cache_root = str(tmp_path / "cache")

    def transport(request):
        packet = json.loads(request.content)
        assert {"read_material", "search_material"} <= {
            item["function"]["name"] for item in packet["tools"]
        }
        assert "今日共有七艘船" in json.dumps(packet, ensure_ascii=False)
        return completion("七艘")

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(text="材料中有几艘船？", materials=[MaterialInput(uri=str(path))])
        )
    assert result.status == "completed"


async def test_tool_result_checkpoint_exists_before_following_model_call():
    snapshots, sent = [], []

    async def checkpoint(state):
        snapshots.append(state)

    def transport(request):
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            return completion(calls=[call("a", "facts")])
        assert any(s["tool_results"] for s in snapshots)
        return completion("结果")

    class Tool:
        async def execute(self, request):
            return ActionResult(
                tool_name="lookup",
                action_id=request.action_id,
                result={"text": "已取得的事实"},
            )

    settings, events = load_settings(), MemoryEventSink()
    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        harness = build_harness(
            model=model,
            settings=settings,
            events=events,
            tools={"lookup": (SPEC, Tool())},
        )
        result = await harness.run(RunInput(text="取资料并回答"), checkpoint=checkpoint)
    assert result.status == "completed"


async def test_long_paste_within_model_budget_is_not_replaced_by_preview():
    sent = []
    text = "资料正文。" * 6500 + "\n末尾暗号：云帆六二九。请给出暗号。"

    def transport(request):
        sent.append(json.loads(request.content))
        return completion("云帆六二九")

    settings, events = load_settings(), MemoryEventSink()
    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(text=text)
        )
    assert result.status == "completed" and len(sent) == 1
    assert any(text in m.get("content", "") for m in sent[0]["messages"])
    assert "pasted-" not in json.dumps(sent[0], ensure_ascii=False)


async def test_quota_failure_is_explicit_and_does_not_retry():
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(402, json={"error": {"message": "insufficient balance"}})

    settings, events = load_settings(), MemoryEventSink()
    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(text="你好")
        )
    assert result.status == "failed" and result.error.code == "model_quota_exhausted"
    assert len(calls) == 1


async def test_pressure_uses_readable_material_reference_then_continuous_tail(tmp_path):
    settings, events, sent = load_settings(), MemoryEventSink(), []
    settings.deployment_limits[settings.model.name] = DeploymentLimits(
        total_tokens=64000
    )
    settings.materials.cache_root = str(tmp_path / "cache")
    material = tmp_path / "long.txt"
    material.write_text(
        "普通记录。\n" * 20000 + "最后暗号是：白鹭九七三", encoding="utf-8"
    )

    def transport(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if len(sent) == 1:
            resource = next(
                json.loads(m["content"])["resource"]
                for m in packet["messages"]
                if '"resource"' in (m.get("content") or "")
            )
            assert resource.get("truncated") and resource.get("material_id")
            return completion(
                calls=[
                    {
                        "id": "read-tail",
                        "type": "function",
                        "function": {
                            "name": "read_material",
                            "arguments": json.dumps(
                                {
                                    "material_id": resource["material_id"],
                                    "position": "end",
                                }
                            ),
                        },
                    }
                ]
            )
        assert "白鹭九七三" in json.dumps(packet, ensure_ascii=False)
        return completion("白鹭九七三")

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(
                text="找出文件末尾的暗号", materials=[MaterialInput(uri=str(material))]
            )
        )
    assert result.status == "completed", result.error
    assert len(sent) == 2
    assert all(
        e.diagnostics["used_tokens"] <= e.diagnostics["budget"]["warning"]
        for e in events.events
        if e.module == "perception" and e.phase == "finished"
    )


async def test_pressure_commits_history_summary_before_omitting_originals():
    settings, events, sent = load_settings(), MemoryEventSink(), []
    settings.deployment_limits[settings.model.name] = DeploymentLimits(
        total_tokens=64000
    )
    records = [
        MemoryRecord(
            record_id=f"history-{i}",
            source="user",
            session_id="chat",
            run_id=f"prior-{i}",
            retention="session",
            body={"text": "原约定港口为宁波，随后纠正为上海。" + "背景资料" * 2500},
        )
        for i in range(2)
    ]

    def transport(request):
        packet = json.loads(request.content)
        sent.append(packet)
        if "运行上下文整理器" in packet["messages"][0]["content"]:
            return completion(
                "目的港已从宁波纠正为上海；其他背景材料尚需按原记录核实。"
            )
        assert "目的港已从宁波纠正为上海" in json.dumps(packet, ensure_ascii=False)
        return completion("最新目的港是上海。")

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(transport)
    ) as model:
        result = await build_harness(model=model, settings=settings, events=events).run(
            RunInput(text="最新目的港是什么？"), session_id="chat", history=records
        )
    assert result.status == "completed", result.error
    assert set(result.execution_state["compacted_record_ids"]) == {"history-0"}
    assert len(sent) == 2  # Stop compacting once the complete request fits.
    assert records[1].body["text"] in json.dumps(sent[-1], ensure_ascii=False)


async def test_cancel_during_tool_return_preserves_receipt_and_progress():
    settings, events, control, states = (
        load_settings(),
        MemoryEventSink(),
        RunControl(),
        [],
    )

    class Tool:
        async def execute(self, request):
            control.cancel("stop")
            return ActionResult(
                tool_name="lookup",
                action_id=request.action_id,
                result={"text": "外部操作已完成"},
            )

    async def checkpoint(state):
        states.append(state)

    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        events,
        transport=httpx.MockTransport(lambda _: completion(calls=[call("a", "facts")])),
    ) as model:
        result = await build_harness(
            model=model,
            settings=settings,
            events=events,
            tools={"lookup": (SPEC, Tool())},
        ).run(RunInput(text="执行操作"), control, checkpoint=checkpoint)
    assert result.status == "cancelled"
    assert states[-1]["tool_results"][0]["result"]["text"] == "外部操作已完成"
    assert states[-1]["task_state"]["steps"][0]["status"] == "completed"


async def test_stop_cancels_model_stream_promptly_and_keeps_visible_partial():
    control, settings = RunControl(), load_settings()

    class StopOnText(MemoryEventSink):
        def emit_stream(self, event):
            super().emit_stream(event)
            if event.type == "delta":
                control.stop()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                "data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": "已经生成的答案"}}]},
                    ensure_ascii=False,
                )
                + "\n\n"
            ).encode()
            await asyncio.sleep(30)

    events = StopOnText()
    async with DeepSeekModel(
        settings,
        SecretStr("offline"),
        events,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=SlowStream())
        ),
    ) as model:
        result = await asyncio.wait_for(
            build_harness(
                model=model, settings=settings, events=events, streams=events
            ).run(RunInput(text="长任务"), control),
            timeout=2,
        )
    assert result.status == "cancelled"
    assert result.answer == "已经生成的答案"


def test_visible_stream_revisions_do_not_persist_tool_preambles():
    events = [
        StreamEvent(run_id="r", type="delta", text="先查找"),
        StreamEvent(run_id="r", type="answer", text=""),
        StreamEvent(run_id="r", type="delta", text="正式答案"),
        StreamEvent(run_id="r", type="done", text="正式答案"),
    ]
    assert visible_text(events) == "正式答案"


async def test_durable_history_hydrates_fresh_harness_and_keeps_account_boundary(
    tmp_path,
):
    from datetime import datetime, timezone

    import pytest

    from backend.application.workspace.conversations import (
        WorkspaceError,
        WorkspaceService,
    )
    from backend.infrastructure.database.connection import Database
    from backend.infrastructure.database.models import AccountModel

    settings = load_settings()
    settings.database.url = "sqlite:///" + (tmp_path / "history.db").as_posix()
    database = Database(settings.database)
    database.initialize()
    try:
        now = datetime.now(timezone.utc)
        with database.session() as session:
            session.add(
                AccountModel(
                    id="account",
                    email="history@example.test",
                    password_hash="test-only",
                    display_name="test",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        service = WorkspaceService(database)
        chat = service.create(account_id="account")
        service.record_run(
            account_id="account",
            conversation_id=chat.id,
            run_id="old",
            question="目的港改为上海",
            status="completed",
            answer="已记住最新目的港上海。",
        )
        fresh = WorkspaceService(database)
        history = fresh.context_records(
            account_id="account", conversation_id=chat.id, exclude_run_id="new"
        )
        with pytest.raises(WorkspaceError):
            fresh.context_records(
                account_id="other", conversation_id=chat.id, exclude_run_id="new"
            )

        def transport(request):
            wire = json.loads(request.content)
            assert "目的港改为上海" in json.dumps(wire, ensure_ascii=False)
            assert "历史回答，不是本轮已核验的证据" in json.dumps(
                wire, ensure_ascii=False
            )
            return completion("上海")

        events = MemoryEventSink()
        async with DeepSeekModel(
            settings,
            SecretStr("offline"),
            events,
            transport=httpx.MockTransport(transport),
        ) as model:
            result = await build_harness(
                model=model, settings=settings, events=events
            ).run(
                RunInput(text="现在目的港在哪里？"),
                session_id=chat.id,
                history=history,
                run_id="new",
            )
        assert result.status == "completed"
    finally:
        database.close()


def test_workbench_snapshot_and_sql_question_survive_json_payload_restore(tmp_path):
    from backend.infrastructure.persistence.run_store import JsonRunStore
    from backend.interfaces.api.control_plane import HarnessControlService
    from backend.tests.integration.test_workbench_contract import (
        CompletedExecutor,
        make_client,
        register,
    )

    client, database = make_client(tmp_path)
    store = JsonRunStore(tmp_path / "runtime.json")
    try:
        with client:
            client.app.state.control_service = HarnessControlService(
                CompletedExecutor(), store=store
            )
            headers = register(client)
            cid = client.post(
                "/workspace/conversations", headers=headers, json={}
            ).json()["conversation"]["conversation_id"]
            submitted = client.post(
                f"/workspace/conversations/{cid}/runs",
                headers=headers,
                json={
                    "conversation_id": cid,
                    "text": "重启后保留的问题",
                    "client_request_id": "once",
                },
            )
            assert submitted.status_code == 202
            rid = submitted.json()["run"]["run_id"]
            client.get(
                f"/workspace/conversations/{cid}/events",
                headers=headers,
                params={"run_id": rid},
            )
            client.app.state.control_service = HarnessControlService(
                CompletedExecutor(), store=store
            )
            snapshot = client.get(
                f"/workspace/conversations/{cid}/snapshot", headers=headers
            )
            assert snapshot.status_code == 200, snapshot.text
            assert snapshot.json()["runs"][0]["payload"]["text"] == "重启后保留的问题"
            from backend.infrastructure.database.models import ConversationRunModel

            with database.session() as session:
                assert (
                    session.get(ConversationRunModel, rid).question
                    == "重启后保留的问题"
                )
    finally:
        database.close()
