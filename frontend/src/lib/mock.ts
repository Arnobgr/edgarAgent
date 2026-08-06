/**
 * `VITE_MOCK=1` — the live flow, driven by the recorded runs.
 *
 * It invents nothing. A submitted question is matched against
 * `recorded/index.json` and the answer that comes back is the payload
 * `fie export-runs` wrote, so the mock cannot show a shape the real API would
 * not return. What it fakes is only the passage of time: the six phases
 * advance on a timer so the status view can be built and demonstrated without
 * a backend, a corpus or a spend.
 */

import type { JobAccepted, JobStatusResponse, RecordedIndex, RunPayload } from "../types/api";

const PHASES = ["starting", "researching", "synthesizing", "verifying", "finalizing"] as const;
const STEP_MS = 1400;

interface MockJob {
  jobId: string;
  question: string;
  companies: string[];
  startedAt: number;
  recordedId: string;
}

const jobs = new Map<string, MockJob>();

function normalise(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
}

async function index(): Promise<RecordedIndex> {
  const response = await fetch(`${import.meta.env.BASE_URL}recorded/index.json`);
  return (await response.json()) as RecordedIndex;
}

/** The recorded run whose question shares the most words with the one asked.
 *  A demo of the real thing, not a random pick. */
async function bestMatch(question: string): Promise<string> {
  const { runs } = await index();
  const asked = new Set(normalise(question).split(" "));
  let best = runs[0];
  let bestScore = -1;
  for (const run of runs) {
    const words = new Set(normalise(run.question).split(" "));
    let overlap = 0;
    for (const word of words) if (asked.has(word)) overlap += 1;
    const score = overlap / Math.max(words.size, 1);
    if (score > bestScore) {
      bestScore = score;
      best = run;
    }
  }
  return best?.job_id ?? "";
}

export async function mockSubmit(question: string, companies: string[]): Promise<JobAccepted> {
  const jobId = Math.random().toString(16).slice(2, 14);
  jobs.set(jobId, {
    jobId,
    question,
    companies,
    startedAt: Date.now(),
    recordedId: await bestMatch(question),
  });
  return { job_id: jobId, status: "queued", source: "live" };
}

export function mockStatus(jobId: string): JobStatusResponse {
  const job = jobs.get(jobId);
  if (!job) throw new Error(`no mock job ${jobId}`);
  const elapsed = Date.now() - job.startedAt;
  const step = Math.floor(elapsed / STEP_MS);
  const done = step >= PHASES.length;
  const phase = PHASES[Math.min(step, PHASES.length - 1)] ?? "starting";
  const at = new Date(job.startedAt).toISOString();
  return {
    job_id: jobId,
    status: done ? "succeeded" : "running",
    question: job.question,
    companies: job.companies,
    created_at: at,
    updated_at: new Date().toISOString(),
    source: "live",
    cost_usd: Math.min(step * 0.009, 0.039),
    tokens: Math.min(step * 8200, 36106),
    error: null,
    progress: {
      step,
      phase,
      verify_iteration: step >= 3 ? 1 : 0,
      critic_iteration: step >= 4 ? 1 : 0,
      cost_usd: Math.min(step * 0.009, 0.039),
      tokens: Math.min(step * 8200, 36106),
    },
  };
}

export async function mockResult(jobId: string): Promise<RunPayload> {
  const job = jobs.get(jobId);
  if (!job) throw new Error(`no mock job ${jobId}`);
  const response = await fetch(`${import.meta.env.BASE_URL}recorded/runs/${job.recordedId}.json`);
  const payload = (await response.json()) as RunPayload;
  // Labelled `live`, because that is the path being exercised. The answer is
  // a recorded one and the question shown is the recorded question — the mock
  // does not pretend to have answered what was typed.
  return { ...payload, job_id: jobId, source: "live" };
}
