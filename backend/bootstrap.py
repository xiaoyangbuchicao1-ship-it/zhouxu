"""Composition root: the only place that connects concrete modules to the runtime."""

from contextlib import AsyncExitStack, asynccontextmanager

from backend.config.settings import Settings
from backend.contracts.modules.loop import ToolSpec
from backend.contracts.ports import (
    ContextualToolExecutor,
    EventSink,
    ModelContextPort,
    ModelPort,
    Module,
    StreamSink,
    ToolExecutor,
)
from backend.contracts.schemas.core import PortError
from backend.harness.runtime.context_recovery import ContextRecovery
from backend.harness.runtime.loop import Harness
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.modules.action.service import RegisteredAction
from backend.modules.memory.service import InMemoryMemory
from backend.modules.perception.service import TextPerception
from backend.modules.reasoning.service import ModelReasoning
from backend.modules.reflection.service import RuleReflection


def build_harness(
    *,
    model: ModelPort,
    settings: Settings,
    events: EventSink,
    streams: StreamSink | None = None,
    tools: dict[str, tuple[ToolSpec, ToolExecutor | ContextualToolExecutor]]
    | None = None,
    rag: Module | None = None,
    model_context: ModelContextPort | None = None,
) -> Harness:
    context_model = model_context or model
    if not isinstance(context_model, ModelContextPort):
        raise PortError(settings.error("context_configuration"))
    registered = dict(tools or {})
    if rag is not None:
        from backend.infrastructure.tools.rag import RagSearchTool

        bridge = RagSearchTool(rag, settings)
        if bridge.spec.name in registered:
            raise ValueError("duplicate_tool_registration")
        registered[bridge.spec.name] = (bridge.spec, bridge)
    catalog = MaterialCatalog(settings)
    from backend.infrastructure.tools.material_read import MaterialReadTool
    from backend.infrastructure.tools.material_search import MaterialSearchTool

    material_tool = MaterialReadTool(catalog, settings)
    for _, executor in registered.values():
        if getattr(executor, 'runtime', None) is not None:
            executor.material_catalog = catalog
    material_search_tool = MaterialSearchTool(catalog, settings)
    if material_tool.spec.name in registered:
        raise ValueError("duplicate_tool_registration")
    registered[material_tool.spec.name] = (material_tool.spec, material_tool)
    if material_search_tool.spec.name in registered:
        raise ValueError("duplicate_tool_registration")
    registered[material_search_tool.spec.name] = (material_search_tool.spec, material_search_tool)
    for name, (spec, _) in registered.items():
        if name != spec.name or name == settings.model.clarification_tool:
            raise ValueError("invalid_tool_registration")
    reflection = RuleReflection(settings)
    if 'search_maritime_corpus' in registered:
        from backend.application.maritime.citations import MaritimeReflection
        reflection = MaritimeReflection(settings, registered['search_maritime_corpus'][1].corpus)
    return Harness(
        execution_runtime=getattr(registered.get('bash', (None,None))[1], 'runtime', None),
        perception=TextPerception(
            settings, model_context=context_model, streams=streams
        ),
        memory=InMemoryMemory(
            max_items=settings.memory.max_recall_items,
            unified_budget=True,
        ),
        reasoning=ModelReasoning(model, settings, streams=streams),
        action=RegisteredAction(
            {name: pair[1] for name, pair in registered.items()}, settings
        ),
        reflection=reflection,
        settings=settings,
        tools=[
            pair[0]
            for name, pair in registered.items()
            if name not in {material_tool.spec.name, material_search_tool.spec.name}
        ],
        events=events,
        streams=streams,
        material_catalog=catalog,
        material_tool=material_tool,
        material_search_tool=material_search_tool,
        model_context=context_model,
        context_recovery=ContextRecovery(model, context_model, settings, catalog),
        completion_validator=getattr(reflection, 'validate_final', None),
        completion_transform=getattr(reflection, 'transform', None),
    )


@asynccontextmanager
async def open_rag(
    *, settings: Settings, events: EventSink, dynamic: bool | None = None
):
    """Independent retrieval entry; warm the resident encoder before accepting work.

    Optional imports keep ordinary Harness startup independent of the rag extra.
    This does not register an action tool or alter the existing main loop.
    ``dynamic`` is the legacy diagnostic override for inner model review;
    normal callers follow rag.review_enabled. The outer Agent can still requery.
    """
    from backend.infrastructure.model.factory import open_model
    from backend.infrastructure.search.bge_m3 import BgeM3Encoder
    from backend.infrastructure.search.deepseek_review import DeepSeekEvidenceReviewer
    from backend.infrastructure.search.opensearch import OpenSearchRetriever
    from backend.modules.dynamic_rag.service import DynamicRag

    encoder = BgeM3Encoder(settings)
    try:
        active_encoder = encoder
        try:
            await encoder.encode(settings.rag.warmup_text)
        except PortError:
            if not settings.rag.allow_bm25_fallback:
                raise
            active_encoder = None
        async with AsyncExitStack() as stack:
            search = await stack.enter_async_context(
                OpenSearchRetriever(settings, active_encoder)
            )
            reviewer = None
            review_enabled = settings.rag.review_enabled if dynamic is None else dynamic
            if review_enabled:
                model = await stack.enter_async_context(
                    open_model(settings=settings, events=events, role="review")
                )
                reviewer = DeepSeekEvidenceReviewer(model)
            yield DynamicRag(search, settings, reviewer)
    finally:
        encoder.close()


@asynccontextmanager
async def open_harnesses(
    *,
    settings: Settings,
    events: EventSink,
    modes: tuple[str, ...],
    model_choices: tuple[str, ...] | None = None,
    model_errors: dict | None = None,
):
    """Application lifetime: warm the encoder once, then serve multiple runs.

    Startup performs no retrieval. Only an action requested by the model queries
    the corpus. Tests may still inject a Module via build_harness(rag=...).
    """
    from backend.infrastructure.model.factory import open_model, select_model_settings

    async with AsyncExitStack() as stack:
        rag = None
        if settings.rag_tool.enabled:
            try:
                rag = await stack.enter_async_context(
                    open_rag(settings=settings, events=events)
                )
            except PortError as exc:
                # Keep basic Q&A available. Retrieval reports the actual setup
                # failure when requested; never pretend a missing service is empty.
                from backend.contracts.schemas.core import ModuleResponse

                class UnavailableRetrieval:
                    def __init__(self, error):
                        self.error = error

                    async def handle(self, request):
                        return ModuleResponse(status="failed", error=self.error)

                rag = UnavailableRetrieval(exc.error)
        harnesses = {}
        from backend.infrastructure.execution.runtime import DockerRuntime
        execution = DockerRuntime()
        async def close_execution():
            for run_id in list(execution.worlds):
                await execution.destroy(run_id)
        stack.push_async_callback(close_execution)
        # Keep each provider independently available. A missing DeepSeek key
        # must not make a correctly configured relay unusable (and vice versa).
        choices = model_choices or tuple(settings.model_deployments) or ("deepseek-v4",)
        for model_id in choices:
            for mode in dict.fromkeys(modes):
                if mode not in settings.model.modes:
                    raise ValueError("unsupported_thinking_mode")
                try:
                    selected = select_model_settings(settings, model_id)
                    selected.model.thinking_mode = mode
                    model = await stack.enter_async_context(
                        open_model(settings=selected, events=events, role="main")
                    )
                except PortError as exc:
                    if model_errors is not None:
                        model_errors[f"{model_id}:{mode}"] = exc.error
                    break
                harness = build_harness(
                    model=model,
                    settings=selected,
                    events=events,
                    streams=events if hasattr(events, "emit_stream") else None,
                    rag=rag,
                    tools=maritime_tools(selected, execution),
                )
                harnesses[f"{model_id}:{mode}"] = harness
                # Preserve the mode-only keys used by older CLI/test callers;
                # new Runs always select the provider-qualified key.
                if model_id == "deepseek-v4":
                    harnesses[mode] = harness
        yield harnesses


def maritime_tools(settings=None, execution=None):
    from backend.infrastructure.tools.maritime import ClaimTool, CorpusTool
    from backend.application.maritime.work_packages import WorkPackageTool,configured_store
    from backend.infrastructure.execution.runtime import DockerRuntime, execution_tools
    tools = {tool.spec.name: (tool.spec, tool) for tool in (CorpusTool(), ClaimTool(), WorkPackageTool(configured_store(settings)))}
    tools.update(execution_tools(execution or DockerRuntime()))
    return tools


@asynccontextmanager
async def open_harness(*, settings: Settings, events: EventSink):
    """CLI/benchmarks use the configured mode; the workbench opens all modes.

    Each mode has its own model client, budget and protocol continuation cache.
    All workbench modes share one resident retrieval service/encoder.
    """
    errors: dict = {}
    async with open_harnesses(
        settings=settings,
        events=events,
        modes=(settings.model.thinking_mode,),
        model_choices=("deepseek-v4",),
        model_errors=errors,
    ) as harnesses:
        key = f"deepseek-v4:{settings.model.thinking_mode}"
        if key not in harnesses:
            error = errors.get(key) or settings.error("credential_missing")
            raise PortError(error)
        yield harnesses[key]
