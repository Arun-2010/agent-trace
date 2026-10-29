# agent-tracer

Record every step an LLM agent takes (each LLM call, each tool call, each error) as
OpenTelemetry traces, so a failed run can be understood and replayed later.

```
agent.request "mini_agent"                   21ms  in 614 / out 78 tok  $0.000423
├─ agent.step #1                              5ms
│  ├─ llm.call  model=example-small-model     1ms  in 89 / out 15 (est)  $0.000067
│  └─ tool.get_wether                         0ms  ERROR unknown_tool
├─ agent.step #2                              4ms
│  ├─ llm.call  model=example-small-model     0ms  in 131 / out 15 (est)  $0.000088
│  └─ tool.get_weather                        0ms  ok
├─ agent.step #3                              3ms
│  ├─ llm.call  model=example-small-model     0ms  in 173 / out 26 (est)  $0.000125
│  └─ tool.convert_currency                   0ms  ok
└─ agent.step #4                              2ms
   └─ llm.call  model=example-small-model     0ms  in 221 / out 22 (est)  $0.000143
```

## 1. The problem

An agent run is a black box. You ask one question and get one answer, but behind it the model
may have made ten calls: it picked a tool, misspelled it, retried, got an error, tried something
else. When the answer is wrong you only see the end result. You can't tell which step went
wrong, how long each step took, or what it cost.

agent-tracer records each of those hidden steps so you can look at them afterwards.

## 2. Key ideas

- **Trace**: everything that happened for one request, as a tree.
- **Span**: one timed operation in that tree (`agent.request`, `agent.step`, `llm.call`,
  `tool.get_weather`). It has a start, an end, a parent, a status (OK/ERROR/UNSET), and events
  such as a recorded exception.
- **Attributes**: key/value facts attached to a span, for example `gen_ai.request.model`,
  `gen_ai.usage.input_tokens`, `agent.cost_usd`, `agent.error.kind`.
- **Why OpenTelemetry**: it is the vendor-neutral standard for traces. The same spans can go to
  a local file, Jaeger, Grafana Tempo, Honeycomb, Arize Phoenix or Datadog without changing the
  agent's code. The `gen_ai.*` attribute names follow OpenTelemetry's GenAI conventions.

## 3. How to run

Requires Python 3.10+.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

**Mac/Linux**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Then, on any OS:

```bash
pytest                                        # run the tests
python demo.py                                # run the example agent, print the tree, save traces/demo.jsonl
python -m agent_tracer.view traces/demo.jsonl # replay the saved trace
python demo.py --capture-content              # also record prompts/completions/arguments (redacted)
```

### View in Jaeger

```bash
docker run --rm -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one:latest
python demo.py --otlp
# open http://localhost:16686, choose service "agent-tracer", click "Find Traces"
```

### Live mode (real Claude calls)

```powershell
# Windows (PowerShell)
pip install -e ".[anthropic]"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python demo.py --live "What's the weather in Pune?"
```

```bash
# Mac/Linux
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY="sk-ant-..."
python demo.py --live "What's the weather in Pune?"
```

In live mode the token counts are the real ones from the API response (no `(est)` marker). No
cost is shown for `claude-sonnet-5-5` until you add its price to `agent_tracer/pricing.py`.

## 4. Trace your own agent

```python
from agent_tracer import setup_tracing, trace_request, agent_step, trace_llm, trace_tool, record_usage

setup_tracing("console_tree")            # or "jsonl", "otlp", "memory"

@trace_tool
def lookup_order(order_id: str) -> dict: ...

@trace_llm(model="example-large-model")
def ask_llm(system: str, messages: list[dict]) -> str:
    reply = my_client.chat(system, messages)
    record_usage(reply.input_tokens, reply.output_tokens)   # optional: real counts beat estimates
    return reply.text

with trace_request("support_bot", user_message=question):
    for n in range(1, 6):
        with agent_step(n):
            ...  # call ask_llm(...), then lookup_order(...)
```

Use `record_error("kind", "message")` for problems that are not exceptions (unknown tool, bad
JSON). Exceptions are recorded automatically.

## 5. Design decisions

| Decision | Why |
|---|---|
| **No-op when off** | Without `setup_tracing()`, `get_tracer()` returns OpenTelemetry's no-op tracer. The decorators stay in the code and cost almost nothing. |
| **Content off by default, plus redaction** | Prompts and tool arguments often hold personal data. They are recorded only with `capture_content=True`, and even then emails, phone numbers and API keys are replaced and the text is truncated to 2,000 chars. Error messages are always redacted. |
| **Estimated vs real tokens** | If the LLM function calls `record_usage()`, real counts are used. Otherwise tokens are estimated as `ceil(chars / 4)` and marked `agent.tokens_estimated = True`, so you never mistake a guess for a measurement. |
| **Never guess prices** | Cost comes from an explicit table. An unknown model gets no cost attribute rather than a wrong one. The table ships with placeholder models only. |
| **Tracing never changes behaviour** | Exceptions are recorded on the span, marked ERROR, and re-raised unchanged. Return values pass straight through. |
| **Own provider instead of global** | OpenTelemetry allows setting the global tracer provider only once per process. Keeping the provider in a module variable lets every test (and every `setup_tracing()` call) start fresh. |

## 6. Exercises

1. **Retries**: wrap the LLM call in a retry loop and add an `llm.retry` span per attempt with
   the attempt number and the error that caused it.
2. **Latency histogram**: add a `latency_ms` histogram using the OpenTelemetry metrics API and
   record every `llm.call` and `tool.*` duration.
3. **Arize Phoenix**: run Phoenix locally and point `setup_tracing("otlp", otlp_endpoint=...)`
   at its OTLP endpoint. What shows up without any code change?
4. **LangChain/LangGraph**: trace a LangGraph agent by wrapping its nodes with `agent_step` and
   its tools with `@trace_tool`, then compare with LangChain's own callbacks.

## 7. Interview talking points

- **Observability for agents is tracing, not logging.** Steps are nested and causal; a tree of
  spans shows "step 1 picked a misspelled tool, step 2 recovered" in a way flat logs can't.
- **Vendor-neutral by design.** The agent only talks to the OpenTelemetry API, so the backend
  (file, Jaeger, a SaaS) is a one-line config change.
- **Privacy first.** Content capture is opt-in, redacted and truncated. Traces leave the process,
  so they get treated as potentially public.
- **Honest numbers.** Estimated tokens are flagged as estimates, and unknown prices are left
  blank instead of guessed.
- **Zero-risk instrumentation.** Tracing re-raises every exception and never alters return
  values; with tracing off it's a no-op.
- **Testability.** An in-memory exporter plus a non-global provider makes every behaviour
  (tree shape, error status, totals, redaction) a plain `pytest` assertion.
- **Errors that aren't exceptions.** Unknown tools, bad JSON and step limits are the most common
  agent failures; `record_error` makes them visible as ERROR spans with a `kind`.

## Project layout

```
agent_tracer/
  setup.py        setup_tracing(), get_tracer(), exporters (console tree, jsonl, otlp, memory)
  instrument.py   trace_request(), agent_step(), @trace_llm, @trace_tool, record_usage(), record_error()
  pricing.py      price table + cost calculation
  redact.py       redact() for emails, phone numbers, API keys
  view.py         CLI + load_traces() + render_tree() (standard library only)
examples/
  mini_agent.py   tiny tool-calling agent used by the demo and tests
demo.py
tests/
```
