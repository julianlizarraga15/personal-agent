"""Token and estimated-cost accounting for OpenAI Responses API calls."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


PRICING_AS_OF = "2026-07-27"
WEB_SEARCH_USD_PER_CALL = 0.01

# Standard short-context prices in USD per million tokens.  Reasoning tokens
# are included in output_tokens and must not be charged a second time.
MODEL_PRICING: dict[str, tuple[float, float, float, float]] = {
    # model: (uncached input, cached input, cache writes, output)
    "gpt-5-nano": (0.05, 0.005, 0.05, 0.40),
    "gpt-5-mini": (0.25, 0.025, 0.25, 2.00),
    "gpt-5.6-sol": (5.00, 0.50, 6.25, 30.00),
    "gpt-5.6-terra": (2.50, 0.25, 3.125, 15.00),
    "gpt-5.6-luna": (1.00, 0.10, 1.25, 6.00),
}


def _get(value: Any, name: str, default: Any = 0) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _model_family(model: str) -> str | None:
    if model == "gpt-5.6" or model.startswith("gpt-5.6-sol"):
        return "gpt-5.6-sol"
    for family in ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5-nano", "gpt-5-mini"):
        if model == family or model.startswith(f"{family}-"):
            return family
    if model.startswith("gpt-5.6-"):
        return "gpt-5.6-sol"
    return None


@dataclass(frozen=True)
class ModelUsage:
    """Usage for one Responses API request."""

    model: str
    phase: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    web_search_calls: int = 0

    @classmethod
    def from_response(cls, response: Any, requested_model: str, phase: str) -> ModelUsage:
        usage = _get(response, "usage", None)
        input_details = _get(usage, "input_tokens_details", {})
        output_details = _get(usage, "output_tokens_details", {})
        output = _get(response, "output", ()) or ()
        return cls(
            model=str(_get(response, "model", requested_model) or requested_model),
            phase=phase,
            input_tokens=int(_get(usage, "input_tokens", 0) or 0),
            cached_input_tokens=int(_get(input_details, "cached_tokens", 0) or 0),
            cache_write_tokens=int(_get(input_details, "cache_write_tokens", 0) or 0),
            output_tokens=int(_get(usage, "output_tokens", 0) or 0),
            reasoning_tokens=int(_get(output_details, "reasoning_tokens", 0) or 0),
            web_search_calls=sum(1 for item in output if _get(item, "type", None) == "web_search_call"),
        )

    @property
    def billed_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float | None:
        family = _model_family(self.model)
        if family is None:
            return None
        input_rate, cached_rate, write_rate, output_rate = MODEL_PRICING[family]
        uncached = max(self.input_tokens - self.cached_input_tokens - self.cache_write_tokens, 0)
        token_cost = (
            uncached * input_rate
            + self.cached_input_tokens * cached_rate
            + self.cache_write_tokens * write_rate
            + self.output_tokens * output_rate
        ) / 1_000_000
        return token_cost + self.web_search_calls * WEB_SEARCH_USD_PER_CALL


@dataclass
class UsageTotals:
    requests: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    web_search_calls: int = 0
    estimated_cost_usd: float = 0.0
    has_unknown_pricing: bool = False

    def add(self, usage: ModelUsage) -> None:
        self.requests += 1
        self.input_tokens += usage.input_tokens
        self.cached_input_tokens += usage.cached_input_tokens
        self.cache_write_tokens += usage.cache_write_tokens
        self.output_tokens += usage.output_tokens
        self.reasoning_tokens += usage.reasoning_tokens
        self.web_search_calls += usage.web_search_calls
        cost = usage.estimated_cost_usd
        if cost is None:
            self.has_unknown_pricing = True
        else:
            self.estimated_cost_usd += cost

    @property
    def billed_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class SessionUsage:
    """Thread-safe accumulated model usage for one Telegram conversation."""

    by_model: dict[str, UsageTotals] = field(default_factory=dict)
    warning_turns: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, usage: ModelUsage) -> None:
        with self._lock:
            self.by_model.setdefault(usage.model, UsageTotals()).add(usage)

    def mark_warning(self) -> None:
        with self._lock:
            self.warning_turns += 1

    def billed_tokens(self) -> int:
        with self._lock:
            return sum(item.billed_tokens for item in self.by_model.values())

    def format(self) -> str:
        with self._lock:
            if not self.by_model:
                return "No model usage in the current session."
            totals = UsageTotals()
            for item in self.by_model.values():
                totals.requests += item.requests
                totals.input_tokens += item.input_tokens
                totals.cached_input_tokens += item.cached_input_tokens
                totals.cache_write_tokens += item.cache_write_tokens
                totals.output_tokens += item.output_tokens
                totals.reasoning_tokens += item.reasoning_tokens
                totals.web_search_calls += item.web_search_calls
                totals.estimated_cost_usd += item.estimated_cost_usd
                totals.has_unknown_pricing = totals.has_unknown_pricing or item.has_unknown_pricing
            estimate = f"${totals.estimated_cost_usd:.6f}" if totals.estimated_cost_usd < 0.01 else f"${totals.estimated_cost_usd:.4f}"
            if totals.has_unknown_pricing:
                estimate += " plus usage with unknown pricing"
            lines = [
                "Usage for this session",
                f"requests: {totals.requests}",
                f"input tokens: {totals.input_tokens:,} (cached {totals.cached_input_tokens:,}; cache writes {totals.cache_write_tokens:,})",
                f"output tokens: {totals.output_tokens:,} (reasoning {totals.reasoning_tokens:,})",
                f"web searches: {totals.web_search_calls}",
                f"estimated cost: {estimate}",
                "models: " + ", ".join(f"{model} ({item.requests})" for model, item in sorted(self.by_model.items())),
            ]
            if self.warning_turns:
                lines.append(f"high-usage turn warnings: {self.warning_turns}")
            lines.append(f"pricing snapshot: {PRICING_AS_OF}")
            return "\n".join(lines)
