"""Budget guardrail: accumulate token/cost usage from LLM callback data.

The tracker rides in the run's callback list so every LLM call reports its
usage; nodes and the retry helper consult `exceeded` so a run over cap stops
making LLM calls and ends cleanly with a partial-result error state.
"""

import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from fie.state import RunBudget


class BudgetExceededError(RuntimeError):
    """Raised (and converted to an error state) when a budget cap is hit."""


class BudgetTracker(BaseCallbackHandler):
    """Accumulates tokens and cost across every LLM call of one run."""

    def __init__(
        self,
        max_tokens: int,
        max_cost_usd: float,
        prices: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """`prices` maps a model-id substring to (input, output) USD per
        million tokens; unknown models count tokens but cost $0."""
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self._prices = dict(prices or {})
        self.tokens_used = 0
        self.cost_usd_used = 0.0
        self._lock = threading.Lock()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if not usage:
                    continue
                metadata = getattr(message, "response_metadata", None) or {}
                model = str(metadata.get("model_name") or metadata.get("model") or "")
                self.record(usage.get("input_tokens", 0), usage.get("output_tokens", 0), model)

    def record(self, input_tokens: int, output_tokens: int, model: str = "") -> None:
        """Add one call's usage (public so tests can drive the tracker)."""
        input_price, output_price = self._price(model)
        with self._lock:
            self.tokens_used += input_tokens + output_tokens
            self.cost_usd_used += (
                input_tokens * input_price / 1e6 + output_tokens * output_price / 1e6
            )

    def _price(self, model: str) -> tuple[float, float]:
        for key, price in self._prices.items():
            if key in model:
                return price
        return (0.0, 0.0)

    @property
    def exceeded(self) -> bool:
        return self.tokens_used > self.max_tokens or self.cost_usd_used > self.max_cost_usd

    def describe(self) -> str:
        return (
            f"{self.tokens_used:,} tokens (cap {self.max_tokens:,}), "
            f"${self.cost_usd_used:.4f} (cap ${self.max_cost_usd:.2f})"
        )

    def snapshot(self) -> RunBudget:
        """The current usage as the state's `run_budget` value."""
        return RunBudget(
            max_tokens=self.max_tokens,
            max_cost_usd=self.max_cost_usd,
            tokens_used=self.tokens_used,
            cost_usd_used=self.cost_usd_used,
        )
