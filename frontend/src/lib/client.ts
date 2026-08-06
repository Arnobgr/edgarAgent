/**
 * The API client.
 *
 * `VITE_API_KEY` ships in the bundle and that is by design, not an oversight:
 * on the public path the key *identifies*, it does not control (ADR 0030).
 * What holds this surface is Turnstile, a USD spend governor, a per-IP daily
 * quota and nginx — none of which the browser can weaken by knowing the key.
 * Anyone reading this file has learned nothing they could spend.
 *
 * Failures are typed rather than stringly, because two of them are screens
 * with their own wording: a Turnstile refusal has a retry, and a limit has a
 * reason the visitor can act on.
 */

import type { JobAccepted, JobStatusResponse, RecordedIndex, RunPayload } from "../types/api";
import { mockResult, mockStatus, mockSubmit } from "./mock";

const API_BASE = (import.meta.env["VITE_API_BASE"] ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const API_KEY = import.meta.env["VITE_API_KEY"] ?? "";

export const TURNSTILE_SITE_KEY = import.meta.env["VITE_TURNSTILE_SITE_KEY"] ?? "";
/** Serve the recorded runs in place of the API, for local work and tests. */
export const MOCK = import.meta.env["VITE_MOCK"] === "1";

export type FailureKind = "turnstile" | "limited" | "rejected" | "missing" | "network" | "server";

export class ApiError extends Error {
  readonly kind: FailureKind;
  readonly status: number;

  constructor(kind: FailureKind, status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

function kindFor(status: number): FailureKind {
  if (status === 403) return "turnstile";
  if (status === 429) return "limited";
  if (status === 422 || status === 400) return "rejected";
  if (status === 404) return "missing";
  return "server";
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError("network", 0, "The engine is unreachable. Check your connection.");
  }
  if (!response.ok) {
    // FastAPI puts the message in `detail`; it is written for a reader in
    // `app.py` (quota, capacity, corpus scope) so it is shown as-is.
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(
      kindFor(response.status),
      response.status,
      body?.detail ?? `The engine returned ${response.status}.`,
    );
  }
  return (await response.json()) as T;
}

export interface AskInput {
  question: string;
  companies: string[];
  turnstileToken: string | null;
}

export function submitQuestion(input: AskInput): Promise<JobAccepted> {
  if (MOCK) return mockSubmit(input.question, input.companies);
  return call<JobAccepted>("/v1/questions", {
    method: "POST",
    body: JSON.stringify({
      question: input.question,
      companies: input.companies,
      turnstile_token: input.turnstileToken,
    }),
  });
}

export function jobStatus(jobId: string): Promise<JobStatusResponse> {
  if (MOCK) return Promise.resolve(mockStatus(jobId));
  return call<JobStatusResponse>(`/v1/jobs/${jobId}`);
}

export function jobResult(jobId: string): Promise<RunPayload> {
  if (MOCK) return mockResult(jobId);
  return call<RunPayload>(`/v1/jobs/${jobId}/result`);
}

/**
 * Live runs only. A recorded run's trace was produced on the developer's
 * machine against a local Langfuse, while production traces go to Langfuse
 * Cloud — so an exported id would be an identifier no visitor could resolve
 * (Phase 5 decision 6). The result view never calls this for `recorded`.
 */
export function jobTrace(jobId: string): Promise<{ trace_id: string | null }> {
  return call<{ trace_id: string | null }>(`/v1/jobs/${jobId}/trace`);
}

/** The recorded runs are static files beside the bundle, so a reader who only
 *  reads never reaches the VPS at all (ADR 0027). */
export async function fetchRecordedIndex(): Promise<RecordedIndex> {
  const response = await fetch(`${import.meta.env.BASE_URL}recorded/index.json`);
  if (!response.ok) throw new ApiError("missing", response.status, "No recorded runs are bundled.");
  return (await response.json()) as RecordedIndex;
}

export async function fetchRecordedRun(jobId: string): Promise<RunPayload> {
  const response = await fetch(`${import.meta.env.BASE_URL}recorded/runs/${jobId}.json`);
  if (!response.ok) throw new ApiError("missing", response.status, "That recorded run is not here.");
  return (await response.json()) as RunPayload;
}
