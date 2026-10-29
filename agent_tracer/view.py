"""Replay saved traces as a tree in the terminal.

Exists so a failed run can be inspected later without any extra tooling: it reads the JSONL file
written by the `jsonl` exporter and uses the standard library only.

Usage: python -m agent_tracer.view traces/demo.jsonl [--trace-id ID]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict


def load_traces(path: str) -> dict[str, list[dict]]:
    """Read a JSONL file into {trace_id: [span, ...]}, ordered oldest trace first."""
    traces: dict[str, list[dict]] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                span = json.loads(line)
                traces[span["trace_id"]].append(span)
    ordered = sorted(traces.items(), key=lambda item: min(s["start_ns"] for s in item[1]))
    return dict(ordered)


def _duration(span: dict) -> str:
    ns = (span.get("end_ns") or span["start_ns"]) - span["start_ns"]
    ms = ns / 1_000_000
    return f"{ms:.0f}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def _label(span: dict) -> str:
    attrs = span.get("attributes", {})
    name = span["name"]
    if name == "agent.request" and "agent.request.name" in attrs:
        return f'{name} "{attrs["agent.request.name"]}"'
    if name == "agent.step" and "agent.step.number" in attrs:
        return f"{name} #{attrs['agent.step.number']}"
    if name == "llm.call" and "gen_ai.request.model" in attrs:
        return f"{name}  model={attrs['gen_ai.request.model']}"
    return name


def _details(span: dict) -> str:
    attrs = span.get("attributes", {})
    parts = []
    if "agent.total_input_tokens" in attrs:
        parts.append(f"in {attrs['agent.total_input_tokens']:,} / out {attrs['agent.total_output_tokens']:,} tok")
    elif "gen_ai.usage.input_tokens" in attrs:
        est = " (est)" if attrs.get("agent.tokens_estimated") else ""
        parts.append(f"in {attrs['gen_ai.usage.input_tokens']:,} / out {attrs['gen_ai.usage.output_tokens']:,}{est}")
    cost = attrs.get("agent.total_cost_usd", attrs.get("agent.cost_usd"))
    if cost is not None:
        parts.append(f"${cost:.6f}")
    if span.get("status") == "ERROR":
        parts.append(f"ERROR {attrs.get('agent.error.kind', '')}".rstrip())
    elif span["name"].startswith("tool."):
        parts.append("ok")
    return "  ".join(parts)


def render_tree(spans: list[dict]) -> str:
    """Render one trace's spans as an indented tree with durations, tokens, cost and errors."""
    ids = {s["span_id"] for s in spans}
    children: dict[str | None, list[dict]] = defaultdict(list)
    for span in spans:
        parent = span.get("parent_id")
        children[parent if parent in ids else None].append(span)
    for group in children.values():
        group.sort(key=lambda s: s["start_ns"])

    rows: list[tuple[str, str, str]] = []

    def walk(span: dict, prefix: str, connector: str, child_prefix: str) -> None:
        rows.append((prefix + connector + _label(span), _duration(span), _details(span)))
        kids = children.get(span["span_id"], [])
        for i, kid in enumerate(kids):
            last = i == len(kids) - 1
            walk(kid, prefix + child_prefix, "└─ " if last else "├─ ", "   " if last else "│  ")

    for root in children.get(None, []):
        walk(root, "", "", "")

    width = max((len(label) for label, _, _ in rows), default=0)
    return "\n".join(f"{label:<{width}}  {dur:>6}  {details}".rstrip() for label, dur, details in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print saved agent traces as a tree.")
    parser.add_argument("path", help="JSONL file written by the jsonl exporter")
    parser.add_argument("--trace-id", help="only print this trace")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    traces = load_traces(args.path)
    if args.trace_id:
        if args.trace_id not in traces:
            print(f"trace {args.trace_id} not found in {args.path}", file=sys.stderr)
            return 1
        traces = {args.trace_id: traces[args.trace_id]}
    if not traces:
        print(f"no spans in {args.path}", file=sys.stderr)
        return 1

    for trace_id, spans in traces.items():
        print(f"trace {trace_id}")
        print(render_tree(spans))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
