"""Read-only maritime search and exact arithmetic tools registered at composition root."""

from backend.application.maritime.calculator import calculate_claim
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus


class CorpusTool:
    def __init__(self, corpus=None):
        self.corpus = corpus or MaritimeCorpus()

    spec = ToolSpec(
        name="search_maritime_corpus",
        read_only=True,
        reuse_identical_results=True,
        description="从自研海事法规或案例库检索真实原文。kind=laws/cases。引用时逐字复制返回的 citation 标记（如 [[ZX:0123456789abcdef]]），系统会转换为真实原文链接，禁止自编网址。回答只展示文献标题、条文或章节位置、原文和会影响判断的具体缺口。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 500},
                "kind": {"type": "string", "enum": ["laws", "cases"]},
                "current_only": {"type": "boolean"},
            },
            "required": ["query", "kind"],
            "additionalProperties": False,
        },
    )

    async def execute(self, request):
        try:
            result = await self.corpus.search(**request.arguments)
            public_items = []
            for item in result["items"]:
                original = item["text"]
                public = {
                    key: item[key]
                    for key in ("kind", "title", "text", "locator", "citation")
                    if item.get(key) not in (None, "", [])
                }
                # A case locator is an internal chunk position; the case title
                # and complete source are enough for a user-facing answer.
                if item.get("kind") == "cases":
                    public.pop("locator", None)
                public["text"] = original[:5000]
                public["excerpt_truncated"] = len(original) > 5000
                public_items.append(public)
            result = {
                "query": result.get("query", request.arguments.get("query", "")),
                "kind": result.get("kind", request.arguments.get("kind", "laws")),
                "items": public_items,
            }
            return ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                result=result,
                outcome="ok" if result["items"] else "empty",
            )
        except (ValueError, LookupError):
            return ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                outcome="error",
                result={
                    "status": "unavailable",
                    "notice": "检索未完成；请检查语料索引和查询，不得据此推断不存在依据。",
                },
            )


class ClaimTool:
    spec = ToolSpec(
        name="calculate_maritime_claim",
        read_only=True,
        reuse_identical_results=True,
        description="按明确提供的数量×单价计算索赔项目，精确十进制并按币种分组；不推断责任、汇率或缺失输入。",
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "string"},
                            "rate": {"type": "string"},
                            "currency": {"type": "string"},
                        },
                        "required": ["name", "quantity", "rate", "currency"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    )

    async def execute(self, request):
        try:
            result = calculate_claim(request.arguments.get("items"))
            return ActionResult(
                tool_name=request.tool_name, action_id=request.action_id, result=result
            )
        except (ValueError, TypeError):
            return ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                outcome="error",
                result={
                    "status": "invalid_input",
                    "notice": "请核实项目、数量、费率与币种，缺失数据不能推定。",
                },
            )
