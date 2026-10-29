# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project: agent-tracer

A small, standalone library that records every step an LLM agent takes (each LLM call, each tool call,
each error) as OpenTelemetry traces, so a failed run can be understood and replayed later.

It does **not** depend on any other project. It ships with its own tiny example agent to trace, and
its helpers can wrap any agent written in plain Python.

Example of what a user should see after one request:

```
agent.request "convert 100 USD to INR and tell me the weather in Hyderabad"   2.1s  in 1,210 / out 96 tok  $0.0052
├─ agent.step #1                                                               0.7s
│  ├─ llm.call  model=claude-sonnet-5-5                                         0.7s  in 380 / out 31
│  └─ tool.get_wether                                                          0ms   ERROR unknown_tool
├─ agent.step #2                                                               0.6s
│  ├─ llm.call  model=claude-sonnet-5-5                                         0.6s  in 402 / out 29
│  └─ tool.get_weather                                                         1ms   ok
├─ agent.step #3                                                               0.5s
│  ├─ llm.call                                                                 0.5s  in 410 / out 22
│  └─ tool.convert_currency                                                    1ms   ok
└─ agent.step #4                                                               0.3s
   └─ llm.call                                                                 0.3s  final answer
```

## Commands

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
python demo.py                              # runs the example agent, prints the trace tree, saves traces/demo.jsonl
python -m agent_tracer.view traces/demo.jsonl   # replay a saved trace as a tree
python demo.py --otlp                       # send to Jaeger instead (see "View in Jaeger")
python demo.py --capture-content            # also record prompts/arguments (redacted)
```

## Conventions

- Python 3.10+, type hints, `from __future__ import annotations`.
- Every module starts with a short docstring explaining *why* it exists.
- **Keep it simple.** Plain functions and small classes. No features beyond this spec.
- `pytest` for tests.

## Non-goals (do NOT build these)

Metrics, log export, sampling, async support, LangSmith/Arize SDKs, a web UI, a database, Pydantic.
Viewing is done with Jaeger (free, one Docker command) or the built-in tree viewer.

## Dependencies (`pyproject.toml`)

```toml
[project]
name = "agent-tracer"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "opentelemetry-api>=1.25",
  "opentelemetry-sdk>=1.25",
  "opentelemetry-exporter-otlp-proto-http>=1.25",
]

[project.optional-dependencies]
dev = ["pytest"]
anthropic = ["anthropic"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

## Files to create

```
agent_tracer/
  __init__.py      public API exports
  setup.py         setup_tracing(), get_tracer(), exporters (console tree, jsonl, otlp, memory)
  instrument.py    trace_request(), agent_step(), @trace_llm, @trace_tool, record_usage()
  pricing.py       price table + cost calculation
  redact.py        redact() for emails, phone numbers, API keys
  view.py          CLI + load_traces() + render_tree() (standard library only)
examples/
  mini_agent.py    tiny tool-calling agent used by the demo and tests
demo.py
tests/
  test_instrument.py
  test_redact.py
  test_view.py
  test_mini_agent.py
README.md
.gitignore
```

---

## `agent_tracer/setup.py`

```python
def setup_tracing(
    exporter: Literal["console_tree", "jsonl", "otlp", "memory"] = "console_tree",
    *,
    jsonl_path: str = "traces/trace.jsonl",
    otlp_endpoint: str = "http://localhost:4318/v1/traces",
    capture_content: bool = False,
    service_name: str = "agent-tracer",
) -> TracingHandle: ...

def get_tracer() -> Tracer: ...
```

- Creates a `TracerProvider` (resource `service.name`) with one `SimpleSpanProcessor` for the chosen exporter.
- `console_tree`: buffers spans per trace and prints the tree (using `view.render_tree`) when the root span ends.
- `jsonl`: appends one JSON object per finished span: `trace_id`, `span_id`, `parent_id`, `name`, `start_ns`,
  `end_ns`, `status` (`OK`/`ERROR`/`UNSET`), `attributes`, `events`. Creates the folder if missing.
- `otlp`: OTLP over HTTP to `otlp_endpoint`.
- `memory`: `InMemorySpanExporter`, exposed via `handle.spans()` for tests.
- `TracingHandle` has `shutdown()` (flush and stop) and `spans()` (memory mode only).
- `capture_content` is stored in a module-level setting. Default **False**.
- **Gotcha:** OpenTelemetry lets you set the *global* tracer provider only once per process. Do not use
  `trace.set_tracer_provider`. Keep the provider in a module-level variable; `get_tracer()` returns a tracer
  from the most recent `setup_tracing`, or OpenTelemetry's no-op tracer if setup was never called.
  This makes tests independent and means un-configured tracing costs nothing.

## `agent_tracer/instrument.py`

The whole public API. Works with any agent.

```python
@contextmanager
def trace_request(name: str, user_message: str | None = None): ...
```
Root span `agent.request`. Records `agent.user_message` only if `capture_content` (redacted).
When it ends, sets `agent.total_input_tokens`, `agent.total_output_tokens`, `agent.total_cost_usd`,
`agent.steps` summed from its child spans (keep running totals in a small per-request accumulator).

```python
@contextmanager
def agent_step(number: int): ...
```
Span `agent.step` with attribute `agent.step.number`.

```python
def trace_llm(model: str): ...      # decorator for a function (system: str, messages: list[dict]) -> str
```
Span `llm.call` with:

| Attribute | Value |
|---|---|
| `gen_ai.request.model` | model name |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | real values if the function called `record_usage()`, otherwise estimated as `ceil(chars / 4)` |
| `agent.tokens_estimated` | `True` when estimated |
| `agent.cost_usd` | from `pricing.py`, omitted for unknown models |
| `agent.prompt`, `agent.completion` | only if `capture_content`; redacted; truncated to 2,000 chars |

```python
def record_usage(input_tokens: int, output_tokens: int) -> None: ...
```
Called from inside a traced LLM function when the provider returns real token counts.

```python
def trace_tool(fn): ...             # decorator for any tool function
```
Span `tool.<function name>` with `agent.tool.name`; arguments as JSON only if `capture_content` (redacted).

```python
def record_error(kind: str, message: str) -> None: ...
```
Marks the current span ERROR with `agent.error.kind` and `agent.error.message` (first 300 chars).
Used by the agent for errors that are not exceptions (e.g. unknown tool, bad JSON).

Exceptions raised inside any traced span: `span.record_exception(exc)`, status ERROR, then **re-raise**.
Tracing must never swallow errors or change the program's behaviour.

## `agent_tracer/pricing.py`

```python
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # model: (input USD per 1M tokens, output USD per 1M tokens)
    # PLACEHOLDER VALUES - replace with current prices from your provider's pricing page.
    "example-small-model": (0.50, 1.50),
    "example-large-model": (3.00, 15.00),
}

def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None: ...
```
Unknown model returns `None` (never guess). Round to 6 decimals.

## `agent_tracer/redact.py`

`redact(text: str) -> str` replaces:
- emails → `[REDACTED_EMAIL]`
- phone numbers (10+ digits, optional `+`, spaces or dashes) → `[REDACTED_PHONE]`
- API keys and tokens (`sk-...`, `Bearer ...`, any 32+ char hex/base64-like string) → `[REDACTED_KEY]`

## `agent_tracer/view.py` (standard library only)

`python -m agent_tracer.view <file.jsonl> [--trace-id ID]`

- `load_traces(path) -> dict[trace_id, list[span]]`
- `render_tree(spans) -> str`: builds parent/child links, sorts children by start time, prints each span with
  name, duration (ms or s), tokens, cost, status, and error kind, using `├─ └─ │` like the example above.
- With no `--trace-id`, print every trace in the file, newest last.

## `examples/mini_agent.py`

A deliberately tiny agent (about 100 lines), so the tracer has something realistic to record.

- 3 tools, each decorated with `@trace_tool`: `get_weather(city)`, `convert_currency(amount, from_currency, to_currency)`,
  `search_docs(query)`, returning fake data.
- `ScriptedLLM`: returns pre-written responses in order (for demo and tests, no API key).
- `AnthropicLLM` (optional, only imported when used): calls the Anthropic SDK and calls `record_usage()`
  with the real usage numbers from the response.
- Both LLMs' `complete` functions are decorated with `@trace_llm(model=...)`.
- Protocol: the LLM replies with JSON, either `{"tool": "...", "arguments": {...}}` or `{"final_answer": "..."}`.
- `run_agent(llm, user_message, max_steps=6) -> str`:
  - wraps everything in `trace_request`, each loop iteration in `agent_step(n)`,
  - invalid JSON → `record_error("bad_json", ...)`, feed the error back, continue,
  - unknown tool → `record_error("unknown_tool", ...)`, feed back the list of valid tools, continue,
  - tool exception → recorded automatically by `@trace_tool`, error fed back, continue,
  - final answer → return it; step limit → `record_error("step_limit", ...)` and return a clear message.

## `demo.py`

Runs `run_agent` with `ScriptedLLM` on: *"Convert 100 USD to INR and tell me the weather in Hyderabad"*.
Scripted responses: misspelled tool name (`get_wether`), then `get_weather`, then `convert_currency`, then a final answer.

- Default: `console_tree` printed to the terminal **and** saved to `traces/demo.jsonl`
  (use two span processors, or run the JSONL exporter and then print with `view.render_tree`; pick the simpler one).
- `--otlp`: export to Jaeger.
- `--capture-content`: record prompts and arguments (redacted).
- `--live "question"`: use `AnthropicLLM` if `ANTHROPIC_API_KEY` is set; otherwise print how to set it.
- Ends by printing the replay command.

### View in Jaeger (document in README)

```bash
docker run --rm -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one:latest
python demo.py --otlp
# open http://localhost:16686, choose service "agent-tracer", click "Find Traces"
```

## Tests (use the `memory` exporter)

1. Without `setup_tracing`, the agent runs normally and nothing is recorded (no errors).
2. Demo run produces the tree `agent.request > agent.step(x4) > {llm.call, tool.*}` with correct parent links.
3. Step 1's `tool.get_wether` span is ERROR with `agent.error.kind = "unknown_tool"`.
4. `llm.call` spans have token attributes and `agent.tokens_estimated = True` with `ScriptedLLM`;
   `record_usage()` overrides the estimate and sets it to `False`.
5. Cost: known model gets `agent.cost_usd`; unknown model has no cost attribute.
6. Root span totals equal the sums over child `llm.call` spans; `agent.steps == 4`.
7. Content attributes absent by default; with `capture_content=True` they exist and emails, phones and keys are redacted.
8. A tool that raises: span ERROR with the exception recorded, and the agent still continues (error fed back).
9. An exception inside a traced function still propagates to the caller.
10. Step limit reached → `step_limit` error recorded, clear message returned.
11. JSONL file has one valid JSON object per line; `load_traces` + `render_tree` contain every span name,
    durations and the `unknown_tool` error.
12. `redact()` unit tests, including text with no secrets left unchanged.

## Build order

Run `pytest` after each step and fix failures before moving on.

1. Project skeleton, `pyproject.toml`, `setup.py` with memory exporter + no-op fallback (test 1 with a stub).
2. `instrument.py`: `trace_request`, `agent_step`, `trace_llm`, `trace_tool`, `record_usage`, `record_error`; `pricing.py`.
3. `examples/mini_agent.py` (tests 2, 3, 4, 5, 6, 8, 9, 10).
4. `redact.py` + content capture (tests 7, 12).
5. JSONL exporter, `view.py`, console tree (test 11).
6. `demo.py`, `README.md`, `.gitignore` (`traces/`, `.venv/`, `__pycache__/`).

## README.md must include

1. The problem in plain words (agent runs are a black box; one wrong answer, 10 hidden steps).
2. Key ideas: trace, span, attributes, why OpenTelemetry (vendor-neutral).
3. How to run: venv, install, demo, tests, viewer, Jaeger, live mode. Windows and Mac/Linux commands.
4. How to trace your own agent (10-line example using the decorators).
5. Design decisions table: no-op when off, content off by default + redaction, estimated vs real tokens,
   never guess prices, tracing never changes behaviour, own provider instead of global.
6. Exercises: add a span for retries; add a `latency_ms` histogram; export to Arize Phoenix; trace a LangChain/LangGraph agent.
7. Interview talking points.

## Definition of done

- `pytest` green.
- `python demo.py` prints a tree with 4 steps, one ERROR (`unknown_tool`), token counts and cost.
- `python -m agent_tracer.view traces/demo.jsonl` prints the same tree.
- The trace appears in Jaeger with `--otlp`.
- No prompt or argument content is recorded unless `--capture-content`, and recorded content is redacted.