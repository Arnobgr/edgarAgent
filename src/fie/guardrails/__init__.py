"""Guardrails: budgets, timeouts, iteration caps, sanitization.

Phase 1 ships the real enforcement: per-run token/cost budgets (abort with a
partial-result error state on cap), an async timeout wrapper applied to every
node, and retry-with-backoff for LLM/tool calls in one place. Iteration caps
live in the graph's conditional edges (`fie.graph`). Input/output
sanitization arrives in Phase 6.
"""

from fie.guardrails.budget import BudgetExceededError, BudgetTracker
from fie.guardrails.execution import (
    EmptyStructuredOutputError,
    NodeFn,
    SupportsError,
    ainvoke_with_retry,
    guarded,
)

__all__ = [
    "BudgetExceededError",
    "BudgetTracker",
    "EmptyStructuredOutputError",
    "NodeFn",
    "SupportsError",
    "ainvoke_with_retry",
    "guarded",
]
