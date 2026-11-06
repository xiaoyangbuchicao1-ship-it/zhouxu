"""Responses protocol adapter reusing the established model and review contracts."""

import json
from collections import OrderedDict
from contextvars import ContextVar
from time import perf_counter

import httpx

from backend.config.relay import RelayConfig
from backend.infrastructure.model.deepseek import DeepSeekModel


class ResponsesTransport(httpx.AsyncBaseTransport):
    """Reuse the existing model/reviewer via a tested protocol translation.

    Explicit selected context is replayed, with no previous_response_id or
    server-side conversation hiding information from Perception's budget.
    Provider reasoning continuation items stay in RAM, never in review files.
    """

    def __init__(
        self,
        config: RelayConfig,
        *,
        transport=None,
        max_cached_runs=1,
        collect_metrics=False,
    ):
        self.config = config
        self.inner = transport or httpx.AsyncHTTPTransport()
        self.calls = []
        self.collect_metrics = collect_metrics
        self.max_cached_runs = max_cached_runs
        self.scope = ContextVar("responses_run_id", default=None)
        self.contexts = OrderedDict()
        self.budget = None

    @property
    def reasoning_for_call(self):
        scope = self.scope.get()
        if scope not in self.contexts:
            self.contexts[scope] = {}
        self.contexts.move_to_end(scope)
        while len(self.contexts) > self.max_cached_runs:
            self.contexts.popitem(last=False)
        return self.contexts[scope]

    def reset_context(self):
        self.contexts.clear()

    def translate(self, packet):
        inputs, inserted = [], set()
        for message in packet["messages"]:
            role = message["role"]
            if role == "tool":
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": message["content"],
                    }
                )
                continue
            if message.get("content") is not None:
                inputs.append({"role": role, "content": message["content"]})
            for call in message.get("tool_calls", []):
                for item in self.reasoning_for_call.get(call["id"], []):
                    identity = item.get("id") or json.dumps(item, sort_keys=True)
                    if identity not in inserted:
                        inputs.append(item)
                        inserted.add(identity)
                inputs.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["function"]["name"],
                        "arguments": call["function"]["arguments"],
                    }
                )
        payload = {
            "model": self.config.model,
            "input": inputs,
            "reasoning": {"effort": self.config.effort},
            "max_output_tokens": packet["max_tokens"],
            "store": False,
            "stream": False,
            "include": ["reasoning.encrypted_content"],
        }
        if packet.get("tools"):
            payload["tools"] = [
                {"type": "function", **tool["function"], "strict": False}
                for tool in packet["tools"]
            ]
            if packet.get("tool_choice"):
                payload["tool_choice"] = packet["tool_choice"]
        if packet.get("response_format"):
            payload["text"] = {"format": packet["response_format"]}
        return payload

    def convert(self, data):
        outputs = data.get("output", [])
        reasoning = [item for item in outputs if item.get("type") == "reasoning"]
        calls, texts = [], []
        for item in outputs:
            if item.get("type") == "function_call":
                call_id = item["call_id"]
                self.reasoning_for_call[call_id] = reasoning
                calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": item["arguments"],
                        },
                    }
                )
            elif item.get("type") == "message":
                texts.extend(
                    part["text"]
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                )
        finish = (
            "length"
            if data.get("status") != "completed"
            else "tool_calls"
            if calls
            else "stop"
        )
        return {
            "choices": [
                {
                    "finish_reason": finish,
                    "message": {
                        "role": "assistant",
                        "content": "\n".join(texts) or None,
                        "tool_calls": calls,
                    },
                }
            ],
            "usage": data.get("usage", {}),
        }

    async def handle_async_request(self, request):
        if request.url != httpx.URL(self.config.endpoint):
            raise ValueError("relay_request_target_mismatch")
        packet = json.loads(request.content)
        body = self.translate(packet)
        if self.budget is None:
            raise ValueError("relay_budget_not_configured")
        self.budget.check(body)
        started = perf_counter()
        entry = {
            "purpose": "decision" if packet.get("tools") else "evidence_review",
            "effort": self.config.effort,
            "max_output_tokens": body["max_output_tokens"],
        }
        try:
            forwarded = httpx.Request(
                "POST",
                self.config.endpoint,
                headers={"Authorization": request.headers["Authorization"]},
                json=body,
                extensions=request.extensions,
            )
            response = await self.inner.handle_async_request(forwarded)
            try:
                await response.aread()
                entry["http_status"] = response.status_code
                if not response.is_success:
                    # Do not echo third-party error bodies or authentication data.
                    return httpx.Response(
                        response.status_code, json={"error": "relay_request_failed"}
                    )
                data = response.json()
                usage = data.get("usage") or {}
                entry.update(
                    {
                        "response_model": data.get("model"),
                        "status": data.get("status"),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "reasoning_tokens": (
                            usage.get("output_tokens_details") or {}
                        ).get("reasoning_tokens"),
                        "output_types": [
                            item.get("type") for item in data.get("output", [])
                        ],
                    }
                )
                return httpx.Response(200, json=self.convert(data))
            finally:
                await response.aclose()
        except httpx.RequestError as exc:
            entry["transport_error"] = type(exc).__name__
            raise
        finally:
            entry["seconds"] = perf_counter() - started
            if self.collect_metrics:
                self.calls.append(entry)

    async def aclose(self):
        await self.inner.aclose()
        self.reset_context()


class ResponsesModel(DeepSeekModel):
    """Keep continuation state isolated by trusted run ID, not provider call ID."""

    def __init__(self, settings, key, events, *, transport: ResponsesTransport):
        super().__init__(settings, key, events, transport=transport)
        self.responses_transport = transport
        transport.budget = self.budget

    def estimate_context(self, items, tools, *, run_id):
        from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
        from backend.contracts.modules.perception import ContextAssembly
        from backend.contracts.schemas.core import ModuleRequest

        token = self.responses_transport.scope.set(run_id)
        try:
            packet = self._payload(
                ModuleRequest(
                    run_id=run_id,
                    call_id="context-estimate",
                    input=ReasoningInput(
                        perceived=PerceivedContext(
                            run_id=run_id, items=items, assembly=ContextAssembly()
                        ),
                        tools=tools,
                    ),
                )
            )
            return self.budget.estimate(self.responses_transport.translate(packet))
        finally:
            self.responses_transport.scope.reset(token)

    async def decide(self, request):
        transport = self.responses_transport
        token = transport.scope.set(request.run_id)
        keep = False
        try:
            decision = await super().decide(request)
            keep = decision.kind == "action"
            return decision
        finally:
            if not keep:
                transport.contexts.pop(request.run_id, None)
            transport.scope.reset(token)

    async def decide_stream(self, request, on_delta):
        """The relay currently returns one Responses document; use safe fallback."""

        return await self.decide(request)
