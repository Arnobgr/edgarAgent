"""Labeled-claim construction for verifier P/R (workstream C.3).

Real claims are rendered from XBRL facts (true verdict `grounded` by
construction); perturbed variants — digits changed, periods shifted, scale
shifted, balance dates moved within a year — are `unsupported` by
construction. Qualitative pairs come from a small hand-written template set
with sentiment inversion. Deterministic (seeded RNG) so the labeled set is
reproducible and versionable.
"""

import random

from fie.eval.datasets import LabeledClaim
from fie.ingest.store import Fact, IngestStore

# Headline concepts that make natural analyst claims.
_CLAIM_CONCEPTS: list[tuple[str, str]] = [
    ("us-gaap:Revenue", "total revenue"),
    ("us-gaap:NetIncomeLoss", "net income"),
    ("us-gaap:GrossProfit", "gross profit"),
    ("us-gaap:OperatingIncomeLoss", "operating income"),
    ("us-gaap:ResearchAndDevelopmentExpense", "research and development expense"),
    ("us-gaap:CashAndCashEquivalentsAtCarryingValue", "cash and cash equivalents"),
    ("us-gaap:Assets", "total assets"),
    ("us-gaap:StockholdersEquity", "stockholders' equity"),
]

# (statement, inverted statement, supporting evidence text) — the sentiment
# inversion axis for qualitative claims.
_QUALITATIVE_TEMPLATES: list[tuple[str, str, str]] = [
    (
        "{t} identifies supply chain disruption as a risk factor to its business.",
        "{t} states that supply chain disruption poses no risk to its business.",
        "Risk Factors: our business depends on complex global supply chains; "
        "disruptions to component supply could materially harm our results.",
    ),
    (
        "{t}'s management attributes the margin change partly to pricing actions.",
        "{t}'s management states that pricing actions had no effect on margins.",
        "Management's Discussion: the decrease in gross margin was driven "
        "primarily by pricing actions taken during the year, partially offset "
        "by cost reductions.",
    ),
    (
        "{t} reports increased competition in its primary markets.",
        "{t} reports that competition in its primary markets has decreased.",
        "Competition in our markets has continued to intensify, with both "
        "established manufacturers and new entrants expanding their offerings.",
    ),
]


def _fmt_value(value: float) -> str:
    if abs(value) >= 1e9:
        return f"${value / 1e9:.1f} billion"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.1f} million"
    return f"${value:,.0f}"


def _claim_text(fact: Fact, name: str, value: float) -> str:
    period = fact.fiscal_period or "FY"
    when = f"{period} {fact.fiscal_year}" if fact.fiscal_year else (fact.period_end or "")
    return f"{fact.ticker}'s {name} for {when} was {_fmt_value(value)}."


# Balance-sheet concepts: the ones filed as instants, so a fiscal year holds
# four of them and only the date says which.
_BALANCE_CONCEPTS: list[tuple[str, str]] = [
    ("us-gaap:Assets", "total assets"),
    ("us-gaap:StockholdersEquity", "stockholders' equity"),
    ("us-gaap:CashAndCashEquivalentsAtCarryingValue", "cash and cash equivalents"),
]

# Pairs of (year-end, quarter-end) claims to emit. Enough to measure the
# class without swamping the flow axes it sits beside.
_BALANCE_PAIRS = 8


def _balance_claim_text(ticker: str, name: str, year: int, value: float) -> str:
    return f"{ticker}'s {name} at the end of FY {year} was {_fmt_value(value)}."


def build_balance_date_claims(store: IngestStore, rng: random.Random) -> list[LabeledClaim]:
    """Balance-date perturbations: a quarter-end balance stated as the
    year-end figure.

    The `period` axis shifts a fiscal *year* on a flow concept, and every one
    of its 16 claims does. Nothing shifted a balance *within* a year, so the
    reconciler's admission of any same-fiscal-year instant
    (`_period_admissible`, deliberate under ADR 0016) went unmeasured — while
    a fiscal year holds four balance dates per concept, so "total assets at
    the end of FY2026" has three wrong answers the store will return. These
    claims are `unsupported` by construction and are expected to be accepted
    until the admission rule consults the date (ADR 0020)."""
    by_year: dict[tuple[str, str, int], list[Fact]] = {}
    for concept, name in _BALANCE_CONCEPTS:
        for fact in store.query_facts(
            concept_terms=[concept], unit="usd", include_dimensioned=False, limit=10**5
        ):
            if fact.concept != concept or fact.period_start is not None or not fact.period_end:
                continue
            if fact.fiscal_year is None or abs(fact.value) < 1e6:
                continue
            by_year.setdefault((fact.ticker, name, fact.fiscal_year), []).append(fact)

    candidates = []
    for (ticker, name, year), facts in sorted(by_year.items()):
        year_end = max(facts, key=lambda f: f.period_end or "")
        decoys = [
            f
            for f in facts
            if f.period_end != year_end.period_end
            and abs(abs(f.value) - abs(year_end.value)) > abs(year_end.value) * 0.02
        ]
        if decoys:
            candidates.append((ticker, name, year, year_end, min(decoys, key=lambda f: f.value)))
    rng.shuffle(candidates)

    claims: list[LabeledClaim] = []
    for i, (ticker, name, year, year_end, decoy) in enumerate(candidates[:_BALANCE_PAIRS]):
        claims.append(
            LabeledClaim(
                id=f"vc-balreal-{i:03d}",
                text=_balance_claim_text(ticker, name, year, year_end.value),
                claim_type="numeric",
                true_verdict="grounded",
                ticker=ticker,
            )
        )
        claims.append(
            LabeledClaim(
                id=f"vc-balance-{i:03d}",
                text=_balance_claim_text(ticker, name, year, decoy.value),
                claim_type="numeric",
                true_verdict="unsupported",
                perturbation="balance_date",
                ticker=ticker,
            )
        )
    return claims


def build_labeled_claims(
    store: IngestStore, *, per_type_target: int = 100, seed: int = 20260719
) -> list[LabeledClaim]:
    """~`per_type_target` labeled claims from the facts store + templates."""
    rng = random.Random(seed)
    claims: list[LabeledClaim] = []
    facts: list[tuple[Fact, str]] = []
    for concept, name in _CLAIM_CONCEPTS:
        for fact in store.query_facts(
            concept_terms=[concept], fiscal_period="FY", include_dimensioned=False, limit=50
        ):
            if fact.unit == "usd" and abs(fact.value) >= 1e6:
                facts.append((fact, name))
    rng.shuffle(facts)
    # Budget: ~40% real, ~60% perturbed across the three numeric axes.
    quota = max(per_type_target - 2 * len(_QUALITATIVE_TEMPLATES), 10)
    facts = facts[: max(quota // 2, 1)]

    for i, (fact, name) in enumerate(facts):
        claims.append(
            LabeledClaim(
                id=f"vc-real-{i:03d}",
                text=_claim_text(fact, name, fact.value),
                claim_type="numeric",
                true_verdict="grounded",
                ticker=fact.ticker,
            )
        )
        axis = ("digit", "period", "scale")[i % 3]
        if axis == "digit":
            factor = rng.choice([1.13, 0.87, 1.31, 0.71])
            text = _claim_text(fact, name, fact.value * factor)
        elif axis == "period":
            shifted = fact.fiscal_year + rng.choice([-1, 1]) if fact.fiscal_year else 1999
            text = f"{fact.ticker}'s {name} for FY {shifted} was {_fmt_value(fact.value)}."
        else:
            text = _claim_text(fact, name, fact.value * rng.choice([1e3, 1e-3]))
        claims.append(
            LabeledClaim(
                id=f"vc-{axis}-{i:03d}",
                text=text,
                claim_type="numeric",
                true_verdict="unsupported",
                perturbation=axis,
                ticker=fact.ticker,
            )
        )

    claims.extend(build_balance_date_claims(store, rng))

    for i, (real, inverted, evidence) in enumerate(_QUALITATIVE_TEMPLATES):
        for ticker in ("TSLA", "AAPL", "NVDA"):
            claims.append(
                LabeledClaim(
                    id=f"vc-qual-{i}-{ticker}",
                    text=real.format(t=ticker),
                    claim_type="qualitative",
                    true_verdict="grounded",
                    ticker=ticker,
                    evidence_text=evidence,
                )
            )
            claims.append(
                LabeledClaim(
                    id=f"vc-qualinv-{i}-{ticker}",
                    text=inverted.format(t=ticker),
                    claim_type="qualitative",
                    true_verdict="unsupported",
                    perturbation="sentiment",
                    ticker=ticker,
                    evidence_text=evidence,
                )
            )
    return claims
