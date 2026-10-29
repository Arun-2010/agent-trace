"""Tracing setup: one call picks where spans go.

Exists so the rest of the library (and any agent using it) never touches OpenTelemetry's
provider/exporter plumbing directly. The provider is kept in a module-level variable instead of
OpenTelemetry's global one, because the global can only be set once per process; this keeps tests
independent and makes tracing a free no-op until `setup_tracing()` is called.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, Sequence

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ExporterName = Literal["console_tree", "jsonl", "otlp", "memory"]

_provider: TracerProvider | None = None
_capture_content: bool = False


def capture_content_enabled() -> bool:
    """True if prompts, completions and tool arguments may be recorded (always redacted)."""
    return _capture_content


def span_to_dict(span: ReadableSpan) -> dict:
    """Convert a finished span to the plain dict stored in JSONL files and read by `view.py`."""
    return {
        "trace_id": format(span.context.trace_id, "032x"),
        "span_id": format(span.context.span_id, "016x"),
        "parent_id": format(span.parent.span_id, "016x") if span.parent else None,
        "name": span.name,
        "start_ns": span.start_time,
        "end_ns": span.end_time,
        "status": span.status.status_code.name,
        "attributes": dict(span.attributes or {}),
        "events": [
            {"name": e.name, "timestamp_ns": e.timestamp, "attributes": dict(e.attributes or {})}
            for e in span.events
        ],
    }


class JsonlSpanExporter(SpanExporter):
    """Appends one JSON object per finished span to a file."""

    def __init__(self, path: str) -> None:
        self.path = path
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with open(self.path, "a", encoding="utf-8") as f:
            for span in spans:
                f.write(json.dumps(span_to_dict(span), default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class ConsoleTreeExporter(SpanExporter):
    """Buffers spans per trace and prints the whole tree when the root span ends."""

    def __init__(self) -> None:
        self._buffers: dict[str, list[dict]] = defaultdict(list)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        # imported here so `python -m agent_tracer.view` doesn't load view twice
        from agent_tracer.view import render_tree

        for span in spans:
            data = span_to_dict(span)
            self._buffers[data["trace_id"]].append(data)
            if data["parent_id"] is None:
                text = render_tree(self._buffers.pop(data["trace_id"]))
                try:
                    print(text)
                except UnicodeEncodeError:  # e.g. a Windows pipe using cp1252
                    encoding = sys.stdout.encoding or "ascii"
                    print(text.encode(encoding, errors="replace").decode(encoding))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._buffers.clear()


@dataclass
class TracingHandle:
    provider: TracerProvider
    exporter: SpanExporter

    def shutdown(self) -> None:
        """Flush and stop. If this is the active setup, tracing goes back to being a no-op."""
        global _provider, _capture_content
        self.provider.shutdown()
        if _provider is self.provider:
            _provider = None
            _capture_content = False

    def spans(self) -> list[ReadableSpan]:
        """Finished spans (memory exporter only; used by tests)."""
        if not isinstance(self.exporter, InMemorySpanExporter):
            raise RuntimeError("spans() is only available with exporter='memory'")
        return list(self.exporter.get_finished_spans())


def setup_tracing(
    exporter: ExporterName = "console_tree",
    *,
    jsonl_path: str = "traces/trace.jsonl",
    otlp_endpoint: str = "http://localhost:4318/v1/traces",
    capture_content: bool = False,
    service_name: str = "agent-tracer",
) -> TracingHandle:
    """Start recording spans to the chosen exporter. Returns a handle to flush/stop it."""
    global _provider, _capture_content

    span_exporter: SpanExporter
    if exporter == "console_tree":
        span_exporter = ConsoleTreeExporter()
    elif exporter == "jsonl":
        span_exporter = JsonlSpanExporter(jsonl_path)
    elif exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    elif exporter == "memory":
        span_exporter = InMemorySpanExporter()
    else:
        raise ValueError(f"unknown exporter {exporter!r}")

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    _provider = provider
    _capture_content = capture_content
    return TracingHandle(provider, span_exporter)


def get_tracer() -> trace.Tracer:
    """Tracer from the most recent `setup_tracing()`, or a no-op tracer if tracing is off."""
    if _provider is None:
        return trace.NoOpTracer()
    return _provider.get_tracer("agent_tracer")
