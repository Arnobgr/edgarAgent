# ADR 0036 — Eval cadence and location: full, rare, on the VPS

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Extends:** ADR 0015 (the denominator never shrinks silently), ADR 0016
(`--write-baseline` is gated), ADR 0020 (`expected_fact_coverage`)

## Context

CLAUDE.md's sequencing rule says never advance a phase while the previous
phase's eval numbers have regressed, and from Phase 3 on `uv run eval-agent`
is the gate. The obvious next step is to run it in CI on every PR. That is
wrong for three separate reasons, and the interesting one is not cost.

**A subset run measures a different population.** `--limit N` takes
`questions[:N]`, and `expected_fact_coverage` iterates the list it is handed.
The first 8 gold questions carry **8 of the 28** XBRL labels, so a subset's
coverage is computed over a different population than the `1.000` in
`baselines.json`. Comparing them is the same category error CLAUDE.md already
records for `citation_accuracy`: the population moved, not the per-class
rates.

**Cost and time.** ~75 minutes and $0.69 per full run — agent spend from
`run_budget` only, so it **excludes `eval_judge` calls** and is a floor.

**Noise.** The judge flips on about 1 in 27 byte-identical spans, so a single
reading carries ±0.037. A per-PR gate would fail runs for judge variance.

## Decision

**Run the full set, rarely, at the moments the answer matters.**

| where | when | what | secrets |
|---|---|---|---|
| GitHub Actions | every push / PR | `ruff` → `mypy` → `pytest` — free, deterministic, offline | **none** |
| GitHub Actions | push to `main` | build image → push to GHCR | **none** — `GITHUB_TOKEN` is per-job |
| VPS `cron` | monthly | **full** `eval-agent`, report to disk | on the box |
| VPS, manual | before a deploy | **full** `eval-agent`, gated against `eval/baselines.json` | on the box |

**`uv run eval-agent --limit N --skip-judge` stays what CLAUDE.md already
calls it — a local iteration tool, never a gate.**

**Monthly, not weekly, and the reason is what it watches for.** With nothing
in the repo changed, a scheduled run measures **external** drift: OpenRouter
re-routing `z-ai/glm-5.2` to another provider or quantization, a `-lite` alias
updated underneath us, a model deprecated, the embedding endpoint shifting.
That is drift we cannot cause and would not otherwise see. It is also
something a PR gate structurally cannot do, because nothing triggers it.

**And it runs on the VPS, not in CI**, for one practical reason and one about
blast radius:

- The eval needs the corpus, and `data/` is gitignored. A CI run would need a
  Qdrant service container plus `data/ingest.db` (13 MB, the XBRL facts store)
  restored from somewhere, plus an embed pass from `data/chunks` (12 MB) to
  repopulate vectors — real setup for a job that runs twelve times a year. The
  VPS already has all of it, because the service needs it to answer anything.
- Moving it off CI means **GitHub Actions never receives a secret**, which
  satisfies the "no secret in CI logs" exit item by construction rather than
  by discipline (ADR 0037).

**The monthly run does not pass `--write-baseline`.** A drift run reports; it
does not silently move the line it is measured against. `--write-baseline`
stays manual and stays gated (ADR 0016).

**On the VPS, but in a one-off container from the deployed image — not on the
host.** This paragraph said `nice -n 10 uv run eval-agent` until the first
real deploy the same day disproved it: the host has no `uv`, no `data/`, and
no route to Qdrant, which publishes no port because only `fie-api` needs it
(ADR 0033). Fixing that on the host meant a Python toolchain plus a ~600 MB
second copy of the image's dependencies on a disk with 7.9 GB free, a
published Qdrant port, and a second copy of the corpus. **The corpus copy is
the part that would have been wrong rather than merely wasteful** — re-seed
one and the eval measures the other, reporting drift that is really a stale
snapshot. A container off the same image and the same `fie_api_data` volume
keeps one corpus and one truth, and has the property the host version never
had: the drift run measures the artifact that is actually serving.

Its CPU cap is the service's own 1.0 rather than a `nice` level — ~75 minutes
sharing 2 vCPU with five production services, and the compose file already
argues that number. It shares `checkpoints.db` with the running service;
SQLite's busy timeout and the runner's retry cover that, and at 03:00 on the
1st there is nothing to contend with.

**Its spend is not governed by the public $5/month counter.** That governs the
HTTP path; the eval calls `answer_question()` directly. Size the provider
balance for the eval **plus** the demo.

## Consequences

`deploy/monthly-eval.sh` reads the same encrypted store the service does
(ADR 0029) but a wider slice of it, including the Anthropic judge key that
`fie-api` must never hold. It fails loudly if that key is absent, because a
store that satisfies `fie-api` proves nothing about the eval.

The exit gate for this phase is **one full run on the VPS passing against
`eval/baselines.json` before the deploy** — not a PR-time subset.

## Honest limits

- Monthly means up to a month of drift before detection. For a portfolio demo
  that is the right trade; for anything with users it is not.
- A cron eval that fails needs somewhere to shout. It writes to
  `/var/log/fie-eval.log` and its exit status is the signal; wiring that to
  the uptime alert destination is a deploy-time task, not a code one.
- Twelve runs a year at $0.69 plus judge cost is ~$10–15/year against a
  $5/month demo budget. Not free, and not accounted for anywhere the governor
  can see.
