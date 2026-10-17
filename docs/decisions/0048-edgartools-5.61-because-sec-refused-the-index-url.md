# ADR 0048 — edgartools 5.61.1, because SEC refused the index URL 5.42.0 built

**Date:** 2026-10-13 · **Phase:** 7 · **Status:** accepted, implemented
2026-10-13; addendum 2026-10-17 removes the duplicated sections · **Amends:**
ADR 0002 (the EDGAR client is a dependency on SEC's page layout, not only on
its API), ADR 0010 (the 10-Q re-split no longer fires)

## Context

On 2026-10-11 a fresh `fie ingest --tickers AAPL --years 1`, run to check the
README quickstart on a clean clone, failed every 10-K and 10-Q with
`'NoneType' object has no attribute 'download'`. The main checkout failed the
same way. The 8-K press releases still ingested, and the XBRL facts still
parsed.

The cause is outside this repo. edgartools 5.42.0 found a filing's primary
document through the short `/data/<cik>/<accession>-index.html` URL, and **SEC
now answers that URL with 403 for every filing**. So `homepage` came back
without a primary document, and `Filing.html()` dereferenced `None`. The 5.61.0
changelog records the same diagnosis and moves the index URL inside the
accession folder.

Newer releases were tried on AAPL's FY2025 10-K:

| version | released | result |
|---|---|---|
| 5.42.0 (locked) | 2026-07-09 | raises; XBRL facts still parse |
| 5.59.1, 5.60.0 | 09-26, 10-02 | no error, empty HTML, 0 sections: worse, because silent |
| 5.61.1 | 10-06 | 17 sections, 1.5M chars of HTML |

The deployed corpus was never at risk. It was ingested in July and travels as
data (`ingest.db` plus a Qdrant snapshot). Nothing on the answer path imports
edgartools; only `fie.ingest` does. What broke was every *future* ingest: a
corpus refresh, a new ticker, or anyone following the README.

## Decision

**`edgartools>=5.61.1`, locked at 5.61.1.** It is the oldest release that works,
so a newer one buys nothing here. 5.62.0 (10-11) was not evaluated.

**`httpxthrottlecache` is locked at 0.6.1, not 0.7.0.** 5.61.1 needs `>=0.6.0`,
and an unconstrained lock resolved 0.7.0, which was uploaded on 10-11, two days
before this change. `frontend/.npmrc` holds npm back a fortnight for the reason
ADR 0041 gives: a malicious release of a legitimate package is usually caught
within days. The Python side has no such hold, but nothing forced the newer
version, so the older one is locked. edgartools 5.61.1 is itself only a week
old. That breaks the same principle, and is accepted because no older release
can ingest anything.

**The deployed corpus is not re-ingested.** See *Consequences*.

## Verified

Done before the lock changed, in a scratch clone, then repeated on the exact
final lock. The two runs were byte-identical: 78 of 78 parsed filings, and one
hash over every fact row.

`make check` is green. All 78 corpus filings were re-acquired through the
pipeline's own `enumerate_filings` and `acquire_filing`, then compared with
what the July ingest stored:

| | stored (5.42.0) | 5.61.1 |
|---|---|---|
| filings enumerated | 78 | 78, plus 7 filed since July |
| XBRL facts | 28,550 | 28,535; every other row identical, values included |
| tables | 1,750 | 1,750, all identical |
| 8-K press releases | 44 | all identical |
| 10-K/10-Q sections | 422 | 409 |
| chunks | 4,181 | 4,285, of which 3,141 are unchanged |

- **The 15 lost facts are years, not figures**:
  `aapl:DebtInstrumentMaturityYearRange{Start,End}`,
  `tsla:DebtInstrumentContractualMaturityYear` and
  `tsla:ResearchTaxCreditCarryForwardExpirationDates`, all unitless. edgartools
  stopped giving unitless facts a `numeric_value`. No gold question uses them.
  Dropping them is the same correction ADR 0047 made on the claim side.
- **Section text no longer fuses table cells.** `Services27,423` and
  `Total net sales94,036` are now `Services 27,423` and
  `Total net sales 94,036`. This accounts for most of the small changes across
  nearly every item.
- **The 19 removed sections were mis-segmentation, and their text is now in the
  right item.** Apple's 10-K "Item 11" was the Items 11–14
  incorporation-by-reference boilerplate in one blob. Items 12–14 now stand
  alone, and Item 11's single sentence falls under the minimum section length.
  NVIDIA's 10-K "Item 16" was a piece of the MD&A tax discussion, now in
  Item 7. Tesla's 10-Q "Part II, Item 3" was Note 2 of the financial
  statements, now in Part I, Item 1.
- **Tesla's FY2023 10-K (`0001628280-24-002390`) is repaired.** As stored, it
  has no Business section, no Risk Factors, and financial-statement notes
  labelled Items 6–8. 5.61.1 returns Item 1 (45k chars), Item 1A (79k) and a
  163k Item 8.
- **The ADR 0010 re-split no longer fires on any corpus 10-Q.** It still fires
  on Tesla's FY2024 and FY2025 10-Ks. There, edgartools' "Item 1B" also contains
  Item 1C, so the re-split adds a duplicate "Part I, Item 1C". Tesla's 2023 Q3
  10-Q gains a duplicate Legal Proceedings as "Part I, Item 1". Each duplicate
  costs about one redundant chunk. *Removed in the addendum below.*
- No 429 or 403 in two full re-acquisitions of the corpus. 5.61.1 throttles to 9 req/s by default
  (`EDGAR_RATE_LIMIT_PER_SEC`).

The README quickstart then passed end to end on a clean clone: 10 filings,
422 chunks, 3,169 facts. The README question was answered with 3 of 3 claims
grounded, the FY2025 figure reconciled to `us-gaap:NetIncomeLoss`, for $0.006.

## Consequences

- **Ingest works again.** Only ingest changes. The service, the eval and the
  deployed corpus run the same code paths and the same data as before.
- **The deployed corpus was parsed by 5.42.0, and any new ingest is parsed by
  5.61.1.** A partial ingest, such as a new filing or a new ticker, mixes the two
  parses. That is tolerable, because the new parse is the better one. It is
  still a mixed corpus, so a refresh should be a full re-ingest.
- **A full re-ingest is a retrieval change and needs a full eval.** About a
  quarter of the embedded chunks change, so recall, citations and grounding can
  move. That is the eval's question, not this ADR's.

## Honest limits

- **One transient empty `html()` in 78.** NVIDIA `0001045810-24-000316` returned
  no HTML once and 1.44M chars on each of two retries. 5.61.1 has several
  `return None` paths in `Filing.html()`. The pipeline's
  `no HTML document` check turns that into a failed filing, which the next run
  retries, so the failure is loud and resumable but costs a re-run.
- **The comparison is against the stored parse, not against the filing.** "Better"
  above means the text is in the item its heading names. Nobody read the 409
  sections end to end.
- **The dependency on SEC's page layout remains.** This break came from SEC
  changing a URL, and no amount of locking prevents the next one. The early
  warning is the one that worked here: run the README quickstart against
  a clean clone.

## Addendum, 2026-10-17: the duplicated sections are removed

Both duplications come from edgartools itself, and they have different
mechanisms:

- **An item that runs past the next item's heading.** On Tesla's FY2024 and
  FY2025 10-Ks, "Item 1B" holds "None." and then all of Item 1C, which
  edgartools also returns on its own. The two copies differ only by a page
  number. The re-split then cut the over-long section and labelled the copy
  "Part I, Item 1C", a 10-Q label on a 10-K.
- **One text under two labels.** On Tesla's Q3 2023 10-Q, "Part I, Item 1" and
  "Part II, Item 1" are byte-identical, and both are Legal Proceedings.

`drop_duplicated_items` runs before the re-split. It cuts a section at the
in-text heading of another item that has its own section. That rule can only
act on bare 10-K labels, because a 10-Q label never equals "Item 1C". Of two
identical texts, it keeps the later label. That choice rests on the one
observed case, where the earlier item could not be anchored and took the next
one's text.

Applied to all 34 corpus 10-Ks and 10-Qs re-acquired under 5.61.1, it removes
exactly the three duplicates and changes nothing else. After it, the re-split
fires on no corpus filing. Tests are in `tests/test_ingest.py`.
