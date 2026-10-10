# ADR 0046 — A draft must state its claims: `claims` is a required field

**Date:** 2026-10-10 · **Phase:** 7 · **Status:** accepted, implemented
2026-10-10, **measured the same day** (see *Measured*) · **Extends:** ADR 0038
(an answer with no claims is a failed run), ADR 0025 (a degraded attempt
disqualifies a baseline)

## Context

Two full evals on the box in two days, and neither can be a baseline. Run
`agent-20261009-162531` finished with `degraded_attempts: 9` and
`runs_with_no_claims: 4`. The 2026-10-10 re-run was stopped at g-011 after two
degraded attempts, g-007 and g-011. Every one of those degraded attempts has
the cause ADR 0038 §1 installed: *synthesis produced no claims*. The re-ask
that ADR adds came back claimless as well.

**The first diagnosis was wrong, and is recorded so that it is not repeated.**
On 2026-10-09 every failure fell inside one 90-minute window, while probes
pinned to four providers and run afterwards all succeeded. The failures were
put down to a transient upstream problem. The 10-10 run then failed the same
way, a day later, on the same image.

**What the traces show.** The Langfuse Cloud traces still held, from 2026-10-01
to 2026-10-10, contain 231 `synthesis` generations. Two of the claimless calls,
from g-007 on 10-10, look like this:

```json
{"answer": "Tesla's operating income (income from operations) in fiscal year 2025 was
 $4,355 million ... [xbrl:0001628280-26-003952:us-gaap:OperatingIncomeLoss:2025-12-31] ..."}
```

The answer is correct and cited, and the arguments carry **no `claims` key at
all**. Across all 231 generations, 51 omit the key. **None** sends an
explicit `"claims": []`.

**Why that parses as success.** `SynthesisOutput.claims` had
`Field(default_factory=list)`. A field with a default is left out of the tool
schema's `required` list. Checked offline through the real `ChatOpenAI`
`function_calling` path with a mocked transport, the schema sent on the wire was
`"required": ["answer"]`. Pydantic filled the missing key with `[]` and no error
was raised. The schema told the model the field was optional; the model
sometimes treated it that way; and ADR 0038 then failed a run whose answer was
fine.

**Which backend.** The trace does not record which OpenRouter provider served a
call, but the tool-call id format is a fingerprint:

| tool-call id shape | 10-01 | 10-09 / 10-10 |
|---|---|---|
| `call_<24 hex>` | 13 of 13 carry `claims` | **50 of 84 omit it** |
| every other shape (`call_<base62>`, `chatcmpl-tool-…`, `call-…`) | 33 of 33 carry it | 1 of 68 omits it |

So the omission is mostly one backend. That backend sent the field every time
on 10-01 and omits it most of the time now, so its behaviour changed on
OpenRouter's side between those dates. Nothing in this repo changed it: the
same failure reproduced on the image that ran the clean 10-01 eval. The failure
became visible because our schema made the field optional.

## Decision

**`claims` is required, with no default.** It is now in the tool schema's
`required` list, so a backend that honours the schema sends it. If one omits it
anyway, the `ValidationError` reaches `ainvoke_with_retry`, which already
retries any exception up to three attempts. The 22 tool-call-less responses on
2026-10-09 were retried through that same path. The ADR 0038 repair call gets the
same three attempts.

**Required is not non-empty.** An explicit `"claims": []` still parses, still
reaches the ADR 0038 repair, and still fails the run if the repair is also
empty. Whether an answer is verifiable stays decided where ADR 0038 put it.

**Rejected: pinning or excluding the provider.** `ModelSpec` has no field for
OpenRouter provider routing, so pinning would need new configuration. It would
tie availability to one vendor. It would also leave the schema wrong for the
next backend that drops an optional field: the critic's outputs here omit their
optional fields routinely, which is legitimate there and would not be for
`claims`. Pinning remains open if the retry rate turns out to be high.

**This is not tuning for a gate metric.** No prompt, model or retrieval setting
changed. The change corrects the output contract to say what ADR 0038 already
required. By itself it can move `degraded_attempts`; it cannot make an
unsupported claim grounded.

## Tests

`tests/test_router.py` drives `bind_structured` and `ainvoke_with_retry`
through a real `ChatOpenAI` over `httpx.MockTransport`, offline:

- an answer with no `claims` key raises a `ValidationError` after three HTTP
  calls, and the schema on the wire lists `claims` as required;
- an explicit `"claims": []` parses after one call, so ADR 0038 still decides
  it.

With the default restored, the first test fails.

## Consequences

- An omission costs one more synthesis call rather than a degraded attempt and
  a whole re-run of the question. A draft that omits the key on all three
  attempts degrades exactly as it did before, so the worst case is unchanged.
- Each retry runs inside the same `guarded` node timeout (600 s in the eval,
  180 s on the public path), so it adds latency. It does not add a new
  unbounded path.

## Honest limits

- **Unmeasured.** The arithmetic is suggestive, not proof. Taking 10-09/10
  routing, that backend served 84 of 152 synthesis calls and omitted the key
  in 50 of them, so roughly a third of calls omitted it, and three
  independent tries would all fail about 4% of the time. The attempts are not
  shown to be independent, though: g-007's draft and repair both hit the same
  backend. It is also unknown whether listing `claims` as required changes that
  backend's behaviour at all. The next full eval measures it, and
  `degraded_attempts` is the number to read.
- **ADR 0038's original case is plausibly the same mechanism, not shown to be.**
  Run `2b11fa1d4f9e` (2026-08-01) had a cited figure and zero claims. ADR 0038
  could not explain it, because only the mapped claims were checkpointed. An
  omitted key fits that case exactly, but its raw output is gone and Langfuse
  Cloud no longer holds August.
- **Adjacent, not addressed here.** Inside claims that are present,
  `source_chunk_ids` is absent in 136 of 920, and `answer_span` in 9. Neither
  is silent. A claim with no cited chunk is ruled `unsupported` with feedback
  that drives a loop-back, and a missing span falls back to token overlap
  (ADR 0023), which is logged. *Corrected below:* the first draft of this ADR
  called making them required "a separate decision" because it would change
  verification. It would not; it is the same contract bug.
- The id-shape fingerprint names no provider. OpenRouter's Activity page for
  2026-10-09 15:00–17:00 UTC would name it.

## Measured

Run `agent-20261010-102654` on image `8b26d55`, the same morning, became the
accepted baseline: 30/30, `degraded_attempts: 0`, `runs_with_no_claims: 0`.

- **The retry never fired.** The run logged no `claims` validation errors.
  The `call_<24 hex>` backend served 37 of the 58 traced synthesis calls and
  **included `claims` in all 37**, against 50 omissions in 84 calls before the
  change. Listing the field as required appears to be what that backend obeys.
  A provider-side change in the same half hour cannot be excluded, because the
  last omission was at 09:23Z and the run started at 09:53Z.
- **The same defect, one level down, cost a question.** In g-014 the same
  backend omitted `source_chunk_ids` from all six claims in four consecutive
  drafts. The prose cited the right XBRL facts inline, but the claims cited
  nothing, so all six correct figures were retracted. Those are all three
  missing expected facts (coverage 0.893). Across the run, that backend
  omitted the key in 51 of 277 claims; every other backend in 3 of 176. g-002
  lost its `answer_span`s the same way, and its prose cannot be highlighted.
  Making both `DraftClaim` fields required is the obvious next step. It is not
  taken here, because it changes the answer path and needs its own eval.
