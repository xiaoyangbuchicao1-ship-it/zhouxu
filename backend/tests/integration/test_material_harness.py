from __future__ import annotations

import pytest

pytest.importorskip("docx")

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import (
    ActionRequest,
    DecisionProposal,
    PerceivedContext,
    ReasoningInput,
    RunInput,
)
from backend.contracts.modules.materials import MaterialInput
from backend.contracts.modules.perception import (
    ContextAssembly,
    ContextItem,
    ContextLink,
)
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.tests.support import ScriptedModel


async def test_complete_short_material_reaches_model_without_forcing_reader(tmp_path):
    path = tmp_path / "case.txt"
    path.write_text("承运人应当妥善保管货物。", encoding="utf-8")
    model = ScriptedModel(
        DecisionProposal(kind="answer", answer="已读取材料。"),
    )
    harness = build_harness(
        model=model,
        settings=load_settings(),
        events=MemoryEventSink(),
    )
    result = await harness.run(
        RunInput(
            text="请读取附件并整理内容",
            materials=[MaterialInput(uri=str(path), material_id="case")],
        )
    )
    assert result.status == "completed"
    first = model.requests[0].input
    material = next(item for item in first.perceived.items if item.id == "material:case")
    assert material.source == "user.material"
    assert {"read_material", "search_material"} <= {tool.name for tool in first.tools}
    assert "承运人应当妥善保管货物" in str(material.body)
    assert len(model.requests) == 1


def test_image_tool_result_is_encoded_as_vision_content():
    call = ActionRequest(
        tool_name="read_material",
        arguments={"material_id": "photo", "unit_id": "photo:image:x"},
        action_id="a1",
    )
    action = {
        "tool_name": "read_material",
        "action_id": "a1",
        "result": {
            "manifest": {"material_id": "photo"},
            "image_data_url": "data:image/png;base64,AA==",
        },
    }
    perceived = PerceivedContext(
        run_id="run",
        items=[
            ContextItem(
                id="call",
                type="tool_call",
                source="reasoning",
                role="assistant",
                kind="reasoning.proposal",
                body={"calls": [call.model_dump()]},
            ),
            ContextItem(
                id="result",
                type="tool_result",
                source="action",
                role="tool",
                kind="action.result",
                body=action,
                links=[ContextLink(relation="responds_to", target_id="call")],
            ),
        ],
        assembly=ContextAssembly(budget_chars=24000, used_chars=100),
    )
    request = ModuleRequest(
        run_id="run",
        call_id="call-model",
        input=ReasoningInput(perceived=perceived),
    )
    model = object.__new__(DeepSeekModel)
    model.settings = load_settings()
    messages = model._messages(request)
    assert messages[-1]["content"][1]["type"] == "image_url"
