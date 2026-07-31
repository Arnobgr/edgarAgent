# ADR 0023 — Claims carry the sentence they came from; retraction excises it

**Date:** 2026-07-31 · **Phase:** 3 (post-gate remediation) · **Status:** accepted

## Context

ADR 0021 stopped retraction from mis-*reporting* what it deleted. It did not
stop it from deleting the wrong thing, because the root cause is upstream:

**the claim → sentence mapping is discarded at synthesis and guessed back at
retract time.** The synthesizer writes both the prose and the claim list and
knows the correspondence exactly, but `DraftClaim` carried `text`,
`claim_type` and `source_chunk_ids` — no span, no offset. `retract` then
recovered it by best token overlap, and one draft's claims overlap heavily
("Data Center revenue was $X billion, up Y% year-on-year"), so a claim's best
match was routinely the sentence stating a different claim.

The synthesis prompt compounded it by asking for each claim "restated as one
self-contained sentence", which is a *different object* from a sentence of
the answer — the restatement is what the verifier must judge standalone, and
the sentence is what a reader would lose.

## Decision

**1. `DraftClaim` carries `answer_span`: the sentence of `answer` that states
the claim, quoted verbatim.** The prompt now asks for both, with their
different jobs stated: `text` stands alone for the verifier, `answer_span` is
copied character for character out of the answer because it is what gets
removed. Two claims made by one sentence quote that same sentence.

**2. The span is validated as a substring of the answer, and repaired by one
bounded retry when it is not.** Models quote imperfectly, so the validation
is load-bearing. Unlike the failure it replaces, this one is **detectable** —
a substring check — rather than silent. A claim still unquoted after the
retry keeps an empty span.

**3. `retract` excises the span.** Exact, idempotent when two claims quote one
sentence, independent of iteration order, and incapable of taking a sentence
from another claim by accident. The Stage B invariant holds by construction
rather than by correction.

**4. Token-overlap excision is demoted to a fallback** for claims carrying no
span, and its use is logged so the fallback's reach stays visible instead of
being assumed to be zero.

**5. Retraction stays deterministic code.** An LLM rewrite ("rewrite this
answer without claim X") would reintroduce exactly the compliance-trust
problem verification exists to remove — the reasoning in `graph/retract.py`'s
docstring is unchanged and still holds.

## Consequences

- The three collateral-deletion states ADR 0021 detects and corrects become
  extinct: a claim can only lose the sentence it quoted. The drop-and-log
  branch in `retract` is now a vestigial guard, kept because a non-quoting
  model still falls through to the heuristic.
- The synthesis contract changed, so this had to land before the
  re-baseline — every draft the eval scores is produced under it.
- Cost: one extra synthesis call per pass in which any claim fails to quote.
  Synthesis is the expensive role (GLM 5.2 on the challenger arm), so the
  repair is bounded to a single retry and never loops.
- A model that will not quote after repair degrades to exactly the old
  behaviour for those claims, which is the honest floor — no worse than
  before, and now counted.

## Honest limits

The substring check is strict: a span differing by whitespace or a character
fails and triggers repair. That is deliberate — the alternative is
re-introducing fuzzy matching, which is the defect. The counter on the
fallback path is what will say whether strictness costs more than it buys;
if the fallback fires often on real runs, the answer is a better prompt, not
a looser check.
