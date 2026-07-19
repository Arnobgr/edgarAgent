# ADR 0012 — Exact numeric reconciliation against the XBRL facts store

**Date:** 2026-07-19 · **Phase:** 3 · **Status:** accepted

## Decision

`XbrlNumericReconciler` replaces the Phase 1 interim reconciler as the
verify node's numeric judge (same `NumericReconciler` protocol — the graph
did not change; the call moved onto a worker thread since the reconciler
now does SQLite + mapping-model I/O).

Per numeric claim:

1. Parse the claim's figures (Phase 1 parser, unchanged — scale words,
   `$`/`%`, accounting parentheses, year skipping).
2. A cheap model (`fact_mapping` role, structured output) maps the claim
   to candidate `(concept terms, fiscal year/period, unit)` triples with
   each figure normalized to an absolute value.
3. **Deterministic SQLite lookup** in `facts` (ticker scope = the cited
   chunks' tickers): a figure is `grounded` only when a stored fact equals
   it **exactly at the claim's stated precision** (half a unit of the last
   significant digit — "$96.8B" matches 96,773,000,000; "$96.85B" does
   not). Percent claims also try the /100 ratio form with the tolerance
   scaled down. Non-dimensioned (headline) facts are preferred over
   segment breakdowns.
4. No fact match → **table-chunk cell match** fallback (the Phase 1
   parse-and-match engine, restricted to cited `chunk_type: table`
   chunks) — display provenance only. Prose figures deliberately do *not*
   ground a numeric claim the XBRL store can't confirm.
5. Still unmatched → `unsupported`, with feedback naming the concepts
   searched and the tables tried, so the loop-back re-retrieves usefully.

The supporting span of an XBRL match is the fact row itself, cited by
accession number: `XBRL us-gaap:Revenues (Revenues) = 9.4827e+10 usd for
FY 2025, 10-K acc 0001628280-26-003952`.

## What this closes (ADR 0005's known gaps)

- **Period shifts:** the true value under the wrong fiscal year no longer
  grounds (lookup is period-scoped). This is a perturbation axis in the
  verifier P/R eval.
- **Context mis-scaling:** fact values are stored as absolute numbers;
  scale normalization happens once, on the claim side.
- **Wrong-company support:** ticker scope comes from the cited chunks.

Known limits, accepted: derived figures (growth rates the synthesizer
computed) still can't ground — they fail closed to `unsupported`; the
cheap mapping model can miss a concept (mitigated by multiple candidate
terms + label matching; measured by verifier P/R, decision 3.5 escalates
the mapper model if numeric precision/recall demands it). Dimensioned
facts from edgartools arrive with only an `is_dimensioned` flag (no axis
detail) in the current version; they are stored flagged so headline
lookups exclude them.

## The interim reconciler stays

`InterimNumericReconciler` remains in `fie/tools/reconcile.py` as the
in-chunk matching engine behind the table fallback (and its Phase 1 test
table remains the regression floor for the parser).
