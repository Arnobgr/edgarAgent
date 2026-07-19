"""Labeled-claim construction for verifier P/R (workstream C.3).

Real claims are rendered from XBRL facts (true verdict `grounded` by
construction); perturbed variants — digits changed, periods shifted, scale
shifted — are `unsupported` by construction. Qualitative pairs come from a
small hand-written template set with sentiment inversion. Deterministic
(seeded RNG) so the labeled set is reproducible and versionable.
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
