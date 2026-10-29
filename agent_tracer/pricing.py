"""Price table and cost calculation.

Exists so cost is computed in exactly one place, from an explicit table. Unknown models get no
cost at all: a missing number is honest, a guessed one is misleading.
"""

from __future__ import annotations

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # model: (input USD per 1M tokens, output USD per 1M tokens)
    # PLACEHOLDER VALUES - replace with current prices from your provider's pricing page.
    "example-small-model": (0.50, 1.50),
    "example-large-model": (3.00, 15.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost of one call in USD, or None if the model is not in the table."""
    prices = PRICES_PER_MTOK.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 6)
