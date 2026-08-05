# ADR 0027 — Recorded runs are the default demo surface

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Extends:** ADR 0025 (synthesize stalls ~1.5% of the time) ·
**Feeds:** ADR 0030 (this is abuse-control layer 2)

## Context

The demo's audience is someone who clicked a link on a CV. A live run costs
~75 seconds, ~$0.023, and stalls at the node timeout in roughly **1.5% of
synthesize calls** (ADR 0025, measured over 198 calls). None of those numbers
are wrong, and all three are bad properties for the first thing a visitor
meets.

There is also an infrastructure fact: the VPS has no CDN in front of it
(ADR 0035). Every request that reaches the demo lands directly on a shared
nginx and 2 vCPU that five production services also use.

Meanwhile the 30 gold questions from the accepted 2026-08-01 baseline are
already sitting in `data/checkpoints.db`, fully verified, with their grounding
tables and their retractions intact.

## Decision

**`fie export-runs` reads finished runs out of the checkpoint database and
writes the exact `GET /v1/jobs/{id}/result` payload as static JSON.** Phase 5
ships those files inside the Cloudflare Pages bundle, so the CDN answers the
visitor who only reads and **the VPS never sees the request**. A live run
stays available as a deliberate second action, behind the abuse controls.

Four things about the implementation are load-bearing:

**The payload is the same model, not a similar one.** `export.py` builds
`RunPayload` through `build_run_result` — the same pure derivation the API
calls. `tests/test_export_runs.py` asserts `RunPayload.model_validate_json`
against an emitted file, so the frontend has one shape to render and a drift
between recorded and live is a test failure rather than a rendering bug.

**The default selection is the *accepted* baseline's run ids, not the newest
report.** `accepted_run_ids` reads `eval/baselines.json`'s `accepted` entry
and resolves it to its report. The recorded surface therefore shows exactly
the runs whose metrics are the published ones; a newer, unaccepted eval cannot
quietly become the demo.

**A run parked mid-flight is skipped, and `pending_writes` is how it is
detected.** A thread waiting at the review gate stores its interrupt as a
pending write, so an unfinished run is identifiable from the checkpoint tuple
alone, without reconstructing the compiled graph. Skips are reported with a
reason rather than dropped silently.

**The index carries no generation timestamp.** The files are committed so the
Pages build can bundle them; a clock in `index.json` would produce a diff on
every export even when no run changed.

`RecordedRun.retracted` is in the index on purpose. **A recorded run showing a
claim being pulled out of the answer is the single best demonstration this
system has**, and the frontend has to be able to find one without fetching all
thirty payloads.

## Consequences

`recorded/` holds 30 runs and an index, 408 KB, committed. Across them, **8
retracted claims in 6 runs** — matching the 8 the accepted baseline recorded,
which is a useful cross-check that the export reads what the eval scored.

This removes more load than every limit in ADR 0030 combined, and costs
nothing, because most visitors never start a live run.

It also means the demo works when the VPS does not. That is worth stating
plainly: the recorded surface is the availability story, and the live path is
the capability story.

## Honest limits

- The export is a snapshot. Nothing re-runs it when the engine changes, so a
  recorded answer can drift from what the current code would produce. Re-run
  `fie export-runs` when a baseline is accepted — it is one command, and
  `plans/00-overview.md`'s sequencing rule already puts a full eval at exactly
  that moment.
- Committing 408 KB of generated JSON is a deliberate exception to "generated
  artifacts are gitignored". The Pages build needs them in the bundle and the
  alternative is a build step that reaches a machine holding the corpus.
- The 30 questions are the gold set, so the recorded surface is the eval's
  population. It shows what the system does on questions chosen to test it,
  which is honest but is not a random sample of what a visitor would ask.
