"""The §6 metrics (blueprint) + retrieval ranking metrics.

Pure functions over run results and labeled data — no I/O, no models —
so every number in a report is reproducible from its inputs. The one
LLM-dependent metric (citation accuracy) takes its judgments as input;
the judge itself lives in the agent runner.
"""

import bisect
import math
import re
from dataclasses import dataclass

from fie.eval.datasets import LabeledClaim, SectionRef
from fie.state import RETRACTED_HEADER, EvidenceChunk, RunResult, VerdictLabel

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
    """Binary-gain nDCG@10 against the labeled section set.

    Section-level labels make several retrieved chunks relevant at once, so
    the ideal ranking is the *retrieved* list perfectly reordered (all
    relevant chunks first) — the standard normalization when judgments
    exist only for retrieved items. Always in [0, 1]."""
    flags = _relevant_flags(retrieved, relevant)
    dcg = sum(1.0 / math.log2(i + 2) for i, flag in enumerate(flags[:10]) if flag)
    ideal_hits = min(sum(flags), 10)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def figure_rank(retrieved: list[EvidenceChunk], relevant: list[str]) -> int | None:
    """1-based rank of the first retrieved chunk that carries the figure.

    Section labels cannot see this: `recall_at_k` credits any chunk of the
    right section, so a retriever that never surfaces the statement table
    still scores well. Figure delivery is what an analyst answer needs, and
    the only thing ADR 0011's numbers never measured (ADR 0020)."""
    wanted = set(relevant)
    return next((i for i, chunk in enumerate(retrieved, start=1) if chunk.chunk_id in wanted), None)


def figure_recall_at_k(ranks: list[int | None], k: int) -> float:
    """Share of figures whose carrying chunk lands inside the top k."""
    if not ranks:
        return 0.0
    return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)


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


def runs_with_no_claims(results: list[RunResult]) -> int:
    """Runs that delivered prose carrying no claims at all.

    A count, not a rate, and deliberately not in `TARGETS`: the point is that
    the number stops being invisible, not that it starts failing runs. Both
    rates above pool claims across runs, so such a run moves neither of them —
    it is not scored badly, it is not scored, and the baseline reads clean
    over a question the system answered with an unverified figure (ADR 0038).

    `retracted` counts as claims: a run whose every claim was pulled out of
    the answer made claims and had them checked, which is this system working
    rather than the hole being counted here."""
    return sum(1 for r in results if r.answer and not r.grounding and not r.retracted)


@dataclass(frozen=True)
class CitationJudgment:
    """One LLM-judge ruling: does the cited span support the claim?"""

    claim_id: str
    supported: bool


def citation_accuracy(judgments: list[CitationJudgment]) -> float:
    """% of judged (claim, span) pairs where the span supports the claim.
    Judged by the `eval_judge` role (see config) in the runner; spot-check 20%
    by hand — the report lists a deterministic sample for that."""
    if not judgments:
        return 0.0
    return sum(1 for j in judgments if j.supported) / len(judgments)


# Inline citations in answers: bracketed chunk ids like
# [0001628280-25-045968:table-0011]. Their digit runs are not figures.
CITATION_RE = re.compile(r"\[[^\[\]]*\]")


def delivered_prose(answer: str) -> str:
    """What the answer actually says: retracted-claims listing and citation
    markup removed.

    Every metric that reads figures out of an answer goes through here, so
    none of them can credit a figure that only survives in the retraction
    disclosure or inside a chunk id (ADR 0020)."""
    return CITATION_RE.sub(" ", answer.split(RETRACTED_HEADER)[0])


def numeric_fidelity(results: list[RunResult], fact_values: list[float]) -> float:
    """% of financial figures in final answers reconciling to an XBRL fact
    (at the figure's stated precision).

    Scoped to figures the answer presents as amounts — a currency symbol or
    an explicit scale word — after stripping bracketed citation markup and
    the retracted-claims section. The 2026-07-27 baseline showed the
    unscoped metric scored 94% non-figures (accession-number fragments,
    dates, list ordinals), which drowned the signal at the store's ~47%
    chance-match floor (ADR 0015). Percent figures stay excluded — they are
    usually derived, not filed facts.

    `fact_values` is the full store's value column; matching is pure so the
    metric stays reproducible from the report inputs.

    Answers render tables under an "(in millions)" header with bare-$ cells
    ("$23,329"), so figures without their own scale word also try the
    answer's context scale — the same rule the reconciler applies to source
    chunks, with the tolerance scaled alongside (ADR 0019). This is the
    table-form sibling of the ADR 0017 suffix-letter gap: 19 of 24 residual
    misses on the 2026-07-28 run were correct in-store values off by the
    table's scale."""
    from fie.tools.reconcile import _context_scale, parse_numbers

    sorted_values = sorted(fact_values)
    total = 0
    matched = 0
    for result in results:
        answer = delivered_prose(result.answer)
        context_scale = _context_scale(answer)
        for number in parse_numbers(answer):
            if number.is_percent or not (number.raw.startswith("$") or number.explicit_scale):
                continue
            total += 1
            forms = [(number.value, number.tolerance)]
            if context_scale is not None and not number.explicit_scale:
                forms.append((number.value * context_scale, number.tolerance * context_scale))
            if any(_binary_match(sorted_values, v, tol) for v, tol in forms):
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
