/**
 * The HTTP contract, mirrored from the Pydantic models.
 *
 * Hand-written rather than generated, and kept in one file, because the whole
 * surface is nine types and a generator would be a build step to maintain for
 * no gain. The source of truth is `src/fie/api/models.py` and
 * `src/fie/state/__init__.py`; when a field moves there it moves here, and
 * `runPayloadFromJson` below is where a drift shows up as a thrown error
 * rather than as an undefined at render time.
 */

/** How the answer reached the caller. `recorded` was exported ahead of time
 *  and ships inside the bundle; `cached` is a byte-identical earlier answer. */
export type RunSource = "live" | "cached" | "recorded";

/** Derived from our own state channels, not LangGraph's task bookkeeping, so
 *  a library upgrade cannot silently blank it. There is no `current_node`. */
export type RunPhase =
  | "starting"
  | "researching"
  | "synthesizing"
  | "verifying"
  | "reviewing"
  | "finalizing";

export type JobState =
  | "queued"
  | "running"
  | "awaiting_review"
  | "succeeded"
  | "failed"
  | "interrupted";

/**
 * Six values, and the difference between the last two is the point of ADR
 * 0034: `accepted` is a human waving a claim through, and colouring it as
 * `grounded` would launder an opinion into the grounding rate in the one
 * place a reader would believe it.
 */
export type ClaimStatus =
  | "pending"
  | "grounded"
  | "unsupported"
  | "partial"
  | "retracted"
  | "accepted";

/** The verifier's ruling — three values, and a different field from `status`,
 *  which is the final word and carries `retracted` and `accepted`. */
export type VerdictLabel = "grounded" | "partial" | "unsupported";

export type ClaimType = "numeric" | "qualitative" | "forward_looking";

export type VerifyMethod = "numeric_reconciliation" | "semantic" | "attribution";

export interface SourceRef {
  chunk_id: string;
  doc_id: string;
  ticker: string;
  form_type: string;
  section: string;
  accession_no: string;
  filing_date: string;
}

export interface GroundingRow {
  claim_id: string;
  text: string;
  claim_type: ClaimType;
  status: ClaimStatus;
  verdict: VerdictLabel | null;
  method: VerifyMethod | null;
  /** Quotes the *source*. */
  supporting_span: string | null;
  /** Quotes the *answer* — the sentence this claim was drawn from, captured at
   *  synthesis (ADR 0023). Empty when synthesis never produced one, which is
   *  the only reason a claim cannot be located in the prose. */
  answer_span: string;
  feedback: string | null;
  sources: SourceRef[];
}

export interface SubQuestion {
  id: string;
  text: string;
  rationale: string;
  status: string;
}

export interface RunBudget {
  max_tokens: number;
  max_cost_usd: number;
  tokens_used: number;
  cost_usd_used: number;
}

export interface RunResult {
  run_id: string;
  question: string;
  answer: string;
  sub_questions: SubQuestion[];
  /** Two lists, not one list with a status field. */
  grounding: GroundingRow[];
  retracted: GroundingRow[];
  verify_iterations: number;
  critic_iterations: number;
  critic_passed: boolean | null;
  budget: RunBudget;
  error: string | null;
  /** Cannot occur with review mode off, which is how production runs. Treated
   *  as an error state, not a screen (Phase 5 decision 2). */
  awaiting_review: boolean;
}

export interface RunPayload {
  job_id: string;
  source: RunSource;
  created_at: string;
  result: RunResult;
}

export interface Progress {
  step: number | null;
  phase: RunPhase;
  verify_iteration: number;
  critic_iteration: number;
  cost_usd: number;
  tokens: number;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobState;
  question: string;
  companies: string[];
  created_at: string;
  updated_at: string;
  source: RunSource;
  cost_usd: number;
  tokens: number;
  error: string | null;
  progress: Progress | null;
}

export interface JobAccepted {
  job_id: string;
  status: JobState;
  source: RunSource;
}

/** One entry of `recorded/index.json` — what a static host cannot list for
 *  itself. `path` is relative to the index so the frontend fetches without
 *  reconstructing a layout. */
export interface RecordedEntry {
  job_id: string;
  question: string;
  created_at: string;
  path: string;
  claims: number;
  retracted: number;
}

export interface RecordedIndex {
  runs: RecordedEntry[];
}
