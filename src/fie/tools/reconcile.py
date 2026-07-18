"""Interim numeric reconciliation (Phase 1).

Parses numbers (with $, %, and scale words like million/billion) from a
numeric claim and from its cited chunk text, and rules `grounded` only when
every figure in the claim matches a figure in the cited text at the claim's
stated precision ("$391.0 billion" matches "391,035" under an "in millions"
context marker because 391,035 million rounds to 391.0 billion).

Written against the `NumericReconciler` protocol so Phase 3 swaps in the
exact XBRL facts lookup without touching the verify node (ADR 0005).
"""

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fie.state import EvidenceChunk, VerdictLabel

_SCALES = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
}

_NUM_RE = re.compile(
    r"(?P<currency>\$\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
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
