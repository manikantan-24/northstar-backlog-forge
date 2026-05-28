"""Per-million-token pricing for LLM models (Claude + Gemini).

Numbers reflect each provider's published list prices as of late 2025.
They're hardcoded here because:
  - The pricing endpoints aren't stable public APIs.
  - The UI cost panel is labeled "estimate" — small drift between list
    price and a customer's actual bill is acceptable.
  - Keeping it local means cost rendering works offline.

To add a model, add an entry to `MODEL_PRICES` keyed by the leading prefix
of the model ID (e.g. "claude-sonnet-4-5"). `estimate_cost_usd()` does a
longest-prefix match so e.g. "claude-sonnet-4-5-20250930" matches the
"claude-sonnet-4-5" entry.

Free-tier convention:
    Gemini's AI Studio free tier currently charges $0 for `gemini-2.5-flash`
    and `gemini-2.5-flash-lite` usage up to its daily quota. We deliberately
    list the PAID-tier rates here so the cost panel reflects list price
    (the published number the user would pay outside free tier). The UI
    tags Gemini models with "(free tier eligible)" so the user knows the
    actual bill on AI Studio's free tier is $0. This matches V2's convention.
"""

from __future__ import annotations

# Per-million-token list prices (USD). input = prompt, output = completion.
MODEL_PRICES: dict[str, dict[str, float]] = {
    # ---- Anthropic Claude ----
    # Claude Sonnet 4 / 4.5 — workhorse model
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4":   {"input": 3.0, "output": 15.0},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-7-sonnet": {"input": 3.0, "output": 15.0},
    # Opus — premium
    "claude-opus-4-5":   {"input": 15.0, "output": 75.0},
    "claude-opus-4":     {"input": 15.0, "output": 75.0},
    "claude-3-opus":     {"input": 15.0, "output": 75.0},
    # Haiku — small / fast
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0},
    "claude-3-5-haiku":  {"input": 0.80, "output": 4.0},
    "claude-3-haiku":    {"input": 0.25, "output": 1.25},

    # ---- Google Gemini (paid-tier list rates; free tier eligible) ----
    # Longest-prefix wins, so the "lite" entry must be longer than the
    # plain "gemini-2.5-flash" prefix — which it is.
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash":      {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro":        {"input": 1.25, "output": 10.00},
    # Older fallbacks in case the model id drifts
    "gemini-2.0-flash":      {"input": 0.10, "output": 0.40},
    "gemini-1.5-flash":      {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro":        {"input": 1.25, "output": 5.00},
}


# Free-tier eligible models — used by the UI to display a "(free tier)" tag
# next to the price. The cost panel still shows the LIST rate computed from
# MODEL_PRICES (see module docstring for why).
FREE_TIER_MODELS: set[str] = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
}


def is_free_tier_eligible(model: str) -> bool:
    """True when `model` is on a provider's free tier (longest-prefix match)."""
    if not model:
        return False
    key = model.lower().strip()
    for prefix in sorted(FREE_TIER_MODELS, key=len, reverse=True):
        if key.startswith(prefix):
            return True
    return False


def _lookup(model: str) -> dict[str, float] | None:
    """Longest-prefix match against MODEL_PRICES, case-insensitive."""
    if not model:
        return None
    key = model.lower().strip()
    # Sort by descending length so e.g. "claude-3-5-sonnet" wins over
    # "claude-3" if both were present.
    for prefix in sorted(MODEL_PRICES.keys(), key=len, reverse=True):
        if key.startswith(prefix):
            return MODEL_PRICES[prefix]
    return None


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Return the estimated USD cost for `input_tokens` + `output_tokens` on `model`.

    Returns None when the model isn't in the price table — the caller should
    render "no price entry" rather than $0.00 so it's obvious the number is
    missing, not zero.
    """
    prices = _lookup(model)
    if prices is None:
        return None
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000.0


def estimate_total_cost_usd(model: str, totals: dict[str, int]) -> float | None:
    """Convenience: `totals` like {"input": N, "output": N}."""
    if not totals:
        return None
    return estimate_cost_usd(
        model,
        int(totals.get("input", 0) or 0),
        int(totals.get("output", 0) or 0),
    )
