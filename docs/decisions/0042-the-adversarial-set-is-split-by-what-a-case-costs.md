# ADR 0042 — The adversarial set is split by what a case costs to run

**Date:** 2026-08-11 · **Phase:** 6 · **Status:** accepted — the offline half
implemented 2026-08-11, the paid half is Step 3 · **Relates to:** ADR 0037
(CI holds no credential), ADR 0033 (a second Qdrant rather than a shared one),
ADR 0036 (the eval runs where the corpus is), ADR 0040 (the fence)

## Context

"Wire the adversarial cases into CI" is the obvious instruction and it is
impossible as written. ADR 0037 makes CI credential-free by construction, and
`grep -r 'secrets\.' .github/` returning only `GITHUB_TOKEN` **is** the exit
gate for that decision. Any case needing a real model call would need a real
key, and adding one to satisfy a test would trade the strongest structural
guarantee in the deploy story for a test that could run somewhere else.

The two kinds of case are also different in what they detect, which matters
more than the cost:

- **A mechanism case** asks whether the code still does what it was built to
  do — the fence token cannot survive, a submission without a Turnstile token
  is refused, no prompt path reaches a secret. It regresses from an unrelated
  refactor, it is deterministic, and it is free.
- **A behaviour case** asks whether a *model* resists an attack — does
  synthesis follow an injected instruction, does a judge accept an excerpt
  that vouches for a claim. It regresses when a provider re-routes a model or
  an alias moves underneath us, it is stochastic, and it costs money.

Running the first kind rarely wastes the cheapest signal there is. Running the
second kind on every push would spend real money on a stochastic answer to a
question nobody asked on that commit.

## Decision

**1. Mechanism cases live in `tests/` and run in `make test` and in CI.** They
are in `tests/test_security.py` and `frontend/src/test/output-safety.test.tsx`,
plus the abuse cases already in `tests/test_api_abuse.py`. Offline, no keys, no
corpus — the property of the suite that ADR 0037 depends on.

They are **not** in `eval/adversarial/`. A directory that mixes free
deterministic assertions with paid stochastic ones has to be run selectively to
be run at all, and a set that is run selectively is a set that stops being run.

**2. Behaviour cases live in `eval/adversarial/` and run with `eval-agent`** —
on the Mac, or on the box through `deploy/monthly-eval.sh`, which is where the
keys and the corpus already are (ADR 0036). Never in CI.

**3. The poisoned corpus is a separate Qdrant collection**, and this is not a
tidiness preference. The collection on the VPS is a *snapshot shipped from the
Mac* and the deploy's parity rests on it — 28,550 facts, 78 filings, 4,181
points, counted equal. There is no second copy. A poisoned chunk written into
it is unrecoverable without a re-ingest, which ADR 0040 has already priced.
This is the same reasoning as ADR 0033: a demo's experiment must not be able to
damage the thing that is serving.

**4. Poisoned documents are authored, not derived, so `eval-seed` leaves them
alone.** Written down rather than inferred from an absence of code, and the set
carries a `label_status` and a provenance note like the other four in
`eval/README.md`. An adversarial set that skips that convention is the one
nobody can tell is stale.

**5. The paid half runs once per phase, not per change.** The exit gate asks
for a passing run with its report under `eval/reports/`, in the same breath as
the unregressed Phase 3 eval. Iterating against it is how a $0.69 run becomes
a $20 week.

## Consequences

The offline half is nine assertions in `tests/test_security.py` and four in
`output-safety.test.tsx`, all green on 2026-08-11, and they are already the
regression net for ADR 0040.

**The split is a real limit, not just an allocation.** CI proves the fence
token cannot survive a chunk that contains it. It does not prove a model
ignores what is inside the fence — nothing free can prove that, and the paid
half is the only evidence for the sentence the writeup most wants to make.

## Honest limits

- **A green CI run is not evidence the system resists prompt injection**, and
  the writeup must not imply it is. It is evidence the controls are still
  wired up.
- **The paid half is a single reading of a stochastic system.** ADR 0039
  measured the judge flipping on 1 in 27 byte-identical spans; an adversarial
  case that passes once has passed once. Report it that way.
- **A drift run does not include the adversarial set.** `monthly-eval.sh` runs
  the gold set, so a model that becomes *more* injectable between phases is
  invisible until someone runs it by hand. Folding it in is a later decision
  with a cost attached, and the trigger is the paid half finding anything at
  all.
