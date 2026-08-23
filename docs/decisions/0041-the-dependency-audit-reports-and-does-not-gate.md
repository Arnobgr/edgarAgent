# ADR 0041 — The dependency audit reports; it never fails the build

**Date:** 2026-08-11 · **Phase:** 6 · **Status:** accepted — implemented
2026-08-11 · **Relates to:** ADR 0037 (CI holds no credential), ADR 0031
(where the image is built), ADR 0036 (a check whose exit status is the signal)

## Context

Story 6 of the threat model was **Open** with one sentence: nothing in this
repo answers "which of our dependencies has a known advisory".
`.github/workflows/` held one file and no audit step.

Adding one collides with a policy this repo already has. `frontend/.npmrc`
pins `ignore-scripts=true` and `before=2026-07-23` — never resolve a version
published in the last fortnight, because a malicious release of a legitimate
package is usually caught and yanked within days. **A hold is the mechanism,
so an audit will report advisories that are being waited out on purpose.**
Deciding after the first red build means deciding under pressure to make it
green.

So the audit was run by hand first, on 2026-08-11, to decide against real
numbers instead of a hypothetical.

| package | advisory | severity | in the tree via | reachable here |
|---|---|---|---|---|
| `h2` 4.3.0 | PYSEC-2026-3628 — duplicate `Host` headers are a request-smuggling primitive | — | `httpx` extra, under `httpxthrottlecache` (edgartools) | **no** — the flaw is in accepting requests; we are a client, uvicorn serves HTTP/1.1, and nothing sets `http2=True` |
| `langgraph-checkpoint-sqlite` 3.1.0 | PYSEC-2026-3636 — namespace `LIKE` prefix match crosses a tenant boundary | — | direct | **no** — the flaw is in the *store*; this system uses the checkpointer, and has one tenant |
| `postcss` ≤ 8.5.22 | GHSA-fxqj-rqcc-2cmp — `sourceMappingURL` reads arbitrary `.map` files | moderate | transitive under `vite` (**dev**) | build machine only; nothing here ships to the browser |
| `nanoid` < 3.3.17 | GHSA-2v37-7h3g-55p8 — custom generators can loop forever at size zero | **high** | transitive under `postcss` → `vite` (**dev**) | build machine only |

**A gating audit would be red today, on four findings of which none is
reachable in this system, and the highest-severity one lives in a package that
never leaves the build machine.** The postcss fix is also the dated item left
over from Phase 5: 8.5.25 becomes eligible on **2026-08-12** under the current
`before=` date, one day after this ADR.

## Decision

**1. The audit runs on every CI run and never fails on a finding.** It is a
separate `audit` job, and nothing depends on it, so it can never block the
`image` job or a deploy. A *tooling* failure still goes red — an audit that
did not run must not look like an audit that found nothing.

The alternative — fail on `high` and above — was rejected on the table above.
It would have gone red on `nanoid`, a build-time transitive dependency of
`vite`, and the only available response would have been to override the hold
policy that is the repo's actual supply-chain control. A gate that can only be
satisfied by disabling a stronger control is worse than no gate.

**2. "Only reports" needs a second half, or it is a check that checks
nothing.** This is the failure mode ADR 0039 names: an alarm nobody reads
loses the ability to report what it exists to catch. Three things make the
report reach a person:

- the findings are written to `$GITHUB_STEP_SUMMARY`, so they are on the run
  page rather than inside a log nobody opens;
- the job also runs on a **weekly schedule**, because a new advisory against an
  unchanged tree is exactly what a push-triggered audit never sees, and that is
  the normal case;
- the standing findings are triaged by *reachability* in `docs/security.md`,
  not merely listed. A list of four names ages into noise; four verdicts age
  into a diff.

**3. No new dependency in either ecosystem, in a phase about supply chain.**
`uv export --format requirements-txt --no-dev` writes the locked production
tree and `uvx pip-audit` runs ephemerally, so nothing enters `pyproject.toml`
or `uv.lock`. `npm audit` reads the committed lockfile and installs nothing —
which also means the audit job needs no `npm install`, and `ignore-scripts` is
never even tested.

`--no-dev` is deliberate on the Python side: the deployed image installs
production dependencies only, so a dev-only advisory there is a finding about
this laptop, not about the service. The npm side has the opposite shape —
everything in `frontend` is a devDependency except React — so it is audited
whole and the *dev* column is what the triage table carries.

**4. ADR 0037 is unaffected.** Neither tool takes a credential;
`grep -r 'secrets\.' .github/` still returns `GITHUB_TOKEN` and nothing else.

## Consequences

`ci.yml` gains one job. The four findings above are recorded in
`docs/security.md` under story 6, which is where the next reader looks, and
the story stops being **Open**.

**The postcss bump is not done here and that is the whole point of the
mechanism.** 8.5.25 is eligible from 2026-08-12; taking it needs `before=`
moved and an `npm install`, which is a deliberate act on a day the fortnight
has passed, not a reaction to a red build.

### Amendment, 2026-08-23 — the bump, taken

Both npm rows of the table above are cleared. `before=` moved to
**2026-08-09**, a fortnight back from the day the bump was taken, so the window
is preserved rather than suspended for the occasion; `npm update postcss
nanoid` then resolved **postcss 8.5.26** and **nanoid 3.3.18**. `npm audit`
reports 0, and `npm run check` is green.

Two details are worth keeping. **The versions are not the ones this ADR named**
— 8.5.26 and 3.3.18 landed on 08-06 and 08-07, after the text above was
written, so the hold delivered a newer fix than the one being waited for rather
than the specific release. And **eleven days passed between eligibility and the
bump**, which is the mechanism behaving correctly: nothing red was showing, so
nothing forced the day. A hold that must be taken the moment it clears is a
deadline, and a deadline is what this decision refused to build.

The Python rows are untouched: `uv.lock` still pins `h2` 4.3.0 and
`langgraph-checkpoint-sqlite` 3.1.0, both unreachable here for the reasons in
the table. Whether a *new* advisory has landed against that tree is the weekly
`audit` job's answer, not this amendment's — it was not re-run by hand.

## Honest limits

- **Severity is the advisory's, reachability is ours, and only the second one
  is in the table.** That triage is hand-written and will go stale — it is a
  dated reading, like every other measurement in this repo.
- **The audit sees declared dependencies, not what is in the image.** A
  vulnerable OS package in the base layer is invisible to both tools. Container
  scanning is out of scope for a demo and is named here so its absence is a
  choice.
- **`before=2026-07-23` is itself unaudited.** Nothing checks that the date is
  being moved at all; a forgotten hold looks exactly like a deliberate one.
  The weekly run is what surfaces it — the same advisory reported for months
  is the signal.
