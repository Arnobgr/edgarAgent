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
    # Suffix-letter notation ("$298,085M", "$71.6B") is how models and filings
    # write compact figures. Without these a figure parses 10^6 to 10^9 too small
    # and can never reconcile against a store held in units — measured on the
    # 2026-07-27 re-baseline as numeric_fidelity 0.654 vs 0.877 (ADR 0017).
    "m": 1e6,
    "mm": 1e6,
    "b": 1e9,
    "bn": 1e9,
}

# Filings print accounting negatives in parentheses: "(8)%", "$(1,234)".
# These parse as magnitudes — matching is sign-blind, because claims state
# direction in words ("declined 8%"), not signs.
_NUM_RE = re.compile(
    r"(?P<currency>\$\s*)?"
    r"(?P<lparen>\(\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<rparen>\s*\))?"
    # Letter suffixes need a trailing word boundary or "$5 Model 3" reads as
    # "$5 million"; the spelled-out words keep their original loose match.
    r"(?:\s*(?P<scale>thousand|million|billion|trillion|(?:mm|bn|m|b)\b))?"
    r"(?P<pct>\s*(?:%|percent\b))?",
    re.IGNORECASE,
)

# "(In millions)" / "$ in thousands" table headers: the implied scale for
# bare figures in that chunk.
_CONTEXT_RE = re.compile(r"in (thousands|millions|billions)", re.IGNORECASE)

# A month name directly before a bare 1-31 marks a day-of-month ("September
# 27, 2025"), not an amount.
_MONTH_RE = re.compile(
    r"(?:january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\s+$",
    re.IGNORECASE,
)

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


def _is_date_day(text: str, number: ParsedNumber) -> bool:
    """Whether this number is a day-of-month rather than an amount."""
    if "$" in number.raw or number.is_percent or number.explicit_scale:
        return False
    if not (1 <= number.value <= 31 and number.value == int(number.value)):
        return False
    return _MONTH_RE.search(text[: number.start]) is not None


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
        # Day-of-month fragments ("as of September 27, 2025" → 27) are not
        # obligations for the cited-text path either — the same defect class
        # ADR 0016 removed from the XBRL path grounded two more claims here
        # in the 2026-07-27 re-baseline (ADR 0018). A claim that is nothing
        # but a date keeps the original behavior.
        amounts = [n for n in claim_numbers if not _is_date_day(claim_text, n)]
        if amounts:
            claim_numbers = amounts
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
        default=None,
        description="'FY', 'Q1'..'Q4', 'YTD6' (six months), or 'YTD9' (nine "
        "months), if the claim implies one; null for balance-sheet 'as of' "
        "figures",
    )
    unit: str | None = Field(default=None, description="'usd', 'shares', 'pure', 'usdPerShare'...")
    numerator_terms: list[str] = Field(
        default_factory=list,
        description="For a ratio percentage (margin, % of revenue): concept "
        "terms for the numerator, e.g. ['GrossProfit']",
    )
    denominator_terms: list[str] = Field(
        default_factory=list,
        description="For a ratio percentage: concept terms for the denominator, e.g. ['Revenues']",
    )


class FactMappingOutput(BaseModel):
    """Structured output of the claim → fact-query mapping."""

    candidates: list[FactCandidate] = Field(default_factory=list)


_MAPPING_SYSTEM = (
    "You map figures in a financial claim to XBRL fact queries. For every "
    "monetary, share or percent figure in the claim (ignore dates, quarter "
    "labels and form names), emit one candidate: the figure as an absolute "
    "number (apply scale words — '$96.8 billion' → 96800000000; percentages "
    "as the printed number), likely US-GAAP concept names and label words, "
    "the fiscal year/period the claim refers to (null if unstated; use the "
    "company's fiscal calendar; always set fiscal_period when the claim "
    "names one — 'for FY 2023' → 'FY', 'in Q2 2025' → 'Q2', 'YTD6'/'YTD9' "
    "for six/nine-month figures — and null period for balance-sheet 'as "
    "of' figures), and the unit. For "
    "a percentage that is a ratio of filed amounts (a margin, a % of "
    "revenue), also give numerator_terms and denominator_terms. For a "
    "year-over-year change percentage ('revenue up 41%'), give the changing "
    "concept as both numerator_terms and denominator_terms with the current "
    "period's fiscal year. For a change amount ('an increase of $433 "
    "million'), give the changing concept's terms with the current period's "
    "fiscal year."
)


class XbrlNumericReconciler:
    """Exact numeric reconciliation against the local XBRL facts store.

    Flow per claim: parse figures (financial figures only — currency,
    scale-worded, percent; see ADR 0016) → cheap-model mapping to candidate
    (concept, period, unit) triples → deterministic SQLite lookup, with the
    stated period first and a period-free (never year-free) retry → a
    figure is grounded only if a stored fact matches it exactly at the
    claim's stated precision. Ratio and year-over-year change percentages
    recompute from operand facts named by the mapping; a percent consistent
    with verified figures in its own claim grounds by recomputation too
    (ADR 0018). Figures with no fact match fall back to cell-matching
    against cited *table* chunks (display provenance); still unmatched →
    unsupported, with feedback naming what was searched.
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

    def close(self) -> None:
        """Release the facts-store connection. The production wiring opens
        one store per run; leaving them open leaks a SQLite connection per
        question across an eval run (ADR 0015)."""
        self._store.close()

    def reconcile(self, claim_text: str, chunks: list[EvidenceChunk]) -> ReconcileOutcome:
        claim_numbers = parse_numbers(claim_text)
        if not claim_numbers:
            return ReconcileOutcome(
                verdict="unsupported",
                supporting_span=None,
                feedback="no numeric value could be parsed from the claim text",
            )
        # Only financial figures — currency, explicit scale, percent — are
        # reconciliation obligations. Dates, quarter ordinals and counts
        # ("Q1", "September 27", "more than 12 months") are not amounts and
        # XBRL has nothing to say about them; demanding they reconcile made
        # 18/53 non-grounded claims in the 2026-07-27 baseline (ADR 0016).
        figures = [n for n in claim_numbers if "$" in n.raw or n.explicit_scale or n.is_percent]
        if not figures:
            # A numeric claim with no amounts ("lead times of more than 12
            # months") is prose detail — verify it against the cited text.
            return self._fallback.reconcile(claim_text, chunks)
        candidates = self._map_claim(claim_text)
        tickers = sorted({c.ticker for c in chunks}) or None

        matched_spans: list[str] = []
        unmatched: list[ParsedNumber] = []
        searched: list[str] = []
        operands: list[float] = []
        table_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        for number in figures:
            span = self._match_fact(number, candidates, tickers, searched)
            if span is None and table_chunks:
                span = self._fallback._find_match(number, table_chunks)
            if span is None:
                unmatched.append(number)
            else:
                matched_spans.append(span)
                if not number.is_percent:
                    operands.append(number.value)

        # Second pass: a percent that is arithmetic over figures already
        # verified in the same claim ("declined 5.7% ... from $316,199M to
        # $298,085M") adds no unverified information — ground it by
        # recomputation at its stated precision (ADR 0018).
        missing: list[str] = []
        for number in unmatched:
            span = _derive_percent(number, operands) if number.is_percent else None
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
        lookup; a percent figure additionally tries its /100 'pure' form and
        a ratio recomputation when the mapping names both operands."""
        for candidate in candidates:
            claim_forms = [(number.value, number.tolerance)]
            if number.is_percent:
                claim_forms.append((number.value / 100.0, number.tolerance / 100.0))
            # Magnitude pairing: the parser is sign-blind ('$-23.0 million'
            # → 23.0 million) but the mapper echoes the printed sign.
            if not any(abs(abs(candidate.value) - v) <= tol for v, tol in claim_forms):
                continue
            searched.extend(candidate.concept_terms)
            for fiscal_period in _period_attempts(candidate.fiscal_period):
                facts = self._store.query_facts(
                    tickers=tickers,
                    concept_terms=candidate.concept_terms or None,
                    fiscal_year=candidate.fiscal_year,
                    fiscal_period=fiscal_period,
                    unit=candidate.unit,
                )
                facts = [f for f in facts if _period_admissible(f, fiscal_period, candidate)]
                for fact in _year_end_only(facts, candidate):
                    if matches_at_stated_precision(number, fact.value):
                        return _fact_span(fact)
            if number.is_percent and candidate.numerator_terms and candidate.denominator_terms:
                span = self._match_ratio(number, candidate, tickers, searched)
                if span is not None:
                    return span
            if not number.is_percent and candidate.fiscal_year is not None:
                span = self._match_difference(number, candidate, tickers)
                if span is not None:
                    return span
        return None

    def _match_difference(
        self,
        number: ParsedNumber,
        candidate: FactCandidate,
        tickers: list[str] | None,
    ) -> str | None:
        """Ground a change amount by recomputing it from filed facts.

        "Declined by $2,057 million" and Q4 figures ("Q4 FY2025 revenue was
        $102.5 billion" — never filed; every analyst derives it as FY minus
        YTD9) are differences of filed values. Two forms, both requiring the
        same ticker and concept so like subtracts from like (ADR 0019):
        year-over-year at the same period, and FY minus YTD9 within the
        year. The claim's printed precision gates the match."""
        if candidate.fiscal_year is None:
            return None
        pairs: list[tuple[list[Fact], list[Fact]]] = []
        for fiscal_period in _period_attempts(candidate.fiscal_period):
            current = self._store.query_facts(
                tickers=tickers,
                concept_terms=candidate.concept_terms or None,
                fiscal_year=candidate.fiscal_year,
                fiscal_period=fiscal_period,
                limit=40,
            )
            current = [f for f in current if _period_admissible(f, fiscal_period, candidate)]
            prior = self._store.query_facts(
                tickers=tickers,
                concept_terms=candidate.concept_terms or None,
                fiscal_year=candidate.fiscal_year - 1,
                fiscal_period=fiscal_period,
                limit=40,
            )
            prior = [f for f in prior if _period_admissible(f, fiscal_period, candidate)]
            pairs.append((current, prior))
        full_year = self._store.query_facts(
            tickers=tickers,
            concept_terms=candidate.concept_terms or None,
            fiscal_year=candidate.fiscal_year,
            fiscal_period="FY",
            limit=40,
        )
        ytd9 = self._store.query_facts(
            tickers=tickers,
            concept_terms=candidate.concept_terms or None,
            fiscal_year=candidate.fiscal_year,
            fiscal_period="YTD9",
            limit=40,
        )
        pairs.append((full_year, ytd9))
        for currents, priors in pairs:
            for a in currents:
                for b in priors:
                    if (a.ticker, a.concept, a.label) != (b.ticker, b.concept, b.label):
                        continue
                    if abs(abs(abs(a.value) - abs(b.value)) - number.value) <= number.tolerance:
                        return _difference_span(a, b)
        return None

    def _match_ratio(
        self,
        number: ParsedNumber,
        candidate: FactCandidate,
        tickers: list[str] | None,
        searched: list[str],
    ) -> str | None:
        """Ground a ratio percentage by recomputing it from filed facts.

        Margins and %-of-revenue figures are derived, never filed — but both
        operands are (14/53 non-grounded claims in the 2026-07-27 baseline
        were such percents, ADR 0016). Operand pairs must share ticker,
        accession, fiscal year and period so the ratio is coherent; the
        claim's printed precision gates the match, the same tolerance rule
        as direct lookups.

        Change percentages ("up 41% from FY2025") are a second form: the
        prior year's value of the *same concept* is the base, so the pair
        gate swaps accession equality for concept equality — the comparative
        may sit in a different filing, but like must compare to like
        (ADR 0018)."""
        searched.extend(candidate.numerator_terms + candidate.denominator_terms)
        target = number.value / 100.0
        tolerance = number.tolerance / 100.0
        for fiscal_period in _period_attempts(candidate.fiscal_period):
            numerators = self._store.query_facts(
                tickers=tickers,
                concept_terms=candidate.numerator_terms,
                fiscal_year=candidate.fiscal_year,
                fiscal_period=fiscal_period,
                limit=40,
            )
            denominators = self._store.query_facts(
                tickers=tickers,
                concept_terms=candidate.denominator_terms,
                fiscal_year=candidate.fiscal_year,
                fiscal_period=fiscal_period,
                limit=40,
            )
            numerators = [f for f in numerators if _period_admissible(f, fiscal_period, candidate)]
            denominators = [
                f for f in denominators if _period_admissible(f, fiscal_period, candidate)
            ]
            for num in numerators:
                for den in denominators:
                    if den.value == 0 or (
                        num.ticker,
                        num.accession_no,
                        num.fiscal_year,
                        num.fiscal_period,
                    ) != (den.ticker, den.accession_no, den.fiscal_year, den.fiscal_period):
                        continue
                    if abs(abs(num.value / den.value) - target) <= tolerance:
                        return _ratio_span(num, den)
            if candidate.fiscal_year is not None:
                priors = self._store.query_facts(
                    tickers=tickers,
                    concept_terms=candidate.denominator_terms,
                    fiscal_year=candidate.fiscal_year - 1,
                    fiscal_period=fiscal_period,
                    limit=40,
                )
                priors = [f for f in priors if _period_admissible(f, fiscal_period, candidate)]
                for num in numerators:
                    for den in priors:
                        if den.value == 0 or (num.ticker, num.fiscal_period, num.concept) != (
                            den.ticker,
                            den.fiscal_period,
                            den.concept,
                        ):
                            continue
                        growth = abs(num.value) / abs(den.value) - 1.0
                        if abs(abs(growth) - target) <= tolerance:
                            return _growth_span(num, den)
        return None


def matches_at_stated_precision(number: ParsedNumber, value: float) -> bool:
    """Exact after scale normalization: `value` equals the stated figure at
    the figure's own stated precision (half a unit of the last significant
    digit — '$96.8B' matches 96,773,000,000). Percent figures also try the
    /100 ratio form XBRL stores, with the tolerance scaled down accordingly.
    Magnitudes are compared — matching is sign-blind on both sides ('$-23.0
    million' parses as 23.0 million; claims state direction in words, not
    signs).

    This is ADR 0012's grounding rule and the single definition of "the same
    figure" in the system. `expected_fact_coverage` scores against it too:
    two components disagreeing about what counts as the same number is how a
    metric ends up reading something other than what it names (ADR 0020)."""
    pairs = [(number.value, number.tolerance)]
    if number.is_percent:
        pairs.append((number.value / 100.0, number.tolerance / 100.0))
    return any(abs(abs(value) - v) <= tol for v, tol in pairs)


def _period_attempts(fiscal_period: str | None) -> list[str | None]:
    """The stated period first, then a relaxed pass for instants only
    (see `_period_admissible`). The fiscal *year* is never dropped: the
    right value under the wrong year must stay unsupported (ADR 0016)."""
    return [fiscal_period, None] if fiscal_period is not None else [None]


def _period_admissible(fact: Fact, attempt: str | None, candidate: FactCandidate) -> bool:
    """Whether a queried fact's period may satisfy this mapping.

    Period omission must default safe, not maximally permissive
    (ADR 0016): a nine-month value grounding a full-year claim was the one
    false accept in the verifier P/R run (vc-period-019).

    - An exact-period query already filtered — everything is admissible.
    - The relaxed pass after a stated period reaches *instants only*:
      balance-sheet facts carry no period label at all (ADR 0015), while
      duration facts are always labeled, so a labeled fact under a
      different period is a genuine mismatch.
    - A mapping with a fiscal year but no period is a year-scoped claim —
      the mapper omits the period ('FY 2023' → None in practice), so only
      full-year and instant facts qualify; quarterly/YTD values must not.
    - No year, no period: value, concept and ticker are the only gates."""
    if attempt is not None:
        return True
    if candidate.fiscal_period is not None:
        return fact.fiscal_period is None
    if candidate.fiscal_year is not None:
        return fact.fiscal_period is None or fact.fiscal_period == "FY"
    return True


def _year_end_only(facts: list[Fact], candidate: FactCandidate) -> list[Fact]:
    """For a year-scoped balance claim, only the year's closing balance.

    `_period_admissible` admits any instant of the stated fiscal year, which
    was safe while instants had no date and unsafe the moment they did: a
    fiscal year holds four balance dates per concept, so "total assets at the
    end of FY2024" has three wrong answers the store returns. All 8 claims of
    that shape were accepted when the class was first measured, taking
    verifier precision to 0.889 (ADR 0021).

    Durations pass through untouched, and a claim that names a *quarter*
    still reaches it through the stated-period query. The narrowing applies
    whenever the mapping is year-scoped without naming a quarter — period
    `None` or `FY`, both of which the mapper emits for "at the end of FY
    2024". Gating on `None` alone left 3 of the 8 accepted: the relaxed
    instant pass after a stated `FY` re-admits every balance of the year.

    The cost is a claim about a *quarterly* balance that does not name its
    quarter; it falls through to the cited table-chunk match. The ratio and
    difference paths keep admitting any same-year instant: their operand
    pairs are gated on matching accession and period, and no claim of that
    shape is labeled, so the class stays unmeasured rather than assumed
    safe."""
    if candidate.fiscal_year is None or (candidate.fiscal_period or "FY") != "FY":
        return facts
    latest: dict[tuple[str, str, int | None], str] = {}
    for fact in facts:
        if fact.period_start is None and fact.period_end:
            key = (fact.ticker, fact.concept, fact.fiscal_year)
            latest[key] = max(latest.get(key, ""), fact.period_end)
    return [
        fact
        for fact in facts
        if fact.period_start is not None
        or latest.get((fact.ticker, fact.concept, fact.fiscal_year)) == fact.period_end
    ]


def _derive_percent(number: ParsedNumber, operands: list[float]) -> str | None:
    """Ground a percent as arithmetic over verified figures in its claim.

    Tries the change form |a-b|/|b| and the share form |a/b| for every
    ordered pair of already-reconciled non-percent figures, at the percent's
    stated precision. Magnitudes throughout — claims state direction in
    words. Growth/decline percents whose operands the claim itself states
    were 4 of 26 non-grounded claims in the 2026-07-27 re-baseline; the
    reconciled operands make the recomputation as trustworthy as the ratio
    path's (ADR 0018)."""
    for a in operands:
        for b in operands:
            if a == b or b == 0:
                continue
            for form, name in ((abs(a - b) / abs(b), "change"), (abs(a / b), "share")):
                if abs(form * 100.0 - number.value) <= number.tolerance:
                    return (
                        f"derived {name} from verified claim figures: "
                        f"|{a:g}| vs |{b:g}| = {form * 100.0:.2f}%"
                    )
    return None


def _amount(value: float, unit: str | None = None) -> str:
    """A filed value as a filing would print it.

    `f"{value:g}"` renders 18,497,000,000 as `1.8497e+10`. A careful reader
    cannot match that against "$18,497 million" without doing arithmetic, and
    the citation judge — correctly — would not: 17 of 27 judged spans were
    rejected on this formatting alone, taking `citation_accuracy` to 0.370.
    The grounding was exact the whole time; only the evidence shown for it
    was unreadable (ADR 0022)."""
    suffix = f" {unit}" if unit else ""
    if abs(value) < 1e6:
        return f"{value:,.4f}".rstrip("0").rstrip(".") + suffix
    if (unit or "").lower() == "usd":
        return f"{value:,.0f}{suffix} (${value / 1e6:,.0f} million)"
    return f"{value:,.0f}{suffix}"


def _fact_span(fact: Fact) -> str:
    """The evidence shown for a grounded numeric claim.

    It must state everything the store knows about the fact, because the
    claim was drawn from a chunk that stated all of it. The span used to omit
    the period end while the injected chunk carried it, so claims quoting
    "which ended January 25, 2026" cited evidence that did not mention the
    date — and the citation judge, told to require support for *every* part
    of a claim, correctly refused them (ADR 0022)."""
    period = fact.fiscal_period or ""
    year = str(fact.fiscal_year or "")
    when = " ".join(x for x in (period, year) if x) or (fact.period_end or "?")
    if fact.period_end and fact.period_end not in when:
        when = f"{when}, period ending {fact.period_end}"
    dims = ""
    if fact.dimensions:
        dims = f" [{', '.join(f'{k}={v}' for k, v in sorted(fact.dimensions.items()))}]"
    return (
        f"XBRL {fact.concept} ({fact.label or 'no label'}){dims} = "
        f"{_amount(fact.value, fact.unit)} for {when}, "
        f"{fact.form_type or '?'} acc {fact.accession_no}"
    )


def _difference_span(a: Fact, b: Fact) -> str:
    def _when(f: Fact) -> str:
        return " ".join(x for x in (f.fiscal_period or "", str(f.fiscal_year or "")) if x)

    return (
        f"XBRL difference {a.concept} ({a.label or 'no label'}): "
        f"|{_amount(a.value)} ({_when(a)}) - {_amount(b.value)} ({_when(b)})| = "
        f"{_amount(abs(abs(a.value) - abs(b.value)), a.unit)}, acc {a.accession_no}"
    )


def _growth_span(num: Fact, den: Fact) -> str:
    period = num.fiscal_period or ""
    growth = abs(num.value) / abs(den.value) - 1.0
    return (
        f"XBRL change {num.concept} ({num.label or 'no label'}): "
        f"{_amount(num.value, num.unit)} ({period} {num.fiscal_year}) vs "
        f"{_amount(den.value, den.unit)} ({period} {den.fiscal_year}) = "
        f"{growth * 100.0:+.2f}%, acc {num.accession_no}"
    )


def _ratio_span(num: Fact, den: Fact) -> str:
    period = num.fiscal_period or ""
    year = str(num.fiscal_year or "")
    when = " ".join(x for x in (period, year) if x) or (num.period_end or "?")
    return (
        f"XBRL ratio {num.concept} ({num.label or 'no label'}) / {den.concept} "
        f"({den.label or 'no label'}) = {_amount(num.value)} / {_amount(den.value)} = "
        f"{num.value / den.value:.4f} for {when}, acc {num.accession_no}"
    )
