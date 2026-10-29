"""agent-tracer: record every step an LLM agent takes as OpenTelemetry traces.

This module only re-exports the public API so users can `from agent_tracer import ...`.
"""

from __future__ import annotations

from agent_tracer.instrument import (
    agent_step,
    record_error,
    record_usage,
    trace_llm,
    trace_request,
    trace_tool,
)
from agent_tracer.pricing import cost_usd
from agent_tracer.redact import redact
from agent_tracer.setup import TracingHandle, get_tracer, setup_tracing

__all__ = [
    "TracingHandle",
    "agent_step",
    "cost_usd",
    "get_tracer",
    "record_error",
    "record_usage",
    "redact",
    "setup_tracing",
    "trace_llm",
    "trace_request",
    "trace_tool",
]
