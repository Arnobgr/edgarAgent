# ADR 0037 — Deploy is manual, and CI holds no credential for it

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Extends:** ADR 0029 (secrets), ADR 0031 (build in CI), ADR 0036 (eval on the
box)

## Context

CI builds the image (ADR 0031). The natural next step is for CI to deploy it,
and there is a clean way to do that: the **Tailscale GitHub Action** brings up
an ephemeral node on the tailnet, `ssh` reaches the VPS, `docker compose pull
&& up -d` runs. It needs one secret — a tailnet auth key.

## Decision

**CI builds and pushes; the operator deploys.** `ssh vps` →
`deploy/deploy.sh` → decrypt secrets → `docker compose pull && up -d` → poll
`/healthz` until Qdrant is `true` → smoke-test one canned question.

**The reason is that the two credentials are different failure classes and
only one is worth accepting.** An API key in CI is bounded by provider-side
caps and recoverable by rotation. A tailnet credential grants **network
reachability** — to the operator's Mac, and to a box running five production services.
Capped-and-rotatable spend and uncapped network reach are not comparable
risks, and a leaked auth key is not fixed by rotating it after the fact.

**With the eval already on the box (ADR 0036) and the deploy manual, no
workflow needs any secret at all.** That is the point: the "no secret in CI
logs" exit item becomes structural rather than a discipline that has to hold
on every future workflow edit.

**The gate is a grep, and it is meant to stay runnable:**

```
grep -r 'secrets\.' .github/    →  GITHUB_TOKEN and nothing else
```

It currently returns three lines — two in the header comment explaining this
rule, and one real use as the GHCR registry password. `GITHUB_TOKEN` is minted
per job, scoped to this repository, and expires when the job ends.

Note that GitHub's log masking would not be a control anyway: it **fails open
on transformed values**, so a base64'd secret prints in the clear. The
protection here is that there is nothing to mask.

**gitleaks runs from its release binary**, not from `gitleaks/gitleaks-action`
— the marketplace action reads a `GITLEAKS_LICENSE` secret, which would break
the rule for a scanner whose whole job is enforcing it.

## Consequences

Three jobs: `checks` (ruff, mypy, pytest as three steps so a red run names
which), `secret-scan` (full history, `--redact` so a finding does not print
what it found into a public log), and `image` — gated on both, and on
`push` to `main` only, so a pull-request build cannot publish unreviewed code
under a tag `deploy.sh` will pull.

A deploy is a human action, so it can be sequenced behind the full eval the
sequencing rule requires (ADR 0036). An automated deploy would have had to
either skip that or wait 75 minutes.

## Honest limits

- Manual deploy means deploys happen when the operator is available, and a rollback
  needs them too. The image is tagged by commit sha as well as `latest`, so the
  rollback exists; it is not automated.
- `deploy.sh` smoke-tests one canned question. That proves the path is alive,
  not that quality held — the eval does that, before the deploy, by hand.
- The grep gate is a convention, not a check. It is not enforced by a workflow
  (a workflow enforcing it would be one more workflow to edit). It belongs in
  the release checklist in `docs/security.md`, and it is there.
