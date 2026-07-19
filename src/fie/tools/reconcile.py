"""Numeric reconciliation.

Phase 3 default: `XbrlNumericReconciler` — a cheap model maps the claim
text to candidate XBRL `(concept, period, unit)` triples, a deterministic
SQLite lookup follows, and the claim is `grounded` only on an exact match
(at the claim's stated precision) against a stored fact; the supporting
span is the fact row itself, cited by accession number. When no fact
matches, table-chunk cell matching is the fallback; otherwise
`unsupported`, with feedback naming what was searched (ADR 0012).

The Phase 1 `InterimNumericReconciler` (parse-and-match at stated
precision, ADR 0005) remains as the in-chunk matching engine behind that
table fallback. Both implement the `NumericReconciler` protocol the verify
node sees.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from fie.ingest.store import Fact, IngestStore
from fie.state import EvidenceChunk, VerdictLabel

logger = logging.getLogger(__name__)

_SCALES = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
}

# Filings print accounting negatives in parentheses: "(8)%", "$(1,234)".
# These parse as magnitudes — matching is sign-blind, because claims state
# direction in words ("declined 8%"), not signs.
_NUM_RE = re.compile(
    r"(?P<currency>\$\s*)?"
    r"(?P<lparen>\(\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<rparen>\s*\))?"
    r"(?:\s*(?P<scale>thousand|million|billion|trillion))?"
    r"(?P<pct>\s*(?:%|percent\b))?",
    re.IGNORECASE,
)

# "(In millions)" / "$ in thousands" table headers: the implied scale for
# bare figures in that chunk.
_CONTEXT_RE = re.compile(r"in (thousands|millions|billions)", re.IGNORECASE)

_SPAN_WINDOW = 70


@dataclass(frozen=True)
class ParsedNumber:
    """One number found in text, normalized to its absolute value."""

    value: float
    tolerance: float  # half a unit of the last stated significant digit
    is_percent: bool
    explicit_scale: bool  # had its own scale word ("391 million")
    raw: str  # the full matched text, for feedback/spans
    start: int


@dataclass(frozen=True)
class ReconcileOutcome:
    """The reconciler's ruling on one numeric claim."""

    verdict: VerdictLabel
    supporting_span: str | None
    feedback: str | None


@runtime_checkable
class NumericReconciler(Protocol):
    """Anything that can rule on a numeric claim against cited chunks.

    Phase 3's XBRL facts lookup implements this same protocol."""

    def reconcile(self, claim_text: str, chunks: list[EvidenceChunk]) -> ReconcileOutcome:
        """Rule grounded/partial/unsupported for `claim_text` given `chunks`."""
        ...


def parse_numbers(text: str) -> list[ParsedNumber]:
    """Extract normalized numbers from text.

    Bare 4-digit integers in 1900-2099 with no $, %, comma, decimal, or scale
    word are treated as years and skipped (interim heuristic, ADR 0005).
    """
    numbers: list[ParsedNumber] = []
    for match in _NUM_RE.finditer(text):
        num = match.group("num")
        scale_word = match.group("scale")
        pct = match.group("pct")
        currency = match.group("currency")
        plain = num.replace(",", "")

        is_bare = not currency and not scale_word and not pct
        if is_bare and "," not in num and "." not in num and len(plain) == 4:
            year = int(plain)
            if 1900 <= year <= 2099:
                continue

        scale = _SCALES[scale_word.lower()] if scale_word else 1.0
        decimals = len(num.split(".")[1]) if "." in num else 0
        numbers.append(
            ParsedNumber(
                value=float(plain) * scale,
                tolerance=0.5 * (10**-decimals) * scale,
                is_percent=bool(pct),
                explicit_scale=bool(scale_word),
                raw=match.group(0).strip(),
                start=match.start(),
            )
        )
    return numbers


def _context_scale(text: str) -> float | None:
    """The scale implied by an "in millions"-style marker, if present."""
    match = _CONTEXT_RE.search(text)
    if match is None:
        return None
    return _SCALES[match.group(1).lower().rstrip("s")]


def _span_around(text: str, number: ParsedNumber) -> str:
    """A short verbatim window of source text around a matched number."""
    lo = max(0, number.start - _SPAN_WINDOW)
    hi = min(len(text), number.start + len(number.raw) + _SPAN_WINDOW)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{text[lo:hi].strip()}{suffix}"


class InterimNumericReconciler:
    """Parse-and-match reconciliation at the claim's stated precision."""

    def reconcile(self, claim_text: str, chunks: list[EvidenceChunk]) -> ReconcileOutcome:
        claim_numbers = parse_numbers(claim_text)
        if not claim_numbers:
            return ReconcileOutcome(
                verdict="unsupported",
                supporting_span=None,
                feedback="no numeric value could be parsed from the claim text",
            )
        if not chunks:
            return ReconcileOutcome(
                verdict="unsupported",
                supporting_span=None,
                feedback="claim cites no evidence chunks",
            )

        matched_spans: list[str] = []
        missing: list[str] = []
        for claim_number in claim_numbers:
            span = self._find_match(claim_number, chunks)
            if span is None:
                missing.append(claim_number.raw)
            else:
                matched_spans.append(span)

        if not missing:
            return ReconcileOutcome(
                verdict="grounded", supporting_span=matched_spans[0], feedback=None
            )

        cited = ", ".join(c.chunk_id for c in chunks)
        feedback = (
            f"figure(s) {', '.join(missing)} not found in cited chunk(s) {cited} — "
            "re-retrieve evidence containing the exact figure or retract the claim"
        )
        return ReconcileOutcome(
            verdict="partial" if matched_spans else "unsupported",
            supporting_span=matched_spans[0] if matched_spans else None,
            feedback=feedback,
        )

    def _find_match(self, claim_number: ParsedNumber, chunks: list[EvidenceChunk]) -> str | None:
        for chunk in chunks:
            context_scale = _context_scale(chunk.text)
            for source_number in parse_numbers(chunk.text):
                if source_number.is_percent != claim_number.is_percent:
                    continue
                candidates = [source_number.value]
                if (
                    context_scale is not None
                    and not source_number.explicit_scale
                    and not source_number.is_percent
                ):
                    candidates.append(source_number.value * context_scale)
                if any(
                    abs(candidate - claim_number.value) <= claim_number.tolerance
                    for candidate in candidates
                ):
                    return _span_around(chunk.text, source_number)
        return None


# --- Phase 3: exact XBRL reconciliation (workstream B.4, ADR 0012) ---


class FactCandidate(BaseModel):
    """One figure's candidate XBRL mapping, as proposed by the cheap model."""

    value: float = Field(description="The claim figure as an absolute number (e.g. 96773000000)")
    concept_terms: list[str] = Field(
        description="Candidate concept/label search terms, e.g. "
        "['RevenueFromContractWithCustomer', 'Revenues', 'net revenue']"
    )
    fiscal_year: int | None = Field(
        default=None, description="Fiscal year, if the claim states one"
    )
    fiscal_period: str | None = Field(
        default=None, description="'FY' or 'Q1'..'Q4', if the claim implies one"
    )
    unit: str | None = Field(default=None, description="'usd', 'shares', 'pure', 'usdPerShare'...")


class FactMappingOutput(BaseModel):
    """Structured output of the claim → fact-query mapping."""

    candidates: list[FactCandidate] = Field(default_factory=list)


_MAPPING_SYSTEM = (
    "You map figures in a financial claim to XBRL fact queries. For every "
    "numeric figure in the claim (ignore years used as dates), emit one "
    "candidate: the figure as an absolute number (apply scale words — "
    "'$96.8 billion' → 96800000000; percentages as the printed number), "
    "likely US-GAAP concept names and label words, the fiscal year/period "
    "the claim refers to (null if unstated), and the unit."
)


class XbrlNumericReconciler:
    """Exact numeric reconciliation against the local XBRL facts store.

    Flow per claim: parse figures → cheap-model mapping to candidate
    (concept, period, unit) triples → deterministic SQLite lookup → a
    figure is grounded only if a stored fact matches it exactly at the
    claim's stated precision. Figures with no fact match fall back to
    cell-matching against cited *table* chunks (display provenance); still
    unmatched → unsupported, with feedback naming what was searched.
    """

    def __init__(
        self,
        store: IngestStore,
        mapper: Any,
        *,
        fallback: InterimNumericReconciler | None = None,
    ) -> None:
        """`mapper` is a runnable producing `FactMappingOutput` from messages
        (a chat model already bound with `.with_structured_output`)."""
        self._store = store
        self._mapper = mapper
        self._fallback = fallback or InterimNumericReconciler()

    def reconcile(self, claim_text: str, chunks: list[EvidenceChunk]) -> ReconcileOutcome:
        claim_numbers = parse_numbers(claim_text)
        if not claim_numbers:
            return ReconcileOutcome(
                verdict="unsupported",
                supporting_span=None,
                feedback="no numeric value could be parsed from the claim text",
            )
        candidates = self._map_claim(claim_text)
        tickers = sorted({c.ticker for c in chunks}) or None

        matched_spans: list[str] = []
        missing: list[str] = []
        searched: list[str] = []
        table_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        for number in claim_numbers:
            span = self._match_fact(number, candidates, tickers, searched)
            if span is None and table_chunks:
                span = self._fallback._find_match(number, table_chunks)
            if span is None:
                missing.append(number.raw)
            else:
                matched_spans.append(span)

        if not missing:
            return ReconcileOutcome(
                verdict="grounded", supporting_span=matched_spans[0], feedback=None
            )
        searched_note = (
            f"searched XBRL concepts: {', '.join(sorted(set(searched))) or '(no mapping produced)'}"
        )
        fallback_note = (
            f" and {len(table_chunks)} cited table chunk(s)"
            if table_chunks
            else "; no table chunks cited"
        )
        return ReconcileOutcome(
            verdict="partial" if matched_spans else "unsupported",
            supporting_span=matched_spans[0] if matched_spans else None,
            feedback=(
                f"figure(s) {', '.join(missing)} not found in the XBRL facts store — "
                f"{searched_note}{fallback_note}. Re-check the figure against the "
                "filing or retract the claim."
            ),
        )

    def _map_claim(self, claim_text: str) -> list[FactCandidate]:
        messages = [("system", _MAPPING_SYSTEM), ("human", f"Claim: {claim_text}")]
        try:
            output = self._mapper.invoke(messages)
        except Exception as exc:
            logger.warning("fact-mapping model failed (%s); falling back to table match", exc)
            return []
        if isinstance(output, FactMappingOutput):
            return output.candidates
        return FactMappingOutput.model_validate(output).candidates

    def _match_fact(
        self,
        number: ParsedNumber,
        candidates: list[FactCandidate],
        tickers: list[str] | None,
        searched: list[str],
    ) -> str | None:
        """The candidate mappings whose value matches this figure drive the
        lookup; a percent figure additionally tries its /100 'pure' form."""
        for candidate in candidates:
            claim_forms = [(number.value, number.tolerance)]
            if number.is_percent:
                claim_forms.append((number.value / 100.0, number.tolerance / 100.0))
            if not any(abs(candidate.value - v) <= tol for v, tol in claim_forms):
                continue
            searched.extend(candidate.concept_terms)
            facts = self._store.query_facts(
                tickers=tickers,
                concept_terms=candidate.concept_terms or None,
                fiscal_year=candidate.fiscal_year,
                fiscal_period=candidate.fiscal_period,
                unit=candidate.unit,
            )
            for fact in facts:
                if self._value_matches(number, fact):
                    return _fact_span(fact)
        return None

    @staticmethod
    def _value_matches(number: ParsedNumber, fact: Fact) -> bool:
        """Exact after scale normalization: the stored fact equals the claim
        figure at the claim's stated precision (half a unit of the last
        significant digit — '$96.8B' matches 96,773,000,000). Percent claims
        also try the /100 ratio form XBRL stores, with the tolerance scaled
        down accordingly."""
        pairs = [(number.value, number.tolerance)]
        if number.is_percent:
            pairs.append((number.value / 100.0, number.tolerance / 100.0))
        return any(abs(fact.value - v) <= tol for v, tol in pairs)


def _fact_span(fact: Fact) -> str:
    period = fact.fiscal_period or ""
    year = str(fact.fiscal_year or "")
    when = " ".join(x for x in (period, year) if x) or (fact.period_end or "?")
    dims = ""
    if fact.dimensions:
        dims = f" [{', '.join(f'{k}={v}' for k, v in sorted(fact.dimensions.items()))}]"
    return (
        f"XBRL {fact.concept} ({fact.label or 'no label'}){dims} = {fact.value:g} "
        f"{fact.unit or ''} for {when}, {fact.form_type or '?'} acc {fact.accession_no}"
    )
