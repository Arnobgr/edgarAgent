"""The §6 metrics (blueprint) + retrieval ranking metrics.

Pure functions over run results and labeled data — no I/O, no models —
so every number in a report is reproducible from its inputs. The one
LLM-dependent metric (citation accuracy) takes its judgments as input;
the judge itself lives in the agent runner.
"""

import bisect
import math
from dataclasses import dataclass

from fie.eval.datasets import LabeledClaim, SectionRef
from fie.state import EvidenceChunk, RunResult, VerdictLabel

# --- Retrieval metrics (workstream C.1) ---


def _relevant_flags(retrieved: list[EvidenceChunk], relevant: list[SectionRef]) -> list[bool]:
    return [
        any(
            ref.matches(
                ticker=chunk.ticker,
                form_type=chunk.form_type,
                section=chunk.section,
                fiscal_period=chunk.metadata.get("fiscal_period", ""),
            )
            for ref in relevant
        )
        for chunk in retrieved
    ]


def recall_at_k(retrieved: list[EvidenceChunk], relevant: list[SectionRef], k: int) -> float:
    """Fraction of labeled relevant sections covered in the top-k results."""
    if not relevant:
        return 0.0
    top = retrieved[:k]
    hit = sum(
        1
        for ref in relevant
        if any(
            ref.matches(
                ticker=c.ticker,
                form_type=c.form_type,
                section=c.section,
                fiscal_period=c.metadata.get("fiscal_period", ""),
            )
            for c in top
        )
    )
    return hit / len(relevant)


def mrr(retrieved: list[EvidenceChunk], relevant: list[SectionRef]) -> float:
    """Reciprocal rank of the first relevant result."""
    for i, flag in enumerate(_relevant_flags(retrieved, relevant)):
        if flag:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_10(retrieved: list[EvidenceChunk], relevant: list[SectionRef]) -> float:
    """Binary-gain nDCG@10 against the labeled section set."""
    flags = _relevant_flags(retrieved, relevant)[:10]
    dcg = sum(1.0 / math.log2(i + 2) for i, flag in enumerate(flags) if flag)
    ideal_hits = min(len(relevant), 10)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


# --- End-to-end answer metrics (workstream C.3, blueprint §6) ---


def grounding_rate(results: list[RunResult]) -> float:
    """% of claims in final answers with a `grounded` verdict and a cited
    source. Retracted claims are not in the final answer and don't count."""
    total = 0
    grounded = 0
    for result in results:
        for row in result.grounding:
            total += 1
            if row.verdict == "grounded" and row.sources:
                grounded += 1
    return grounded / total if total else 0.0


def hallucination_rate(results: list[RunResult]) -> float:
    """% of claims with no valid support that *survive* to the final answer.
    Retraction is the system working — retracted claims don't count against."""
    total = 0
    unsupported_survivors = 0
    for result in results:
        for row in result.grounding:
            total += 1
            if row.verdict in (None, "unsupported") or not row.sources:
                unsupported_survivors += 1
    return unsupported_survivors / total if total else 0.0


@dataclass(frozen=True)
class CitationJudgment:
    """One LLM-judge ruling: does the cited span support the claim?"""

    claim_id: str
    supported: bool


def citation_accuracy(judgments: list[CitationJudgment]) -> float:
    """% of judged (claim, span) pairs where the span supports the claim.
    Judged by `eval_judge` (claude-opus-4-8) in the runner; spot-check 20%
    by hand — the report lists a deterministic sample for that."""
    if not judgments:
        return 0.0
    return sum(1 for j in judgments if j.supported) / len(judgments)


def numeric_fidelity(results: list[RunResult], fact_values: list[float]) -> float:
    """% of currency/share figures in final answers exactly reconciling to
    an XBRL fact (at the figure's stated precision). Percent figures are
    excluded — they are usually derived, not filed facts.

    `fact_values` is the full store's value column; matching is pure so the
    metric stays reproducible from the report inputs."""
    from fie.tools.reconcile import parse_numbers

    sorted_values = sorted(fact_values)
    total = 0
    matched = 0
    for result in results:
        for number in parse_numbers(result.answer):
            if number.is_percent:
                continue
            total += 1
            if _binary_match(sorted_values, number.value, number.tolerance):
                matched += 1
    return matched / total if total else 1.0


def _binary_match(sorted_values: list[float], value: float, tolerance: float) -> bool:
    lo = bisect.bisect_left(sorted_values, value - tolerance)
    return lo < len(sorted_values) and sorted_values[lo] <= value + tolerance


# --- Verifier precision/recall (workstream C.3, decision 3.5) ---


@dataclass(frozen=True)
class VerifierScore:
    """P/R of the verifier's `grounded` ruling for one claim type.

    Positive class = claim is actually good (`true_verdict == "grounded"`).
    Precision: of claims the verifier accepted, how many were good (a miss
    here is a hallucination let through). Recall: of good claims, how many
    were accepted (a miss here is churn — needless loop-backs)."""

    claim_type: str
    n: int
    precision: float
    recall: float


def verifier_scores(
    labeled: list[LabeledClaim], predictions: dict[str, VerdictLabel]
) -> list[VerifierScore]:
    """Score predicted verdicts against the labeled claim set, per claim
    type plus an "all" rollup. `partial` predictions count as not-accepted
    (conservative: a partial figure match must not green-light a claim)."""
    types: list[str] = ["all", *sorted({c.claim_type for c in labeled})]
    scores = []
    for claim_type in types:
        subset = [c for c in labeled if claim_type in ("all", c.claim_type)]
        accepted_good = sum(
            1
            for c in subset
            if predictions.get(c.id) == "grounded" and c.true_verdict == "grounded"
        )
        accepted = sum(1 for c in subset if predictions.get(c.id) == "grounded")
        good = sum(1 for c in subset if c.true_verdict == "grounded")
        scores.append(
            VerifierScore(
                claim_type=claim_type,
                n=len(subset),
                precision=accepted_good / accepted if accepted else 0.0,
                recall=accepted_good / good if good else 0.0,
            )
        )
    return scores
