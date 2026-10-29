"""A deliberately tiny tool-calling agent, so the tracer has something realistic to record.

The LLM replies with JSON: {"tool": "...", "arguments": {...}} or {"final_answer": "..."}.
`ScriptedLLM` replays canned replies (no API key needed); `AnthropicLLM` calls the real API.
"""

from __future__ import annotations

import json

from agent_tracer import agent_step, get_tracer, record_error, record_usage, trace_llm, trace_request, trace_tool

RATES_PER_USD = {"USD": 1.0, "INR": 83.4, "EUR": 0.92, "GBP": 0.79}


@trace_tool
def get_weather(city: str) -> dict:
    return {"city": city, "temp_c": 31, "conditions": "partly cloudy"}


@trace_tool
def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    for code in (from_currency, to_currency):
        if code not in RATES_PER_USD:
            raise ValueError(f"unsupported currency: {code}")
    converted = amount / RATES_PER_USD[from_currency] * RATES_PER_USD[to_currency]
    return {"amount": round(converted, 2), "currency": to_currency}


@trace_tool
def search_docs(query: str) -> dict:
    return {"query": query, "results": ["Getting started guide", "API reference"]}


TOOLS = {"get_weather": get_weather, "convert_currency": convert_currency, "search_docs": search_docs}

SYSTEM_PROMPT = (
    "You are a helpful assistant that can call tools. Reply with JSON only, no other text.\n"
    'To call a tool: {"tool": "<name>", "arguments": {...}}\n'
    'To finish: {"final_answer": "<answer>"}\n'
    "Tools: get_weather(city), convert_currency(amount, from_currency, to_currency), search_docs(query)."
)

DEMO_QUESTION = "Convert 100 USD to INR and tell me the weather in Hyderabad"
DEMO_RESPONSES = [
    '{"tool": "get_wether", "arguments": {"city": "Hyderabad"}}',
    '{"tool": "get_weather", "arguments": {"city": "Hyderabad"}}',
    '{"tool": "convert_currency", "arguments": {"amount": 100, "from_currency": "USD", "to_currency": "INR"}}',
    '{"final_answer": "100 USD is about 8,340 INR, and Hyderabad is 31 C and partly cloudy."}',
]


class ScriptedLLM:
    """Returns pre-written replies in order. Raises once it runs out."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    @trace_llm(model="example-small-model")
    def complete(self, system: str, messages: list[dict]) -> str:
        if self.calls >= len(self.responses):
            raise RuntimeError("ScriptedLLM ran out of responses")
        self.calls += 1
        return self.responses[self.calls - 1]


class AnthropicLLM:
    """Calls Claude through the Anthropic SDK and reports the real token usage."""

    MODEL = "claude-sonnet-5-5"

    def __init__(self, max_tokens: int = 1024) -> None:
        import anthropic  # optional dependency: pip install -e ".[anthropic]"

        self.client = anthropic.Anthropic()
        self.max_tokens = max_tokens

    @trace_llm(model=MODEL)
    def complete(self, system: str, messages: list[dict]) -> str:
        response = self.client.messages.create(
            model=self.MODEL, max_tokens=self.max_tokens, system=system, messages=messages
        )
        record_usage(response.usage.input_tokens, response.usage.output_tokens)
        return "".join(block.text for block in response.content if block.type == "text")


def _parse(reply: str) -> dict:
    text = reply.strip()
    if text.startswith("```"):  # real models sometimes wrap JSON in a code fence
        text = text.strip("`").removeprefix("json").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


def _run_tool(name: str, arguments: dict) -> str:
    if name not in TOOLS:
        with get_tracer().start_as_current_span(f"tool.{name}", attributes={"agent.tool.name": name}):
            record_error("unknown_tool", f"unknown tool {name!r}")
        return f"Error: unknown tool {name!r}. Valid tools: {', '.join(TOOLS)}."
    try:
        result = TOOLS[name](**arguments)
    except Exception as exc:  # already recorded on the tool span by @trace_tool
        return f"Error: tool {name} failed: {exc}"
    return f"Tool {name} returned: {json.dumps(result)}"


def run_agent(llm, user_message: str, max_steps: int = 6) -> str:
    """Loop: ask the LLM, run the tool it picks, feed the result back, until a final answer."""
    messages = [{"role": "user", "content": user_message}]
    with trace_request("mini_agent", user_message=user_message):
        for step in range(1, max_steps + 1):
            with agent_step(step):
                reply = llm.complete(SYSTEM_PROMPT, messages)
                messages.append({"role": "assistant", "content": reply})
                try:
                    data = _parse(reply)
                except ValueError as exc:
                    record_error("bad_json", str(exc))
                    feedback = f"Error: your reply was not valid JSON ({exc}). Reply with JSON only."
                else:
                    if "final_answer" in data:
                        return str(data["final_answer"])
                    feedback = _run_tool(str(data.get("tool")), data.get("arguments") or {})
                messages.append({"role": "user", "content": feedback})
        record_error("step_limit", f"no final answer after {max_steps} steps")
        return f"Stopped: the agent did not reach a final answer within {max_steps} steps."
