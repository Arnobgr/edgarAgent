# ADR 0002 — edgartools as the EDGAR client

**Date:** 2026-07-17 · **Phase:** 0 · **Status:** accepted

## Decision

All SEC EDGAR access goes through [`edgartools`](https://github.com/dgunning/edgartools):
fetching filings, resolving latest-by-form, extracting item sections, and
(from Phase 3) reading XBRL facts.

## Rationale

- **Structured accessors, not scraping.** `filing.obj()` returns typed report
  objects (`TenK` etc.) with item-level section access — exactly the
  section-aware chunking Phase 0 needs, without HTML parsing of our own.
- **XBRL facts exposure** is the load-bearing reason (blueprint §15.3): numeric
  verification in Phase 3 becomes a deterministic lookup against the filing's
  machine-tagged XBRL facts, which edgartools exposes directly. Choosing it now
  means the Phase 3 facts store needs no client change.
- **Actively maintained**, wide EDGAR coverage, and it handles SEC's
  identity/user-agent requirement (`set_identity`, fed from config).

## Consequences

- Raw parsed filings are cached under the gitignored `data/` dir so reruns
  don't hit EDGAR (rate-limit courtesy + speed).
- Fallback if item extraction proves too coarse for narrative structure:
  `sec-parser` for finer HTML structure (named in the plan, not needed yet).
