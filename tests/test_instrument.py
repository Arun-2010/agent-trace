"""Tests for the decorators and context managers, independent of the example agent."""

from __future__ import annotations

import pytest
from opentelemetry.trace import StatusCode

from agent_tracer import cost_usd, record_usage, trace_llm, trace_request, trace_tool
from tests.conftest import by_name


@trace_llm(model="example-small-model")
def known_llm(system: str, messages: list[dict]) -> str:
    return "hello world"


@trace_llm(model="some-unknown-model")
def unknown_llm(system: str, messages: list[dict]) -> str:
    return "hello world"


@trace_llm(model="example-large-model")
def reporting_llm(system: str, messages: list[dict]) -> str:
    record_usage(1000, 200)
    return "hi"


def test_no_setup_is_a_noop():
    # Test 1 (stub level): decorated functions work and nothing breaks without setup_tracing.
    with trace_request("r", user_message="hi"):
        assert known_llm("sys", [{"role": "user", "content": "x"}]) == "hello world"


def test_tokens_are_estimated_by_default(tracing):
    known_llm("system", [{"role": "user", "content": "abcd" * 10}])
    (span,) = by_name(tracing.spans(), "llm.call")
    assert span.attributes["gen_ai.request.model"] == "example-small-model"
    assert span.attributes["gen_ai.usage.input_tokens"] > 0
    assert span.attributes["gen_ai.usage.output_tokens"] == 3  # ceil(11 / 4)
    assert span.attributes["agent.tokens_estimated"] is True


def test_record_usage_overrides_estimate(tracing):
    reporting_llm("system", [])
    (span,) = by_name(tracing.spans(), "llm.call")
    assert span.attributes["gen_ai.usage.input_tokens"] == 1000
    assert span.attributes["gen_ai.usage.output_tokens"] == 200
    assert span.attributes["agent.tokens_estimated"] is False
    assert span.attributes["agent.cost_usd"] == cost_usd("example-large-model", 1000, 200)


def test_cost_known_and_unknown_model(tracing):
    known_llm("s", [])
    unknown_llm("s", [])
    known, unknown = by_name(tracing.spans(), "llm.call")
    assert "agent.cost_usd" in known.attributes
    assert "agent.cost_usd" not in unknown.attributes


def test_cost_usd():
    assert cost_usd("example-large-model", 1_000_000, 1_000_000) == 18.0
    assert cost_usd("example-small-model", 123, 45) == round((123 * 0.5 + 45 * 1.5) / 1e6, 6)
    assert cost_usd("nope", 10, 10) is None


def test_exception_propagates_and_is_recorded(tracing):
    # Test 9
    @trace_tool
    def explode(x: int) -> int:
        raise KeyError("boom")

    with pytest.raises(KeyError):
        explode(1)
    (span,) = by_name(tracing.spans(), "tool.explode")
    assert span.status.status_code == StatusCode.ERROR
    assert span.events[0].name == "exception"
    assert span.attributes["agent.error.kind"] == "KeyError"


def test_decorators_keep_return_values(tracing):
    @trace_tool
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, b=3) == 5
    assert add.__name__ == "add"
