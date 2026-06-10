"""Single source of truth for the Gemini model and its on-demand pricing.

Both the cost calculator (phoenix_summary) and the UI read from here so the
displayed model and the numbers used to estimate cost can never drift apart.

Vertex AI / Gemini API standard (paid-tier) text pricing, USD per 1M tokens.
Verified June 2026 — https://ai.google.dev/gemini-api/docs/pricing
"""

from __future__ import annotations

import os

# model id → (input USD / 1M tokens, output USD / 1M tokens)
# Vertex exposes some models under a "-preview" id; keep both so the cost
# estimate stays correct regardless of which one GEMINI_MODEL is set to.
_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3-flash": (0.50, 3.00),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3-5-flash": (1.50, 9.00),
    "gemini-3-1-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}

DEFAULT_MODEL = "gemini-3-flash"
_FALLBACK_PRICE = _PRICING[DEFAULT_MODEL]


def current_model() -> str:
    """The Gemini model AgentWatch is configured to use (env override aware)."""
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def pricing_for(model: str | None = None) -> tuple[float, float]:
    """(input_price_per_1M, output_price_per_1M) for ``model`` in USD.

    Unknown models fall back to the default model's pricing so the cost
    estimate degrades gracefully instead of crashing or returning zero.
    """
    return _PRICING.get(model or current_model(), _FALLBACK_PRICE)


def pricing_note(model: str | None = None) -> str:
    """Human-readable one-liner, e.g. ``Gemini 3 Flash: $0.50/1M in · $3.00/1M out``."""
    m = model or current_model()
    pin, pout = pricing_for(m)
    label = m.replace("gemini-", "Gemini ").replace("-", " ").title()
    return f"{label}: ${pin:g}/1M in · ${pout:g}/1M out"
