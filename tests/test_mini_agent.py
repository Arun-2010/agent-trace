"""End-to-end tests: run the example agent and check the recorded trace."""

from __future__ import annotations

import json

import pytest
from opentelemetry.trace import StatusCode

from agent_tracer import setup_tracing
from examples.mini_agent import DEMO_QUESTION, DEMO_RESPONSES, ScriptedLLM, run_agent
from tests.conftest import by_name


def run_demo() -> str:
    return run_agent(ScriptedLLM(DEMO_RESPONSES), DEMO_QUESTION)


def test_runs_without_setup():
    # Test 1: tracing never configured -> agent works, nothing recorded, no errors.
    handle = setup_tracing("memory")
    handle.shutdown()  # back to no-op
    assert "8,340 INR" in run_demo()
    assert handle.spans() == []


def test_tree_shape(tracing):
    # Test 2
    run_demo()
    spans = tracing.spans()
    (root,) = by_name(spans, "agent.request")
    assert root.parent is None
    steps = by_name(spans, "agent.step")
    assert len(steps) == 4
    assert [s.attributes["agent.step.number"] for s in steps] == [1, 2, 3, 4]
    for step in steps:
        assert step.parent.span_id == root.context.span_id
    step_ids = {s.context.span_id for s in steps}
    for span in spans:
        if span.name == "llm.call" or span.name.startswith("tool."):
            assert span.parent.span_id in step_ids
    assert len(by_name(spans, "llm.call")) == 4
    assert {s.name for s in spans if s.name.startswith("tool.")} == {
        "tool.get_wether",
        "tool.get_weather",
        "tool.convert_currency",
    }


def test_unknown_tool_error(tracing):
    # Test 3
    run_demo()
    spans = tracing.spans()
    (span,) = by_name(spans, "tool.get_wether")
    step1 = next(s for s in by_name(spans, "agent.step") if s.attributes["agent.step.number"] == 1)
    assert span.parent.span_id == step1.context.span_id
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["agent.error.kind"] == "unknown_tool"


def test_scripted_llm_tokens_estimated_and_costed(tracing):
    # Tests 4 and 5
    run_demo()
    for span in by_name(tracing.spans(), "llm.call"):
        assert span.attributes["gen_ai.usage.input_tokens"] > 0
        assert span.attributes["gen_ai.usage.output_tokens"] > 0
        assert span.attributes["agent.tokens_estimated"] is True
        assert span.attributes["agent.cost_usd"] > 0


def test_root_totals(tracing):
    # Test 6
    run_demo()
    spans = tracing.spans()
    (root,) = by_name(spans, "agent.request")
    calls = by_name(spans, "llm.call")
    assert root.attributes["agent.total_input_tokens"] == sum(s.attributes["gen_ai.usage.input_tokens"] for s in calls)
    assert root.attributes["agent.total_output_tokens"] == sum(s.attributes["gen_ai.usage.output_tokens"] for s in calls)
    assert root.attributes["agent.total_cost_usd"] == pytest.approx(sum(s.attributes["agent.cost_usd"] for s in calls))
    assert root.attributes["agent.steps"] == 4


def test_content_absent_by_default(tracing):
    # Test 7, first half
    run_demo()
    for span in tracing.spans():
        for key in ("agent.user_message", "agent.prompt", "agent.completion", "agent.tool.arguments"):
            assert key not in span.attributes


def test_content_captured_and_redacted(tracing_with_content):
    # Test 7, second half
    secret_message = "I am bob@example.com, call +91 98765 43210, key sk-abc123def456ghi789"
    responses = [
        '{"tool": "search_docs", "arguments": {"query": "email alice@example.org about it"}}',
        '{"final_answer": "done"}',
    ]
    run_agent(ScriptedLLM(responses), secret_message)
    spans = tracing_with_content.spans()
    (root,) = by_name(spans, "agent.request")
    (tool,) = by_name(spans, "tool.search_docs")
    calls = by_name(spans, "llm.call")

    user_message = root.attributes["agent.user_message"]
    assert "[REDACTED_EMAIL]" in user_message
    assert "[REDACTED_PHONE]" in user_message
    assert "[REDACTED_KEY]" in user_message
    assert "[REDACTED_EMAIL]" in json.loads(tool.attributes["agent.tool.arguments"])["query"]
    assert all("agent.prompt" in s.attributes and "agent.completion" in s.attributes for s in calls)

    everything = json.dumps([dict(s.attributes) for s in spans])
    for raw in ("bob@example.com", "alice@example.org", "98765 43210", "sk-abc123def456ghi789"):
        assert raw not in everything


def test_tool_exception_recorded_and_agent_continues(tracing):
    # Test 8
    responses = [
        '{"tool": "convert_currency", "arguments": {"amount": 5, "from_currency": "XYZ", "to_currency": "INR"}}',
        '{"final_answer": "Sorry, XYZ is not supported."}',
    ]
    llm = ScriptedLLM(responses)
    assert run_agent(llm, "convert 5 XYZ") == "Sorry, XYZ is not supported."
    (span,) = by_name(tracing.spans(), "tool.convert_currency")
    assert span.status.status_code == StatusCode.ERROR
    assert span.events[0].name == "exception"
    assert span.events[0].attributes["exception.type"] == "ValueError"
    assert len(by_name(tracing.spans(), "agent.step")) == 2


def test_exception_in_llm_propagates(tracing):
    # Test 9: ScriptedLLM raises once out of replies; the error must reach the caller.
    with pytest.raises(RuntimeError, match="ran out"):
        run_agent(ScriptedLLM([]), "hi")
    spans = tracing.spans()
    (call,) = by_name(spans, "llm.call")
    (root,) = by_name(spans, "agent.request")
    assert call.status.status_code == StatusCode.ERROR
    assert root.status.status_code == StatusCode.ERROR


def test_bad_json_is_recorded_and_fed_back(tracing):
    llm = ScriptedLLM(["not json", '{"final_answer": "ok"}'])
    assert run_agent(llm, "hi") == "ok"
    step1 = by_name(tracing.spans(), "agent.step")[0]
    assert step1.attributes["agent.error.kind"] == "bad_json"


def test_step_limit(tracing):
    # Test 10
    llm = ScriptedLLM(['{"tool": "get_weather", "arguments": {"city": "Pune"}}'] * 3)
    answer = run_agent(llm, "loop forever", max_steps=3)
    assert "did not reach a final answer within 3 steps" in answer
    (root,) = by_name(tracing.spans(), "agent.request")
    assert root.status.status_code == StatusCode.ERROR
    assert root.attributes["agent.error.kind"] == "step_limit"
    assert root.attributes["agent.steps"] == 3
