# ADR 0010 — Structure-aware parsing: edgartools items + bs4 tables, with an in-text re-split fallback

**Date:** 2026-07-19 · **Phase:** 3 · **Status:** accepted (decided on TSLA evidence)

## Decision

Three parsing paths, one per structural problem (blueprint §5):

1. **Narrative sections** come from edgartools' item accessors
   (`filing.obj()[item]`), with a deterministic fallback: when a returned
   section contains **two or more in-text `ITEM N.` headings**, every such
   blob is re-split at those headings, the Part I → Part II transition is
   tracked by item numbers restarting, and pieces are merged across blobs
   keeping the longest text per item (`resplit_mangled_sections`,
   `src/fie/ingest/parse.py`).
2. **Tables are never flattened to prose.** edgartools' item text silently
   drops table structure (verified: TSLA FY2025 MD&A text contains zero
   table rows), so tables are extracted separately from the filing HTML
   with BeautifulSoup: innermost `<table>` elements → cleaned grids (empty
   layout columns/rows dropped, digit-free layout/TOC tables discarded),
   header row detected (labels/years/period phrases vs data magnitudes),
   caption from the nearest short preceding block, and the containing item
   attributed by document order relative to `Item N` headings.
3. **8-K earnings filings** are parsed as their EX-99 press-release
   attachment (the analyst-facing document): full text as one section plus
   its tables. Non-2.02 8-Ks are not ingested (plan scope).

`sec-parser` is **not** adopted.

## Evidence (TSLA — the least standard filer of the three)

- TSLA FY2025 10-K: 16 usable sections (Item 7 = 55k chars), 71 data
  tables extracted, 52 correctly attributed to Item 8, 9 to Item 7;
  1,365 XBRL facts.
- TSLA Q1-2026 10-Q: edgartools mis-segments badly — the entire Part I
  (financial statements + MD&A, 100k chars) came back labeled
  `Part I, Item 4`, overlapping a second 76k blob labeled
  `Part II, Item 4`. After the re-split fallback: `Part I, Item 1` (63k),
  `Part I, Item 2` (38k MD&A), Items 3/4 and Part II items all correctly
  labeled. This mis-segmentation is exactly the failure mode the plan told
  us to test for; the fallback fixes it without a new dependency.
- TSLA earnings 8-K: EX-99 press release resolves and parses (text +
  tables).

## Why not sec-parser

The observed failure (wrong item labels on whole-part blobs) is fixable
with a 40-line deterministic re-split on evidence we can test; sec-parser
would add a heavy dependency, its own element model to map into ours, and
a second parsing opinion to reconcile. Revisit if AAPL/NVDA filings
surface a failure the re-split cannot express (e.g. headings not rendered
in the text at all) — that is the trigger to swap this fallback for
sec-parser, behind the same `ParsedFiling` model.

## Consequences

- Table cell provenance is display-only; numeric truth is XBRL (ADR 0012).
- Section labels are stable identifiers (`10-K/Item 7`,
  `10-Q/Part I, Item 2`) and are what retrieval-eval relevance labels
  reference.
