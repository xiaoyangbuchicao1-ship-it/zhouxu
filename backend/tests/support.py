from pydantic import BaseModel, ValidationError

from backend.config.settings import load_settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.budget import ModelBudget
from backend.infrastructure.model.deepseek import DeepSeekModel


class EncodingContext(DeepSeekModel):
    """Real production encoder/budget only; no client, credentials or network."""
    def __init__(self, settings=None):
        self.settings = settings or load_settings()
        self.budget = ModelBudget(self.settings)


def context_model(settings=None):
    return EncodingContext(settings)


def relay_settings(model_id="test"):
    settings = load_settings()
    settings.model.name = model_id
    # Synthetic model identity with explicit test-only operating assumptions.
    settings.model_capabilities[model_id] = settings.model_capabilities["gpt-6.1-sol"].model_copy(
        update={"model_id": model_id, "version_or_alias": "offline protocol fixture"})
    return settings


class ScriptedModel:
    def __init__(self, *replies, settings=None):
        self.replies = list(replies)
        self.requests = []
        self.context = context_model(settings)

    def context_budget(self):
        return self.context.context_budget()

    def estimate_context(self, items, tools, *, run_id):
        return self.context.estimate_context(items, tools, run_id=run_id)

    def supports_images(self):
        return self.context.supports_images()

    def allocate_tool_context(self, used_tokens, call_count, image_count=0):
        return self.context.allocate_tool_context(used_tokens, call_count, image_count)

    async def decide(self, request):
        self.requests.append(request.model_copy(deep=True))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class LookupInput(BaseModel):
    record_id: str


class FixtureLookup:
    """Test-only executor, never imported by production code."""

    def __init__(self, receipt):
        self.receipt = receipt
        self.calls = []

    @property
    def spec(self):
        return ToolSpec(
            name="lookup_test_record",
            description="读取测试记录，返回测试回执 receipt。",
            parameters=LookupInput.model_json_schema(),
        )

    async def execute(self, request):
        try:
            LookupInput.model_validate(request.arguments)
        except ValidationError:
            raise PortError(load_settings().error("invalid_arguments")) from None
        self.calls.append(request)
        return ActionResult(
            tool_name=request.tool_name,
            action_id=request.action_id,
            result={"receipt": self.receipt},
        )
