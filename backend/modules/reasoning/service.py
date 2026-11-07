from backend.config.settings import Settings
from backend.contracts.events.streaming import StreamEvent
from backend.contracts.modules.loop import DecisionProposal, ReasoningInput
from backend.contracts.ports import ModelPort, StreamSink
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse, PortError


class ModelReasoning:
    def __init__(
        self,
        model: ModelPort,
        settings: Settings,
        streams: StreamSink | None = None,
        stream_transform_factory=None,
        answer_transform=None,
    ):
        self.model = model
        self.settings = settings
        self.streams = streams
        self.stream_transform_factory = stream_transform_factory
        self.answer_transform = answer_transform

    async def handle(
        self, request: ModuleRequest[ReasoningInput]
    ) -> ModuleResponse[DecisionProposal]:
        if request.input.perceived.run_id != request.run_id:
            return ModuleResponse(
                status="failed", error=self.settings.error("memory_scope")
            )
        stream_transform = (
            self.stream_transform_factory() if self.stream_transform_factory else None
        )

        def public_text(text: str) -> str:
            return self.answer_transform(text) if self.answer_transform else text

        try:
            if self.streams is not None and request.input.purpose != "compaction":
                self.streams.emit_stream(
                    StreamEvent(
                        type="answer",
                        run_id=request.run_id,
                        text=public_text(request.input.delivered_prefix),
                    )
                )

            async def on_delta(text: str) -> None:
                if stream_transform is not None:
                    text = stream_transform.push(text)
                if self.streams is not None and text:
                    self.streams.emit_stream(
                        StreamEvent(type="delta", run_id=request.run_id, text=text)
                    )

            decide_stream = getattr(self.model, "decide_stream", None)
            if callable(decide_stream):
                decision = await decide_stream(request, on_delta)
            else:
                decision = await self.model.decide(request)
            if stream_transform is not None and self.streams is not None:
                tail = stream_transform.push(final=True)
                if tail:
                    self.streams.emit_stream(
                        StreamEvent(type="delta", run_id=request.run_id, text=tail)
                    )
        except PortError as exc:
            return ModuleResponse(status="failed", error=exc.error)
        valid = (
            decision.kind == "answer"
            and bool((decision.answer or "").strip())
            or decision.kind == "action"
            and bool(decision.action_requests)
            and len({a.action_id for a in decision.action_requests})
            == len(decision.action_requests)
            or decision.kind == "needs_input"
            and any(q.strip() for q in decision.missing_inputs)
        )
        if not valid:
            return ModuleResponse(
                status="failed",
                error=self.settings.error("invalid_decision", retryable=True),
            )
        if self.streams is not None and request.input.purpose != "compaction":
            self.streams.emit_stream(
                StreamEvent(
                    type="answer",
                    run_id=request.run_id,
                    text=public_text(request.input.delivered_prefix)
                    + (
                        public_text(decision.answer or "")
                        if decision.kind == "answer"
                        else ""
                    ),
                )
            )
        return ModuleResponse(status="ok", output=decision)
