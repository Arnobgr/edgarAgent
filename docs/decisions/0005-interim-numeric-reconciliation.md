# ADR 0005 — Interim numeric reconciliation (and the Phase 3 XBRL swap)

**Date:** 2026-07-18 · **Phase:** 1 · **Status:** accepted (interim by design)

## Decision

Phase 1 verifies numeric claims with a deterministic parse-and-match tool
(`fie/tools/reconcile.py`), not an LLM: numbers are extracted from the claim
and from its cited chunk text (handling `$`, `%`, comma grouping, and scale
words thousand/million/billion/trillion), and the claim is `grounded` only
when **every** figure in it matches a source figure at the claim's stated
precision — the tolerance is half a unit of the claim's last significant
digit, so "$391.0 billion" matches a source "391,035" under an
"(in millions)" table marker (391,035 M rounds to 391.0 B), while
"$391.55 billion" does not.

Details that matter:

- **Context scaling:** a bare source figure is also tried at the scale of an
  "in thousands/millions/billions" marker found in the chunk text; explicit
  scale words on the figure itself always win.
- **Years:** bare 4-digit integers in 1900–2099 (no `$`, `%`, comma, decimal,
  or scale word) are skipped on both sides as year noise.
- **Partial:** some figures matched, some not → `partial`; none → `unsupported`,
  with feedback naming the missing figures and the cited chunks.

The tool sits behind the `NumericReconciler` protocol; the verify node only
sees the protocol.

## Rationale

An exact-match code check can't be sweet-talked by a fluent draft — a
fabricated figure fails, period. It costs nothing per claim and gives the
graph its real shape (verdict → loop-back → retraction) now, before Phase 3
lands the corpus infrastructure.

Known limitations, accepted as interim: context-scale inference can in
principle mis-scale (a bare "391" under an "in millions" header matches both
391 and 391M); there is no fiscal-period awareness (a matching figure from
the wrong year grounds the claim if it appears in the cited chunk); derived
figures (growth rates the model computed) can't be verified. These are
precisely the gaps XBRL closes.

## The Phase 3 swap

Phase 3 replaces `InterimNumericReconciler` with an exact lookup in the
local XBRL facts store (concept, value, unit, period, accession — overview
§3.3): claim text → candidate concept/period mapping (cheap model) → exact
fact match with unit/scale normalization. It implements the same
`NumericReconciler` protocol, so the swap touches `fie/tools/`, not the
graph. The reconciliation test table in `tests/test_reconcile.py` carries
over as the regression floor.
