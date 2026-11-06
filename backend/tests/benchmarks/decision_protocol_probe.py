"""Isolated paid A/B probe. Production code never imports this adapter.

The closed variant uses real native tool calls, including a delivery control
operation. Its JSON argument text is projected to ordinary answer deltas using
the already installed pydantic-core partial JSON reader. No regex classifies
business text. Expected decisions are local oracles, never model inputs.
"""

import argparse
import asyncio
import hashlib
import json
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from backend.bootstrap import build_harness, open_rag
from backend.config.settings import load_key, load_settings
from backend.contracts.events.runtime import RunEvent
from backend.contracts.events.streaming import visible_text
from backend.contracts.modules.loop import DecisionProposal, RunInput, ToolSpec
from backend.contracts.modules.materials import MaterialInput
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.decision import DeliveryText as ArgumentText
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.public_text import PublicText
from backend.infrastructure.observability.memory import MemoryEventSink

CONFIG = Path(__file__).with_name("decision_protocol_cases.json")


class ProbeModel(DeepSeekModel):
    def __init__(self, settings, key, events, *, mode, config, transport=None):
        self.mode, self.config, self.calls = mode, config, []
        super().__init__(settings, key, events, transport=transport)

    def _messages(self, request):
        messages = super()._messages(request)
        if self.mode == "closed" and request.input.purpose != "compaction":
            messages[0]["content"] += "\n" + self.config["protocol_instruction"].format(
                finish_tool=self.config["finish_tool"],
                clarification_tool=self.settings.model.clarification_tool,
            )
        return messages

    def _tools(self, value):
        tools = super()._tools(value)
        if self.mode == "closed" and value.purpose != "compaction":
            if value.purpose == "finalization":
                tools = super()._tools(
                    value.model_copy(update={"purpose": "main", "tools": []})
                )
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": self.config["finish_tool"],
                        "description": self.config["finish_description"],
                        "parameters": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return tools

    def _payload(self, request):
        packet = super()._payload(request)
        if self.mode == "closed" and request.input.purpose != "compaction":
            packet["tool_choice"] = "required"
        return packet

    def parse_decision(self, message, finish_reason, *, purpose="main"):
        if self.mode != "closed" or purpose == "compaction":
            return super()._decision_from_message(message, finish_reason)
        calls = message.get("tool_calls") or []
        terminal = [
            c for c in calls if c["function"]["name"] == self.config["finish_tool"]
        ]
        if terminal:
            if len(calls) != 1:
                raise ValueError("mixed_terminal_and_action")
            args = json.loads(terminal[0]["function"]["arguments"])
            if (
                set(args) != {"text"}
                or not isinstance(args["text"], str)
                or not args["text"].strip()
            ):
                raise ValueError("invalid_delivery_arguments")
            if finish_reason not in {"tool_calls", "stop"}:
                return DecisionProposal(
                    kind="answer",
                    answer=args["text"],
                    incomplete=True,
                    finish_reason=finish_reason,
                )
            return DecisionProposal(kind="answer", answer=args["text"])
        if finish_reason not in {"tool_calls", "stop"}:
            raise ValueError("incomplete_control_operation")
        if not calls:
            raise ValueError("plain_text_not_a_closed_decision")
        if any(
            c["function"]["name"] == self.settings.model.clarification_tool
            for c in calls
        ):
            if len(calls) != 1:
                raise ValueError("mixed_wait_and_other_operation")
            args = json.loads(calls[0]["function"]["arguments"])
            questions = args.get("questions")
            # A single question has an unambiguous list representation; never
            # infer questions by searching the generated answer text.
            if isinstance(questions, str):
                questions = [questions]
            if (
                not isinstance(questions, list)
                or not questions
                or not all(isinstance(q, str) and q.strip() for q in questions)
            ):
                raise ValueError("invalid_wait_questions")
            return DecisionProposal(kind="needs_input", missing_inputs=questions)
        return super()._decision_from_message(message, finish_reason)

    async def decide_stream(self, request, on_delta):
        packet = self._payload(request)
        packet.update(stream=True, stream_options={"include_usage": True})
        entry = {
            "run_id": request.run_id,
            "purpose": request.input.purpose,
            "packet": packet,
            "wire_deltas": [],
            "http_status": None,
        }
        self.calls.append(entry)
        began, usage, status = perf_counter(), None, "failed"
        content, calls, readers = [], {}, {}
        reason = None
        public = PublicText(request.input.perceived.items)

        async def emit(text):
            visible = public.push(text)
            if visible:
                entry.setdefault(
                    "first_visible_seconds", round(perf_counter() - began, 4)
                )
                entry.setdefault("visible_chunks", []).append(visible)
                await on_delta(visible)

        try:
            async with self.client.stream(
                "POST", self.settings.model.endpoint, json=packet
            ) as response:
                entry["http_status"] = response.status_code
                if response.status_code == 402:
                    raise PortError(self.settings.error("model_quota_exhausted"))
                if response.status_code == 429 or response.status_code >= 500:
                    raise PortError(
                        self.settings.error("model_unavailable", retryable=True)
                    )
                if not response.is_success:
                    raise PortError(self.settings.error("model_rejected"))
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ValueError("stream_not_supplied")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    data = json.loads(raw)
                    entry.setdefault(
                        "first_sse_seconds", round(perf_counter() - began, 4)
                    )
                    if isinstance(data.get("usage"), dict):
                        usage = data["usage"]
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    reason = choice.get("finish_reason") or reason
                    delta = choice.get("delta") or {}
                    # Never collect reasoning_content or response headers.
                    entry["wire_deltas"].append(
                        {
                            "seconds": round(perf_counter() - began, 4),
                            "content": delta.get("content"),
                            "tool_calls": delta.get("tool_calls"),
                            "finish_reason": choice.get("finish_reason"),
                        }
                    )
                    text = delta.get("content")
                    if isinstance(text, str):
                        content.append(text)
                        if (
                            self.mode == "current"
                            or request.input.purpose == "compaction"
                        ):
                            await emit(text)
                    for part in delta.get("tool_calls") or []:
                        index = int(part.get("index", 0))
                        call = calls.setdefault(
                            index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if part.get("id"):
                            call["id"] = part["id"]
                        function = part.get("function") or {}
                        call["function"]["name"] += function.get("name") or ""
                        call["function"]["arguments"] += function.get("arguments") or ""
                        if (
                            self.mode == "closed"
                            and call["function"]["name"] == self.config["finish_tool"]
                        ):
                            arguments = call["function"]["arguments"]
                            if arguments.strip():
                                await emit(
                                    readers.setdefault(index, ArgumentText()).push(
                                        arguments
                                    )
                                )
            message = {"content": "".join(content) or None}
            if calls:
                message["tool_calls"] = [calls[i] for i in sorted(calls)]
            entry["native_message"] = message
            decision = self.parse_decision(
                message, reason, purpose=request.input.purpose
            )
            tail = public.push("", final=True)
            if tail:
                entry.setdefault("visible_chunks", []).append(tail)
                await on_delta(tail)
            if decision.answer:
                decision.answer = public.render(decision.answer)
            entry["decision"] = decision.model_dump(mode="json")
            status = "ok"
            return decision
        except PortError as exc:
            entry["error"] = exc.error.code
            raise
        except asyncio.CancelledError:
            entry["error"] = "cancelled_or_timeout"
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            entry["error"] = type(exc).__name__
            raise PortError(self.settings.error("model_invalid_response")) from None
        finally:
            entry["seconds"] = round(perf_counter() - began, 4)
            entry["usage"] = usage
            self.events.emit(
                RunEvent(
                    run_id=request.run_id,
                    call_id=request.call_id,
                    module="model",
                    phase="model",
                    status=status,
                    elapsed_seconds=perf_counter() - began,
                    total_tokens=(usage or {}).get("total_tokens"),
                )
            )


class FaultFixture:
    """Explicit fault fixture. No real maritime integration is represented."""

    def __init__(self, settings, config):
        self.settings, self.config = settings, config
        self.spec = ToolSpec(
            name=config["fault_tool_name"],
            description=config["fault_tool_description"],
            parameters={
                "type": "object",
                "properties": {"record_id": {"type": "string"}},
                "required": ["record_id"],
            },
            read_only=True,
        )

    async def execute(self, request):
        raise PortError(self.settings.error("tool_failed"))


@asynccontextmanager
async def probe_resources(settings, sink, config, with_rag):
    async with AsyncExitStack() as resources:
        current = await resources.enter_async_context(
            ProbeModel(
                settings, load_key(settings), sink, mode="current", config=config
            )
        )
        closed = await resources.enter_async_context(
            ProbeModel(settings, load_key(settings), sink, mode="closed", config=config)
        )
        rag = (
            await resources.enter_async_context(
                open_rag(settings=settings, events=sink)
            )
            if with_rag
            else None
        )
        yield current, closed, rag


async def run(args, directory):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    settings = load_settings()
    if (
        settings.model.provider != "deepseek"
        or settings.model.thinking_enabled
    ):
        raise RuntimeError(
            "Use the existing non-thinking speed profile; no configuration was changed."
        )
    settings.materials.cache_root = str(directory / "parsed")
    files = {}
    for name, text in config["materials"].items():
        files[name] = directory / f"{name}.txt"
        files[name].write_text(text, encoding="utf-8")
    files["long"] = directory / "long.txt"
    files["long"].write_text(
        config["long_prefix"] * config["long_repetitions"] + config["long_suffix"],
        encoding="utf-8",
    )
    files["scope"] = directory / "records.txt"
    files["scope"].write_text(
        "\n".join(f"{i}.记录状态：已登记" for i in range(1, 21)), encoding="utf-8"
    )
    selected = set(args.cases.split(",")) if args.cases else None
    cases = [c for c in config["cases"] if not selected or c["id"] in selected]
    if not cases or selected and selected - {c["id"] for c in cases}:
        raise ValueError("Unknown case selection")
    sink = MemoryEventSink()
    results = []
    experiment = {
        "model": settings.model.name,
        "thinking": settings.model.thinking_enabled,
        "production_changed": False,
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "with_rag": args.with_rag,
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "results": results,
        "scope": "Isolated tools/stream/control experiment; RAG supplied only with --with-rag; no web UI changes.",
    }
    output = Path(args.output)

    def save():
        output.write_text(
            json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    setup_start = perf_counter()
    async with probe_resources(settings, sink, config, args.with_rag) as (
        current,
        closed,
        rag,
    ):
        experiment["setup_seconds"] = round(perf_counter() - setup_start, 4)
        models = {"current": current, "closed": closed}
        for repeat in range(args.repeats):
            for index, case in enumerate(cases):
                modes = (
                    ["current", "closed"]
                    if (repeat + index) % 2 == 0
                    else ["closed", "current"]
                )
                for mode in modes:
                    model = models[mode]
                    agent_settings = settings.model_copy(deep=True)
                    if case.get("fault"):
                        agent_settings.messages["tool_failed"] = config[
                            "fault_user_error"
                        ]
                    fault = FaultFixture(agent_settings, config)
                    agent = build_harness(
                        model=model,
                        rag=rag,
                        settings=agent_settings,
                        events=sink,
                        streams=sink,
                        tools={fault.spec.name: (fault.spec, fault)}
                        if case.get("fault")
                        else None,
                    )
                    session = f"protocol-probe:{uuid4().hex}"
                    turns = [case["question"]] + (
                        [case["followup"]] if case.get("followup") else []
                    )
                    for turn, text in enumerate(turns, 1):
                        start, call_start, stream_start = (
                            perf_counter(),
                            len(model.calls),
                            len(sink.stream_events),
                        )
                        result = await agent.run(
                            RunInput(
                                text=text,
                                materials=[
                                    MaterialInput(
                                        uri=str(files[f]), material_id=f"probe-{f}"
                                    )
                                    for f in case.get("materials", [])
                                ]
                                if turn == 1
                                else [],
                            ),
                            session_id=session,
                        )
                        calls = model.calls[call_start:]
                        streams = sink.stream_events[stream_start:]
                        row = {
                            "case": case["id"],
                            "repeat": repeat + 1,
                            "turn": turn,
                            "mode": mode,
                            "question": text,
                            "expected": case["expected"],
                            "criterion": case["criterion"],
                            "status": result.status,
                            "answer": result.answer,
                            "questions": result.missing_inputs,
                            "error": result.error.code if result.error else None,
                            "seconds": round(perf_counter() - start, 4),
                            "calls": calls,
                            "tools": result.execution_state.get("tool_results", []),
                            "decision_matches": result.status
                            == (
                                "needs_input"
                                if case["expected"] == "needs_input"
                                else "completed"
                            ),
                            "stream_matches": visible_text(streams)
                            == (result.answer or ""),
                            "tokens": sum(
                                (c.get("usage") or {}).get("total_tokens", 0)
                                for c in calls
                            )
                            if calls and all(c.get("usage") for c in calls)
                            else None,
                        }
                        results.append(row)
                        save()
                        print(
                            json.dumps(
                                {
                                    k: row[k]
                                    for k in [
                                        "case",
                                        "repeat",
                                        "turn",
                                        "mode",
                                        "status",
                                        "seconds",
                                        "decision_matches",
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        if (
                            result.status == "failed"
                            and calls
                            and all(
                                c.get("http_status") in {402, 429}
                                or (c.get("http_status") or 0) >= 500
                                or c.get("error")
                                in {
                                    "ReadTimeout",
                                    "ConnectError",
                                    "cancelled_or_timeout",
                                }
                                for c in calls
                            )
                        ):
                            experiment["stopped"] = "provider_unavailable"
                            save()
                            return False
    experiment["stopped"] = "collected_requires_subjective_review"
    save()
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--cases")
    parser.add_argument(
        "--with-rag",
        action="store_true",
        help="Include the real configured law tool and retrieval service",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.live or args.repeats < 1:
        parser.error("Explicit --live and positive --repeats are required")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(".ai_tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="decision-protocol-", dir=".ai_tmp"
    ) as directory:
        complete = asyncio.run(run(args, Path(directory)))
    if not complete:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
