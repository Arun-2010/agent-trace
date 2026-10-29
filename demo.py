"""Run the example agent with tracing on, so you can see what a trace looks like.

Default: saves spans to traces/demo.jsonl and prints the trace tree.
  --otlp              send spans to Jaeger (http://localhost:4318) instead
  --capture-content   also record prompts, completions and tool arguments (redacted)
  --live "question"   use the real Anthropic API (needs ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import argparse
import os
import socket
import sys

from agent_tracer import setup_tracing
from agent_tracer.view import load_traces, render_tree
from examples.mini_agent import DEMO_QUESTION, DEMO_RESPONSES, ScriptedLLM, run_agent

JSONL_PATH = "traces/demo.jsonl"
JAEGER_CMD = "docker run --rm -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one:latest"


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trace the example agent.")
    parser.add_argument("--otlp", action="store_true", help="export to Jaeger over OTLP/HTTP")
    parser.add_argument("--capture-content", action="store_true", help="record prompts and arguments (redacted)")
    parser.add_argument("--live", metavar="QUESTION", help="ask the real Anthropic API this question")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.live:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--live needs an Anthropic API key. Set it, then run again:")
            print('  Windows (PowerShell):  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
            print('  Mac/Linux:             export ANTHROPIC_API_KEY="sk-ant-..."')
            return 1
        try:
            from examples.mini_agent import AnthropicLLM

            llm = AnthropicLLM()
        except ImportError:
            print('--live needs the Anthropic SDK:  pip install -e ".[anthropic]"')
            return 1
        question = args.live
    else:
        llm = ScriptedLLM(DEMO_RESPONSES)
        question = DEMO_QUESTION

    if args.otlp:
        if not _port_open("localhost", 4318):
            print("Jaeger is not reachable on localhost:4318. Start it with:")
            print(f"  {JAEGER_CMD}")
            return 1
        handle = setup_tracing("otlp", capture_content=args.capture_content)
    else:
        if os.path.exists(JSONL_PATH):
            os.remove(JSONL_PATH)  # one run per file, so the replay shows just this trace
        handle = setup_tracing("jsonl", jsonl_path=JSONL_PATH, capture_content=args.capture_content)

    try:
        answer = run_agent(llm, question)
    finally:
        handle.shutdown()

    if not args.otlp:
        traces = load_traces(JSONL_PATH)
        print(render_tree(list(traces.values())[-1]))
    print()
    print(f"Answer: {answer}")
    print()
    if args.otlp:
        print('Open http://localhost:16686, choose service "agent-tracer", click "Find Traces".')
    else:
        print(f"Saved to {JSONL_PATH}. Replay it with:")
        print(f"  python -m agent_tracer.view {JSONL_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
