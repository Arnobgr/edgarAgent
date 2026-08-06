/**
 * The recorded runs, as fixtures.
 *
 * The plan's "fixtures are free" note is the point: `recorded/runs/*.json` is
 * 29 payloads in the exact shape the live API returns, produced by
 * `fie export-runs` from the checkpoints of the accepted 2026-08-01 baseline.
 * A hand-written fixture would be a guess about the contract; these are the
 * contract, so a backend change surfaces here rather than in a browser.
 *
 * Loaded through `import.meta.glob` rather than `node:fs` so the tests read
 * them exactly as the bundle does, and so nothing here needs Node's types.
 */

import type { RecordedIndex, RunPayload } from "../types/api";

const RUNS = import.meta.glob<{ default: RunPayload }>("/public/recorded/runs/*.json", {
  eager: true,
});

const INDEX = import.meta.glob<{ default: RecordedIndex }>("/public/recorded/index.json", {
  eager: true,
});

export function recordedIndex(): RecordedIndex {
  const loaded = Object.values(INDEX)[0];
  if (!loaded) throw new Error("recorded/index.json is not where the tests expect it");
  return loaded.default;
}

export function recordedRuns(): RunPayload[] {
  return Object.entries(RUNS)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, module]) => module.default);
}

export function recordedRun(jobId: string): RunPayload {
  const found = recordedRuns().find((payload) => payload.job_id === jobId);
  if (!found) throw new Error(`no recorded run ${jobId}`);
  return found;
}

// Re-exported from the app rather than restated, so a test and the view can
// never disagree about which run a stranger lands on.
export { LANDING_RUN_ID as LANDING_RUN, UNMARKABLE_RUN_ID as SPANLESS_RUN } from "../lib/recorded";
