"""The spend-side abuse controls: the global USD governor, the per-IP daily
quota and the exact-match answer cache.

These are layers 5, 6 and 8 of the stack in `plans/phase-4-backend-and-deploy.md`.
They cap *spend*; nginx and Turnstile cap *load*, and neither substitutes for
the other — the governor only runs after FastAPI has already accepted the
connection.

All three are checked and recorded through `ApiStore`, so they survive a
restart. An in-memory counter would reset the month's spend every deploy.
"""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from fie.api.store import ApiStore, utcnow

Clock = Callable[[], datetime]

# How much of the month may be spent before `/healthz` says so.
#
# **The month is the only one of the two caps worth an alert, and it is worth
# one *before* it binds.** A day that runs out reopens at midnight UTC with
# nobody doing anything — alerting on that pages for the system working, and
# at $0.35 against a $5.00 month it would page on any day the demo is busy.
# A month that runs out leaves the demo dark until the 1st, and both cures —
# raise the cap, or accept the dark — are decisions someone has to make.
#
# 0.8 of $5.00 is $4.00, which at a full $0.35 day is about three days of
# warning. Deliberately not configurable: one threshold with one reader is
# not a setting (`plans/00-overview.md` §3.1).
MONTH_WARN_FRACTION = 0.8


@dataclass(frozen=True)
class SpendTotals:
    """What the admin endpoint reports and what the alert watches."""

    day: str
    month: str
    day_usd: float
    month_usd: float
    day_cap_usd: float
    month_cap_usd: float

    @property
    def exhausted(self) -> bool:
        return self.day_usd >= self.day_cap_usd or self.month_usd >= self.month_cap_usd

    @property
    def month_nearly_spent(self) -> bool:
        """Whether the month is close enough to its cap to be worth telling
        someone, while the service is still answering.

        This is deliberately true well before `exhausted` is: an alarm that
        fires when the demo is already dark reports history, not a problem
        anyone can still act on.
        """
        return self.month_usd >= MONTH_WARN_FRACTION * self.month_cap_usd


class SpendGovernor:
    """Day + month USD counters, fed by the same `BudgetTracker` that already
    reports per-run cost.

    A run's cost is not known before it runs, so the cap can only be checked
    on the way in and charged on the way out: the last admitted run overshoots
    by whatever it spends. That overshoot is bounded by the public per-run cap
    (`Settings.public_budget_max_cost_usd`, $0.25 against a $0.35 day) — which
    is the reason that cap exists, rather than a coincidence.

    The clock is injected so the day-boundary reset is testable without
    waiting for midnight.
    """

    def __init__(
        self,
        store: ApiStore,
        *,
        daily_cap_usd: float,
        monthly_cap_usd: float,
        clock: Clock = utcnow,
    ) -> None:
        self._store = store
        self._daily_cap = daily_cap_usd
        self._monthly_cap = monthly_cap_usd
        self._clock = clock

    def totals(self) -> SpendTotals:
        now = self._clock()
        day = now.date().isoformat()
        month = f"{now.year:04d}-{now.month:02d}"
        return SpendTotals(
            day=day,
            month=month,
            day_usd=self._store.spend_on(day),
            month_usd=self._store.spend_in_month(month),
            day_cap_usd=self._daily_cap,
            month_cap_usd=self._monthly_cap,
        )

    def refusal(self) -> str | None:
        """The reason to refuse a submission, or None to admit it."""
        totals = self.totals()
        if totals.day_usd >= totals.day_cap_usd:
            return (
                f"daily spend cap reached (${totals.day_usd:.2f} of "
                f"${totals.day_cap_usd:.2f}); live runs resume tomorrow (UTC)"
            )
        if totals.month_usd >= totals.month_cap_usd:
            return (
                f"monthly spend cap reached (${totals.month_usd:.2f} of "
                f"${totals.month_cap_usd:.2f}); live runs resume next month"
            )
        return None

    def record(self, cost_usd: float) -> None:
        if cost_usd <= 0.0:
            return
        self._store.record_spend(self._clock().date().isoformat(), cost_usd)


class IpQuota:
    """Per-IP submissions per UTC day. Generous for a demo, fatal for a
    script: it is what makes a copied API key worth nothing on its own."""

    def __init__(self, store: ApiStore, *, daily_limit: int, clock: Clock = utcnow) -> None:
        self._store = store
        self._limit = daily_limit
        self._clock = clock

    def take(self, ip: str) -> bool:
        """Consume one submission. False when the IP is already at its limit.

        Increments first and compares after, so two concurrent submissions
        from one address cannot both read the old count and both pass."""
        count = self._store.bump_ip(self._clock().date().isoformat(), ip)
        return count <= self._limit


def cache_key(question: str, companies: Sequence[str]) -> str:
    """Normalise case and whitespace, nothing else.

    Deliberately not semantic. A nearest-neighbour cache would happily serve
    the answer to a *different* question, which for a system whose entire
    claim is that every figure is verified against its source is the worst
    failure mode on the menu. Company scope is part of the key, so the same
    words asked about different filers never collide.
    """
    normalized = " ".join(question.split()).casefold()
    scope = ",".join(sorted(c.upper() for c in companies))
    return hashlib.sha256(f"{normalized}|{scope}".encode()).hexdigest()


class AnswerCache:
    """Exact-match answer reuse. Demo questions cluster hard, so the tenth
    visitor asking about Apple's FY2025 revenue costs $0."""

    def __init__(self, store: ApiStore) -> None:
        self._store = store

    def get(self, question: str, companies: Sequence[str]) -> str | None:
        return self._store.cache_get(cache_key(question, companies))

    def put(self, question: str, companies: Sequence[str], result_json: str) -> None:
        self._store.cache_put(cache_key(question, companies), result_json)
