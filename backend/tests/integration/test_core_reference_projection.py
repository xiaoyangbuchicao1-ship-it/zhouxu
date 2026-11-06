"""Model views stay compact while real read coverage remains in checkpoints."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import RunInput
from backend.contracts.modules.materials import MaterialInput
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink


@pytest.mark.parametrize("units", [10, 1000, 4000])
async def test_read_reference_is_bounded_without_erasing_private_coverage(
    tmp_path, units
):
    path = tmp_path / "many-lines.txt"
    path.write_text("\n".join(f"记录{i}" for i in range(units)), encoding="utf-8")
    settings, events, packets = load_settings(), MemoryEventSink(), []
    settings.materials.cache_root = str(tmp_path / "cache")

    def respond(request):
        packet = json.loads(request.content)
        packets.append(packet)
        if len(packets) == 1:
            message = {
                "tool_calls": [
                    {
                        "id": "read-lines",
                        "type": "function",
                        "function": {
                            "name": "read_material",
                            "arguments": json.dumps(
                                {
                                    "material_id": "lines",
                                    "max_chars": 48000,
                                }
                            ),
                        },
                    }
                ]
            }
            reason = "tool_calls"
        else:
            message, reason = {"content": "已读取记录。"}, "stop"
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": reason}]}
        )

    async with DeepSeekModel(
        settings, SecretStr("offline"), events, transport=httpx.MockTransport(respond)
    ) as model:
        agent = build_harness(model=model, settings=settings, events=events)
        result = await agent.run(
            RunInput(
                text="读取记录。",
                materials=[MaterialInput(uri=str(path), material_id="lines")],
            ),
            session_id="reference-test",
        )
    assert result.status == "completed" and len(packets) == 2
    references = [
        m["content"]
        for m in packets[-1]["messages"]
        if m["role"] == "user" and '"source": "tool_reference"' in m.get("content", "")
    ]
    assert references, "exercise the explicit Conversation reference path"
    assert max(map(len, references)) < 2500, (
        "internal per-unit bookkeeping leaked into the model"
    )
    assert "记录0" in next(
        m["content"] for m in packets[-1]["messages"] if m["role"] == "tool"
    )
    private = result.execution_state["tool_results"][0]["result"]
    assert len(private["coverage"]) > 1
    assert result.execution_state["material_coverage"]
    # The saved source reference is not mutated by model projection.
    reference = next(
        r for r in result.execution_state["records"] if r["source"] == "tool_reference"
    )
    body = reference.get("body") or reference.get("updates")
    assert len(body["summary"]["unit_ids"]) == len(private["units"])
    assert len(body["summary"]["coverage"]) == len(private["coverage"])
