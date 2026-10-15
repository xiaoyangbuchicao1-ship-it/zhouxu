import io
import json
import urllib.error

from perception_layer.planning import DeepSeekToolPlanner


def test_model_can_decline_retrieval_for_linked_resources(monkeypatch):
    requests = []

    def respond(request, timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "可以直接交流。"}}]}).encode())

    monkeypatch.setattr("perception_layer.planning.urllib.request.urlopen", respond)
    planner = DeepSeekToolPlanner(lambda: "test-only-key", endpoint="https://model-test.example.invalid/chat/completions")
    plan = planner.plan("你好", [], "本对话关联一本参考书。")
    assert requests[0]["tool_choice"] == "auto"
    assert plan.requests == ()
    assert plan.web_requests == ()
    assert plan.planner.startswith("tool:")


def test_model_can_choose_external_search_without_trigger_words(monkeypatch):
    arguments = {"requests": [{"purpose": "核对适用版本", "query": "示例条款 生效日期"}]}
    result = {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "web_search", "arguments": json.dumps(arguments),
    }}]}}]}
    monkeypatch.setattr(
        "perception_layer.planning.urllib.request.urlopen",
        lambda request, timeout: io.BytesIO(json.dumps(result).encode()),
    )
    plan = DeepSeekToolPlanner(lambda: "test-only-key", endpoint="https://model-test.example.invalid/chat/completions").plan("这个条款还适用吗？", [], "合同节选")
    assert plan.requests == ()
    assert len(plan.web_requests) == 1
    assert plan.web_requests[0].purpose == "核对适用版本"


def test_failed_model_planning_is_marked_as_raw_local_fallback(monkeypatch):
    def fail(request, timeout):
        raise urllib.error.URLError("test-only network failure")

    monkeypatch.setattr("perception_layer.planning.urllib.request.urlopen", fail)
    plan = DeepSeekToolPlanner(lambda: "test-only-key", endpoint="https://model-test.example.invalid/chat/completions").plan("请联网搜索最新规定", [], "合同节选")
    assert plan.planner == "raw_query_fallback:URLError"
    assert len(plan.requests) == 1
    assert plan.web_requests == ()
