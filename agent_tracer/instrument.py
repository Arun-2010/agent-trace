"""The public tracing API: context managers and decorators for any plain-Python agent.

Exists so an agent author adds a handful of lines (`trace_request`, `agent_step`, `@trace_llm`,
`@trace_tool`) and gets a full trace of every step. Rules followed throughout: tracing never
swallows errors or changes return values, and content is only recorded when explicitly enabled,
always redacted.
"""

from __future__ import annotations

import functools
import inspect
import json
import math
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from agent_tracer.pricing import cost_usd
from agent_tracer.redact import redact
from agent_tracer.setup import capture_content_enabled, get_tracer

MAX_CONTENT_CHARS = 2000
MAX_ERROR_CHARS = 300


@dataclass
class _RequestTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    has_cost: bool = False
    steps: int = 0


_totals: ContextVar[_RequestTotals | None] = ContextVar("agent_tracer_totals", default=None)
_usage: ContextVar[dict | None] = ContextVar("agent_tracer_usage", default=None)


def _content(text: str) -> str:
    return redact(text)[:MAX_CONTENT_CHARS]


@contextmanager
def _span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span]:
    """Start a span; on exception record it, mark ERROR and re-raise."""
    with get_tracer().start_as_current_span(
        name, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except Exception as exc:
            message = redact(str(exc))
            span.record_exception(
                exc,
                attributes={
                    "exception.message": message,
                    "exception.stacktrace": redact("".join(traceback.format_exception(exc))),
                },
            )
            span.set_attribute("agent.error.kind", type(exc).__name__)
            span.set_attribute("agent.error.message", message[:MAX_ERROR_CHARS])
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {message[:MAX_ERROR_CHARS]}"))
            raise


@contextmanager
def trace_request(name: str, user_message: str | None = None) -> Iterator[Span]:
    """Root span `agent.request` for one user request; sums tokens, cost and steps when it ends."""
    totals = _RequestTotals()
    token = _totals.set(totals)
    try:
        with _span("agent.request", {"agent.request.name": name}) as span:
            if user_message is not None and capture_content_enabled():
                span.set_attribute("agent.user_message", _content(user_message))
            try:
                yield span
            finally:
                span.set_attribute("agent.total_input_tokens", totals.input_tokens)
                span.set_attribute("agent.total_output_tokens", totals.output_tokens)
                if totals.has_cost:
                    span.set_attribute("agent.total_cost_usd", round(totals.cost_usd, 6))
                span.set_attribute("agent.steps", totals.steps)
    finally:
        _totals.reset(token)


@contextmanager
def agent_step(number: int) -> Iterator[Span]:
    """Span `agent.step` for one iteration of the agent loop."""
    totals = _totals.get()
    if totals is not None:
        totals.steps += 1
    with _span("agent.step", {"agent.step.number": number}) as span:
        yield span


def record_usage(input_tokens: int, output_tokens: int) -> None:
    """Report real token counts from inside a `@trace_llm` function (overrides the estimate)."""
    usage = _usage.get()
    if usage is not None:
        usage["input_tokens"] = input_tokens
        usage["output_tokens"] = output_tokens


def record_error(kind: str, message: str) -> None:
    """Mark the current span ERROR for a problem that is not an exception (bad JSON, unknown tool)."""
    span = trace.get_current_span()
    message = redact(message)[:MAX_ERROR_CHARS]
    span.set_attribute("agent.error.kind", kind)
    span.set_attribute("agent.error.message", message)
    span.set_status(Status(StatusCode.ERROR, f"{kind}: {message}"))


def _prompt_text(system: str, messages: list[dict]) -> str:
    lines = [f"system: {system}"]
    lines += [f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages]
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def trace_llm(model: str) -> Callable:
    """Decorate a `(system, messages) -> str` function (or method) with an `llm.call` span."""

    def decorator(fn: Callable) -> Callable:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _span("llm.call", {"gen_ai.request.model": model}) as span:
                bound = signature.bind(*args, **kwargs).arguments
                prompt = _prompt_text(str(bound.get("system", "")), list(bound.get("messages", [])))
                if capture_content_enabled():
                    span.set_attribute("agent.prompt", _content(prompt))

                usage: dict = {}
                token = _usage.set(usage)
                try:
                    completion = fn(*args, **kwargs)
                finally:
                    _usage.reset(token)

                estimated = "input_tokens" not in usage
                if estimated:
                    input_tokens = _estimate_tokens(prompt)
                    output_tokens = _estimate_tokens(str(completion))
                else:
                    input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
                span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                span.set_attribute("agent.tokens_estimated", estimated)

                cost = cost_usd(model, input_tokens, output_tokens)
                if cost is not None:
                    span.set_attribute("agent.cost_usd", cost)
                if capture_content_enabled():
                    span.set_attribute("agent.completion", _content(str(completion)))

                totals = _totals.get()
                if totals is not None:
                    totals.input_tokens += input_tokens
                    totals.output_tokens += output_tokens
                    if cost is not None:
                        totals.cost_usd += cost
                        totals.has_cost = True
                return completion

        return wrapper

    return decorator


def trace_tool(fn: Callable) -> Callable:
    """Decorate any tool function with a `tool.<name>` span."""
    name = fn.__name__
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _span(f"tool.{name}", {"agent.tool.name": name}) as span:
            if capture_content_enabled():
                try:
                    arguments = signature.bind(*args, **kwargs).arguments
                except TypeError:
                    arguments = {"args": args, "kwargs": kwargs}
                span.set_attribute("agent.tool.arguments", _content(json.dumps(arguments, default=str)))
            return fn(*args, **kwargs)

    return wrapper
