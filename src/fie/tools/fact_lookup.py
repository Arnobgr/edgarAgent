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
    "names and label words, the company, and the fiscal year/period (use the "
    "company's fiscal calendar; 'FY' for a full year, null period for "
    "balance-sheet 'as of' figures). A question spanning several years or "
    "companies needs one request each.\n\n"
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

    def close(self) -> None:
        self._store.close()

    def lookup(self, question: str) -> list[EvidenceChunk]:
        try:
            output = self._mapper.invoke(
                [("system", _LOOKUP_SYSTEM), ("human", f"Question: {question}")]
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
                chunk = _as_evidence(fact)
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

        Non-dimensioned only — a segment breakdown must never be injected as
        the company total — and the store's ordering puts the shortest
        matching concept first, which is the headline of any concept family
        (`us-gaap:Assets` before `us-gaap:AssetsCurrent`)."""
        facts = self._store.query_facts(
            tickers=[request.ticker] if request.ticker else None,
            concept_terms=request.concept_terms or None,
            fiscal_year=request.fiscal_year,
            fiscal_period=request.fiscal_period,
            unit=request.unit,
            include_dimensioned=False,
            limit=20,
        )
        if request.fiscal_period is None and request.fiscal_year is not None:
            # A year holds four balance dates; the question means the closing
            # one unless it named a quarter (the reconciler's rule, ADR 0021).
            instants = [f for f in facts if f.period_start is None and f.period_end]
            if instants:
                year_end = max(f.period_end or "" for f in instants)
                facts = [f for f in facts if f.period_start is not None or f.period_end == year_end]
        return facts[:1]


def _as_evidence(fact: Fact) -> EvidenceChunk:
    """One filed fact as a citable evidence chunk.

    `chunk_type` is `xbrl`, not `table`: the reconciler's table fallback
    cell-matches chunk text, and letting it match this text would turn the
    store into its own corroboration."""
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
            "filing_date": period,
            "chunk_type": "xbrl",
            "concept": fact.concept,
        },
    )
