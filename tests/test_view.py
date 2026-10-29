"""JSONL exporter + tree viewer tests (test 11)."""

from __future__ import annotations

import json

from agent_tracer import setup_tracing
from agent_tracer.view import load_traces, main, render_tree
from examples.mini_agent import DEMO_QUESTION, DEMO_RESPONSES, ScriptedLLM, run_agent

FIELDS = {"trace_id", "span_id", "parent_id", "name", "start_ns", "end_ns", "status", "attributes", "events"}


def write_demo_trace(path) -> None:
    handle = setup_tracing("jsonl", jsonl_path=str(path))
    try:
        run_agent(ScriptedLLM(DEMO_RESPONSES), DEMO_QUESTION)
    finally:
        handle.shutdown()


def test_jsonl_one_object_per_line(tmp_path):
    path = tmp_path / "nested" / "trace.jsonl"  # folder is created automatically
    write_demo_trace(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 4 + 4 + 3  # request, steps, llm calls, tools
    for line in lines:
        span = json.loads(line)
        assert set(span) == FIELDS
        assert span["status"] in {"OK", "ERROR", "UNSET"}


def test_load_and_render(tmp_path):
    path = tmp_path / "trace.jsonl"
    write_demo_trace(path)
    traces = load_traces(str(path))
    assert len(traces) == 1
    (spans,) = traces.values()
    tree = render_tree(spans)

    for name in ("agent.request", "agent.step #1", "agent.step #4", "llm.call", "tool.get_wether",
                 "tool.get_weather", "tool.convert_currency"):
        assert name in tree
    assert "ERROR unknown_tool" in tree
    assert "ms" in tree
    assert "├─" in tree and "└─" in tree and "│" in tree
    assert tree.splitlines()[0].startswith("agent.request")


def test_multiple_traces_newest_last(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    write_demo_trace(path)
    write_demo_trace(path)
    traces = load_traces(str(path))
    assert len(traces) == 2
    first, second = traces.values()
    assert min(s["start_ns"] for s in first) < min(s["start_ns"] for s in second)

    assert main([str(path)]) == 0
    assert capsys.readouterr().out.count("agent.request") == 2

    trace_id = list(traces)[0]
    assert main([str(path), "--trace-id", trace_id]) == 0
    assert capsys.readouterr().out.count("agent.request") == 1
    assert main([str(path), "--trace-id", "missing"]) == 1


def test_render_durations_in_seconds():
    spans = [
        {"trace_id": "t", "span_id": "a", "parent_id": None, "name": "agent.request", "start_ns": 0,
         "end_ns": 2_100_000_000, "status": "UNSET", "attributes": {}, "events": []},
        {"trace_id": "t", "span_id": "b", "parent_id": "a", "name": "tool.x", "start_ns": 0,
         "end_ns": 1_000_000, "status": "UNSET", "attributes": {}, "events": []},
    ]
    tree = render_tree(spans)
    assert "2.1s" in tree
    assert "1ms  ok" in tree
