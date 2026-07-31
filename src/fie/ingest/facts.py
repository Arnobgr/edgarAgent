"""XBRL facts extraction (decision 3.3): the numeric ground truth.

Facts come from edgartools' XBRL parse of each filing. Values arrive
already scaled to absolute numbers (e.g. revenue as 96_773_000_000.0, not
"96,773 in millions") — normalization here is unit lower-casing, decimals
coercion, and dropping non-numeric/dei housekeeping facts. Dimensioned
facts (segment/axis breakdowns) are kept with their dimensions serialized,
so segment-level claims can still reconcile; lookups prefer non-dimensioned
headline facts.
"""

import logging
import math
from typing import Any

from fie.ingest.store import Fact

logger = logging.getLogger(__name__)

# Housekeeping namespaces that are never analyst-facing numbers.
_SKIP_PREFIXES = ("dei:",)


def extract_facts(xbrl: Any, *, ticker: str, accession_no: str, form_type: str) -> list[Fact]:
    """All numeric facts from an edgartools XBRL object, normalized."""
    df = xbrl.facts.query().to_dataframe()
    facts: list[Fact] = []
    dim_cols = [c for c in df.columns if c.startswith("dim_")]
    for row in df.to_dict("records"):
        concept = str(row.get("concept") or "")
        value = row.get("numeric_value")
        if not concept or concept.startswith(_SKIP_PREFIXES):
            continue
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        dimensions = {
            c.removeprefix("dim_"): str(row[c]) for c in dim_cols if _present(row.get(c))
        } or None
        # Some edgartools versions expose only the flag, not the axis
        # columns — a dimensioned fact must still never masquerade as the
        # headline total (lookups prefer/filter non-dimensioned facts).
        if dimensions is None and bool(row.get("is_dimensioned")):
            dimensions = {"dimensioned": "true"}
        # Normalize each side *before* the fallback. An instant row's
        # `period_end` cell is NaN, not missing — and NaN is truthy, so
        # `row.get("period_end") or row.get("period_instant")` returns the
        # NaN and the instant date is discarded. That single expression left
        # all 15,211 instants (53% of the store) with no date at all, which
        # made `_own_fiscal_year` fall back to the filing's year for every
        # balance-sheet fact and disabled ADR 0012's period-shift protection
        # exactly where it matters most (ADR 0020).
        period_end = _str_or_none(row.get("period_end")) or _str_or_none(row.get("period_instant"))
        facts.append(
            Fact(
                ticker=ticker.upper(),
                concept=concept,
                label=_str_or_none(row.get("label")),
                value=float(value),
                unit=_unit(row.get("unit_ref"), row.get("currency")),
                decimals=_int_or_none(row.get("decimals")),
                period_start=_str_or_none(row.get("period_start")),
                period_end=period_end,
                fiscal_period=_str_or_none(row.get("fiscal_period")),
                fiscal_year=_own_fiscal_year(period_end, row.get("fiscal_year"), ticker),
                accession_no=accession_no,
                form_type=form_type,
                dimensions=dimensions,
            )
        )
    logger.info("extracted %d numeric facts from %s", len(facts), accession_no)
    return facts


# Fiscal year-end month per corpus ticker (decision 3.1: TSLA, AAPL, NVDA).
# An unknown ticker falls back to December, i.e. no shift.
_FISCAL_YEAR_END_MONTH = {"TSLA": 12, "AAPL": 9, "NVDA": 1}


def _own_fiscal_year(period_end: str | None, reported: Any, ticker: str) -> int | None:
    """A fact's own fiscal year, from its own period end.

    edgartools stamps every fact with the *filing's* fiscal year, so the
    prior-year comparatives in a 10-K would carry the wrong year — which
    would let a period-shifted claim falsely ground (ADR 0012).

    A fiscal year is labeled by the calendar year it *ends* in, so the
    period end's own calendar year is not enough for an off-calendar fiscal
    year: AAPL's quarter ending December 2025 and NVDA's ending April 2026
    are Q1 FY2026 and Q1 FY2027 — a year later than their period end reads.
    Reading the year verbatim mislabeled 5,838 of 28,550 facts, every AAPL
    and NVDA interim period, and left the annual figures (whose period end
    is in the year-end month) correct, which is why it survived a corpus
    built on 10-K questions (ADR 0020).
    """
    if period_end and len(period_end) >= 4 and period_end[:4].isdigit():
        month = int(period_end[5:7]) if period_end[5:7].isdigit() else 0
        return int(period_end[:4]) + (
            1 if month > _FISCAL_YEAR_END_MONTH.get(ticker.upper(), 12) else 0
        )
    return _int_or_none(reported)


def _present(value: Any) -> bool:
    if value is None:
        return False
    return not (isinstance(value, float) and math.isnan(value))


def _str_or_none(value: Any) -> str | None:
    return None if not _present(value) or str(value) == "" else str(value)


def _int_or_none(value: Any) -> int | None:
    if not _present(value):
        return None
    try:
        # XBRL `decimals` may be the literal "INF" (exact value) — no finite
        # precision to record.
        number = float(value)
        return int(number) if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _unit(unit_ref: Any, currency: Any) -> str | None:
    unit = _str_or_none(unit_ref) or _str_or_none(currency)
    return unit.lower() if unit else None
