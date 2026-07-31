"""The XBRL facts store as a *research* tool.

`plans/00-overview.md` §3.3 decided numeric truth lives in XBRL rather than
in parsed HTML cells. Only the verification half was ever built: the store
was wired into `verify` and not into `research`, so the system could **check**
a number it could not **look up**. `query_facts` resolves all 28 of the gold
set's expected figures while retrieval failed to surface 13 of them — they
are standard flow facts (revenue, net income, R&D) sitting in a local SQLite
table the answering path could not reach (ADR 0022).

This is a lookup, a formatter and a wiring change — deliberately not a
subsystem. A resolved fact enters `research`'s evidence as a chunk carrying
its own accession, so a claim citing it keeps the same citation trail as one
citing filing prose.
"""

import logging
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from fie.ingest.store import Fact, IngestStore
from fie.state import EvidenceChunk

logger = logging.getLogger(__name__)

# Facts injected per lookup. A handful of exact figures beside the retrieved
# k chunks — enough for a multi-year trend question, small enough that the
# window stays prose-dominated.
MAX_FACTS = 6

# Injected chunks outrank retrieved ones in the fan-in packing sort. Without
# this the `chunks_per_sub_question` cap could drop the exact figure in
# favour of prose — that cap already cost two answers their figure after
# retrieval had found it (ADR 0022).
INJECTED_SCORE = 1.0


class FactRequest(BaseModel):
    """One (company, concept, period) figure the question asks for.

    The field vocabulary is the reconciler's `FactCandidate`, minus the
    figure being checked — one mapping vocabulary in the system, not two."""

    ticker: str = Field(description="Ticker of the company asked about: TSLA, AAPL or NVDA")
    concept_terms: list[str] = Field(
        description="Candidate concept/label search terms, e.g. "
        "['RevenueFromContractWithCustomer', 'Revenues', 'net revenue']"
    )
    fiscal_year: int | None = Field(
        default=None, description="Fiscal year the question refers to, if it states or implies one"
    )
    fiscal_period: str | None = Field(
        default=None,
        description="'FY', 'Q1'..'Q4', 'YTD6' or 'YTD9' if the question implies one; "
        "null for balance-sheet 'as of' figures",
    )
    unit: str | None = Field(default="usd", description="'usd', 'shares', 'usdPerShare'...")


class FactLookupOutput(BaseModel):
    """Structured output of the question → fact-query mapping."""

    requests: list[FactRequest] = Field(default_factory=list)


_LOOKUP_SYSTEM = (
    "You turn an analyst's question about SEC filings into lookups against a "
    "table of XBRL facts filed by TSLA, AAPL and NVDA. Emit one request per "
    "specific filed figure the question asks for — likely US-GAAP concept "
    "names and label words, the company, and the fiscal year/period. A "
    "question spanning several years or companies needs one request each.\n\n"
    "Rules that decide whether the lookup finds the right row:\n"
    "- Put the most specific US-GAAP concept FIRST in concept_terms and keep "
    "the list short. A vague term like 'revenue' matches many concepts, and "
    "the first term that matches anything wins.\n"
    "- fiscal_period is null for every BALANCE-SHEET figure — total assets, "
    "cash and cash equivalents, total liabilities, stockholders' equity, "
    "inventory — even when the question names a year, because a balance is "
    "stated at a date and not over a period. Use 'FY' for a full-year INCOME "
    "or CASH-FLOW figure (revenue, net income, R&D, operating income), and "
    "'Q1'..'Q4' only when the question names a quarter.\n"
    "- 'latest', 'most recent' and 'the last three years' mean the fiscal "
    "years listed below, counting back from each company's latest. Never "
    "assume the current calendar year.\n\n"
    "Emit an EMPTY list when the question does not ask for a filed figure — "
    "risks, strategy, competition, management commentary and anything "
    "answered by prose rather than a number. Never guess a figure yourself; "
    "you are naming what to look up, not answering."
)


@runtime_checkable
class FactLookup(Protocol):
    """Anything that can resolve a question to filed figures as evidence."""

    def lookup(self, question: str) -> list[EvidenceChunk]:
        """Resolved facts as evidence chunks; empty when none apply."""
        ...


class XbrlFactLookup:
    """Question → filed figures, straight out of the local facts store."""

    def __init__(self, store: IngestStore, mapper: Any) -> None:
        """`mapper` is a runnable producing `FactLookupOutput` from messages
        (a chat model already bound with `.with_structured_output`)."""
        self._store = store
        self._mapper = mapper
        # Filing dates preloaded (78 rows) rather than read per lookup: the
        # graph calls this from research threads, and a second read path on
        # the shared connection would widen the close/query race ADR 0019
        # closed.
        self._filing_dates = {row.accession_no: row.filing_date for row in store.list_manifest()}
        latest = store.latest_fiscal_years()
        self._system = (
            _LOOKUP_SYSTEM
            + "\n\nLatest fiscal year in the corpus: "
            + (
                ", ".join(f"{ticker} FY{year}" for ticker, year in sorted(latest.items()))
                or "(store empty)"
            )
        )

    def close(self) -> None:
        self._store.close()

    def lookup(self, question: str) -> list[EvidenceChunk]:
        try:
            output = self._mapper.invoke(
                [("system", self._system), ("human", f"Question: {question}")]
            )
        except Exception as exc:
            logger.warning("fact-lookup model failed (%s); no facts injected", exc)
            return []
        requests = (
            output.requests
            if isinstance(output, FactLookupOutput)
            else FactLookupOutput.model_validate(output).requests
        )
        chunks: list[EvidenceChunk] = []
        seen: set[str] = set()
        for request in requests:
            for fact in self._resolve(request):
                chunk = _as_evidence(fact, self._filing_dates.get(fact.accession_no, ""))
                if chunk.chunk_id in seen:
                    continue
                seen.add(chunk.chunk_id)
                chunks.append(chunk)
                if len(chunks) >= MAX_FACTS:
                    logger.info("fact lookup: %d facts injected (cap)", len(chunks))
                    return chunks
        logger.info("fact lookup: %d request(s) → %d fact(s) injected", len(requests), len(chunks))
        return chunks

    def _resolve(self, request: FactRequest) -> list[Fact]:
        """The headline figure a request names, at most one per request.

        Terms are tried **one at a time**, because ORing them lets a vague
        term decide the row: "revenue" pulled
        `us-gaap:ContractWithCustomerLiabilityRevenueRecognized` ahead of
        `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` and
        g-001 was injected with the wrong concept entirely. **A term that
        names a fact exactly — by concept or by filed label — settles it
        immediately**; otherwise the shortest matching concept is held as a
        fallback, which is the headline of any concept family
        (`us-gaap:Assets` before `us-gaap:AssetsCurrent`).

        The stated period is tried first and then dropped — the reconciler's
        `_period_attempts` rule. The mapper says `FY` for balance-sheet
        figures often enough to matter, and `fiscal_period = 'FY'` excludes
        instants outright, so "Nvidia's total assets" matched `%Assets%`
        against `us-gaap:AmortizationOfIntangibleAssets` and injected an
        amortization expense (ADR 0022).

        Non-dimensioned only: a segment breakdown must never be injected as
        the company total."""
        attempts = [request.fiscal_period, None] if request.fiscal_period else [None]
        best: Fact | None = None
        for fiscal_period in attempts:
            for term in request.concept_terms or [""]:
                facts = self._store.query_facts(
                    tickers=[request.ticker] if request.ticker else None,
                    concept_terms=[term] if term else None,
                    fiscal_year=request.fiscal_year,
                    fiscal_period=fiscal_period,
                    unit=request.unit,
                    include_dimensioned=False,
                    limit=40,
                )
                if fiscal_period is None and request.fiscal_year is not None:
                    # A year holds four balance dates; the question means the
                    # closing one unless it named a quarter (ADR 0021).
                    instants = [f for f in facts if f.period_start is None and f.period_end]
                    if instants:
                        year_end = max(f.period_end or "" for f in instants)
                        facts = [
                            f
                            for f in facts
                            if f.period_start is not None or f.period_end == year_end
                        ]
                exact = [f for f in facts if _names_fact(f, term)]
                if exact:
                    return [min(exact, key=_headline)]
                if facts and best is None:
                    best = min(facts, key=_headline)
        return [best] if best is not None else []


def _names_fact(fact: Fact, term: str) -> bool:
    """Whether a term names this fact exactly — by concept local name or by
    filed label, both space- and case-insensitive.

    Substring matching alone cannot separate `Net sales` from `Portion of
    total net sales that was included in deferred revenue`: the term is
    inside both, and the wrong concept happens to have the shorter name, so
    a length tiebreak picks it. The label is what tells them apart, and it
    is the vocabulary the mapper writes in (ADR 0022)."""
    if not term:
        return False
    wanted = term.replace(" ", "").lower()
    if fact.concept.split(":")[-1].lower() == wanted:
        return True
    return (fact.label or "").replace(" ", "").lower() == wanted


def _headline(fact: Fact) -> tuple[int, str]:
    """Sort key preferring the headline of a concept family."""
    return len(fact.concept), fact.concept


def _as_evidence(fact: Fact, filing_date: str) -> EvidenceChunk:
    """One filed fact as a citable evidence chunk.

    `chunk_type` is `xbrl`, not `table`: the reconciler's table fallback
    cell-matches chunk text, and letting it match this text would turn the
    store into its own corroboration.

    `filing_date` comes from the manifest, not from the fact. A fact carries
    the period it covers, and the two differ by weeks — Tesla's FY2025 10-K
    covers a period ending 2025-12-31 and was filed 2026-01-29. Putting the
    period end in the citation trail states the wrong date for the document
    the reader would go and open (blueprint §5)."""
    period = fact.period_end or "?"
    scope = f"{fact.fiscal_period or 'as of'} {fact.fiscal_year or ''}".strip()
    return EvidenceChunk(
        chunk_id=f"xbrl:{fact.accession_no}:{fact.concept}:{period}",
        doc_id=f"{fact.ticker}-{fact.form_type or 'filing'}-{fact.accession_no}",
        ticker=fact.ticker,
        form_type=fact.form_type or "",
        section="XBRL facts",
        text=(
            f"{fact.ticker} filed {fact.label or fact.concept} ({fact.concept}) "
            f"for {scope}, period ending {period}: {fact.value:,.0f} "
            f"{(fact.unit or '').upper()} — as reported in {fact.form_type or 'filing'} "
            f"{fact.accession_no}."
        ),
        score=INJECTED_SCORE,
        metadata={
            "accession_no": fact.accession_no,
            "filing_date": filing_date,
            "period_end": period,
            "chunk_type": "xbrl",
            "concept": fact.concept,
        },
    )
