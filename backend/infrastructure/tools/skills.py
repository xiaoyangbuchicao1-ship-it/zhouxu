"""Model-facing read-only adapter for the declarative Skill registry.

``Skill`` remains a method/resource object. ``read_skill`` is the Tool
operation that loads one registered Skill into the model context.
"""

from collections.abc import Callable
from typing import Any

from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.skills.registry import SkillNotFound, SkillRegistry

AccountSkillLoader = Callable[[str, str, int | None], dict[str, Any]]


class SkillReadTool:
    def __init__(
        self,
        registry: SkillRegistry,
        settings: Settings,
        account_skill_loader: AccountSkillLoader | None = None,
    ):
        self.registry = registry
        self.settings = settings
        self.account_skill_loader = account_skill_loader

    @property
    def spec(self) -> ToolSpec:
        descriptors = self.registry.descriptors()
        catalog = "\n".join(
            f"- {item.name} (v{item.version}): {item.description}"
            for item in descriptors
        )
        return ToolSpec(
            name="read_skill",
            description=(
                "读取已注册的专业 Skill 规范。遇到具体海事法律问答、法规版本、"
                "海事案例或其他工具描述明确匹配的专业流程时，先读取对应 Skill；"
                "如果当前用户事实中有已选 skill_id，必须先读取该 Skill。"
                "Skill 只是流程说明，不是本次问题的证据。只能选择内置或当前账户"
                "有权使用的 Skill，不要传路径、URL 或内部文件名。\n\n"
                "已注册的内置 Skill：\n"
                f"{catalog}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "version": {"type": "integer", "minimum": 1},
                },
                "required": ["skill_id"],
                "additionalProperties": False,
            },
            read_only=True,
            reuse_identical_results=True,
            kind="skill_activation",
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            # ``name`` was the v1 argument. Keep accepting it in persisted
            # requests while advertising the clearer skill_id contract now.
            arguments = request.input.arguments
            name = arguments.get("skill_id")
            if name is None:
                name = arguments.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError
            name = name.strip()
            version = arguments.get("version")
            if version is not None and (type(version) is not int or version < 1):
                raise ValueError

            user_facts = (request.context or {}).get("user_facts", {})
            if not isinstance(user_facts, dict):
                user_facts = {}
            if version is None and user_facts.get("skill_id") == name:
                selected_version = user_facts.get("skill_version")
                if type(selected_version) is int and selected_version >= 1:
                    version = selected_version

            if self.registry.contains(name):
                descriptor = self.registry.descriptor(name)
                if version is not None and str(version) != descriptor.version:
                    raise SkillNotFound("skill_version_not_found")
                body = self.registry.read(name)
                method = self.registry.method(name)
                skill_name = descriptor.name
                skill_id = descriptor.skill_id
                skill_version = descriptor.version
                skill_description = descriptor.description
                source = "builtin"
            else:
                account_id = (request.context or {}).get("account_id")
                if (
                    not isinstance(account_id, str)
                    or not account_id
                    or self.account_skill_loader is None
                ):
                    raise SkillNotFound("skill_not_found")
                selected = self.account_skill_loader(account_id, name, version)
                if not isinstance(selected, dict):
                    raise SkillNotFound("skill_not_found")
                body = selected.get("instructions")
                skill_id = selected.get("id")
                skill_name = selected.get("name")
                skill_version = selected.get("version")
                skill_description = selected.get("summary", "")
                if (
                    not isinstance(body, str)
                    or not body.strip()
                    or not isinstance(skill_id, str)
                    or not isinstance(skill_name, str)
                    or type(skill_version) is not int
                ):
                    raise SkillNotFound("skill_invalid")
                method = {}
                source = "account"
        except (SkillNotFound, ValueError, TypeError, AttributeError):
            raise PortError(self.settings.error("invalid_arguments")) from None
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result={
                "status": "ok",
                "skill_id": skill_id,
                "name": skill_name,
                "version": str(skill_version),
                "description": skill_description,
                "source": source,
                "category": method.get("category"),
                "summary": method.get("summary"),
                "purpose": method.get("purpose"),
                "inputs": method.get("inputs", []),
                "deliverables": method.get("deliverables", []),
                "steps": method.get("steps", []),
                "checks": method.get("checks", []),
                "examples": method.get("examples", []),
                "branches": method.get("branches", []),
                "anti_example": method.get("anti_example"),
                "content": body,
            },
            outcome="ok",
            session_summary={
                "status": "ok",
                "skill": skill_id,
                "version": str(skill_version),
                "source": source,
                "body_currently_visible": True,
            },
        )
