/**
 * Laying claims over the answer prose — the one piece of this frontend with
 * real logic.
 *
 * The engine hands us an answer and two lists of claims, and the headline view
 * colours each claim's sentence in the prose. Everything hard about that is
 * here, and all of it is forced by how the backend actually behaves:
 *
 * - **The answer already carries its own retraction section.** `retract`
 *   appends `### Retracted claims` and lists them, so rendering the payload's
 *   `retracted` array under an unsplit answer prints every retraction twice.
 *   6 of the 29 recorded answers carry the header.
 * - **A retracted claim's span is *supposed* to be missing from the prose.**
 *   That is what excision means. Across the recorded corpus the count of spans
 *   absent from the body equals the count of retracted rows exactly, in every
 *   run. So retracted rows are never looked for in the body, and their absence
 *   is not an anomaly.
 * - **Spans overlap.** One sentence can carry two claims — 10 such pairs in the
 *   recorded corpus — so the prose cannot be rendered by wrapping each claim
 *   in turn. It is swept into non-overlapping atoms instead, each knowing
 *   every claim that covers it (ADR 0021 point 3, predicted extinct under ADR
 *   0023 and not extinct).
 * - **Two different failures both mean "cannot highlight", and the UI must not
 *   merge them.** `spanless` is synthesis never emitting a span; `unlocated` is
 *   a span that exists but is no longer in the prose, which means another
 *   claim's excision took the sentence with it. The first is a gap in the
 *   record, the second is a known live defect. Guessing a position for either
 *   — token overlap, fuzzy match — would put a colour on a sentence no
 *   verifier ruled on, which is the one thing this project must never render.
 */

import type { GroundingRow } from "../types/api";

/** Written by `retract` in `src/fie/graph/build.py`; part of the answer
 *  contract, not a formatting choice. */
export const RETRACTED_HEADER = "### Retracted claims";

export interface AnswerSplit {
  /** The answer proper. */
  body: string;
  /** The appended retraction notice, verbatim and unrendered. Kept only so a
   *  caller can prove the split happened; the retracted rows are rendered from
   *  the payload, which carries verdicts and sources the prose does not. */
  notice: string;
}

export function splitAnswer(answer: string): AnswerSplit {
  const at = answer.indexOf(RETRACTED_HEADER);
  if (at === -1) return { body: answer.trim(), notice: "" };
  return {
    body: answer.slice(0, at).trim(),
    notice: answer.slice(at).trim(),
  };
}

export type BlockKind = "paragraph" | "bullet" | "ordered" | "heading";

export interface Block {
  kind: BlockKind;
  /** Heading depth, or the number on an ordered item. 0 otherwise. */
  level: number;
  /** Offsets into the body of the block's *content*, with any list bullet or
   *  heading hashes excluded — so a claim span, which never quotes the marker,
   *  lines up with them. */
  start: number;
  end: number;
}

const HEADING = /^(#{1,6})\s+/;
const BULLET = /^[-*]\s+/;
const ORDERED = /^(\d+)\.\s+/;

/**
 * Split the body into blocks. A deliberately small Markdown subset — headings,
 * bullets, ordered items, paragraphs, and inline bold and citations below —
 * because that is the whole of what synthesis emits: measured across the
 * recorded corpus, 55 bold pairs, 22 ordered items, 16 bullets, 4 headings and
 * zero tables. A full Markdown library would parse to an AST with no offsets
 * back into the source string, and offsets are the entire problem here.
 */
export function parseBlocks(body: string): Block[] {
  const blocks: Block[] = [];
  let paragraphStart = -1;
  let paragraphEnd = -1;

  const flush = (): void => {
    if (paragraphStart === -1) return;
    blocks.push({ kind: "paragraph", level: 0, start: paragraphStart, end: paragraphEnd });
    paragraphStart = -1;
  };

  let offset = 0;
  for (const line of body.split("\n")) {
    const start = offset;
    offset += line.length + 1;
    const end = start + line.length;

    if (line.trim() === "") {
      flush();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({
        kind: "heading",
        level: (heading[1] ?? "#").length,
        start: start + heading[0].length,
        end,
      });
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      flush();
      blocks.push({ kind: "bullet", level: 0, start: start + bullet[0].length, end });
      continue;
    }
    const ordered = ORDERED.exec(line);
    if (ordered) {
      flush();
      blocks.push({
        kind: "ordered",
        level: Number(ordered[1] ?? "0"),
        start: start + ordered[0].length,
        end,
      });
      continue;
    }
    if (paragraphStart === -1) paragraphStart = start;
    paragraphEnd = end;
  }
  flush();
  return blocks;
}

export interface Placement {
  claimId: string;
  start: number;
  end: number;
}

export interface AnswerLayout {
  body: string;
  notice: string;
  blocks: Block[];
  /** Where each locatable claim sits in `body`, in document order. */
  placements: Placement[];
  /** Grounding rows synthesis never gave a span. Nothing to locate. */
  spanless: GroundingRow[];
  /** Grounding rows whose span is real but no longer in the prose — another
   *  claim's excision removed the sentence they shared. */
  unlocated: GroundingRow[];
}

/**
 * Locate every grounding row in the prose.
 *
 * Retracted rows are not passed here and must not be: their spans were cut out
 * of the body on purpose, so searching for them would report the system's
 * correct behaviour as a fault.
 */
export function layoutAnswer(answer: string, grounding: GroundingRow[]): AnswerLayout {
  const { body, notice } = splitAnswer(answer);
  const placements: Placement[] = [];
  const spanless: GroundingRow[] = [];
  const unlocated: GroundingRow[] = [];

  for (const row of grounding) {
    if (!row.answer_span) {
      spanless.push(row);
      continue;
    }
    const at = body.indexOf(row.answer_span);
    if (at === -1) {
      unlocated.push(row);
      continue;
    }
    placements.push({ claimId: row.claim_id, start: at, end: at + row.answer_span.length });
  }
  // Document order, and at a shared start the wider claim first, so a nested
  // pair reads outer-to-inner rather than in whichever order synthesis
  // happened to emit them.
  placements.sort((a, b) => a.start - b.start || b.end - a.end);
  return { body, notice, blocks: parseBlocks(body), placements, spanless, unlocated };
}

export type AtomKind = "text" | "strong" | "citation";

export interface Atom {
  start: number;
  end: number;
  text: string;
  kind: AtomKind;
  /** Every claim covering this atom. More than one when claims overlap. */
  claimIds: string[];
}

/** `**bold**` or `[0001628280-25-003063:item-7-p014]`. Both are matched in one
 *  pass so a citation inside a bold run cannot be double-counted; 128 of the
 *  211 recorded spans contain a citation, so the two genuinely do nest. */
const INLINE = /\*\*([\s\S]+?)\*\*|\[([^\]\n]+)\]/g;

interface Span {
  start: number;
  end: number;
  kind: AtomKind;
}

/** The bold asterisks and citation brackets themselves, which are consumed. */
function inlineSpans(body: string, from: number, to: number): { keep: Span[]; drop: Span[] } {
  const keep: Span[] = [];
  const drop: Span[] = [];
  const slice = body.slice(from, to);
  INLINE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = INLINE.exec(slice)) !== null) {
    const start = from + match.index;
    const end = start + match[0].length;
    const bold = match[1] !== undefined;
    const pad = bold ? 2 : 1;
    drop.push({ start, end: start + pad, kind: "text" });
    keep.push({ start: start + pad, end: end - pad, kind: bold ? "strong" : "citation" });
    drop.push({ start: end - pad, end, kind: "text" });
  }
  return { keep, drop };
}

/**
 * Cut one block into atoms of uniform style and uniform claim membership.
 *
 * A sweep rather than nested wrapping, because inline formatting and claim
 * spans are independent range sets over the same string and either can
 * straddle the other. Every boundary from both goes into one sorted list and
 * the pieces between consecutive boundaries are, by construction, uniform.
 */
export function atomsFor(block: Block, layout: AnswerLayout): Atom[] {
  const { body, placements } = layout;
  const { keep, drop } = inlineSpans(body, block.start, block.end);

  const cuts = new Set<number>([block.start, block.end]);
  const add = (value: number): void => {
    if (value > block.start && value < block.end) cuts.add(value);
  };
  for (const span of [...keep, ...drop]) {
    add(span.start);
    add(span.end);
  }
  for (const placement of placements) {
    add(placement.start);
    add(placement.end);
  }

  const edges = [...cuts].sort((a, b) => a - b);
  const atoms: Atom[] = [];
  for (let i = 0; i < edges.length - 1; i += 1) {
    const start = edges[i] ?? 0;
    const end = edges[i + 1] ?? 0;
    if (start >= end) continue;
    if (drop.some((span) => start >= span.start && end <= span.end)) continue;
    const styled = keep.find((span) => start >= span.start && end <= span.end);
    atoms.push({
      start,
      end,
      text: body.slice(start, end),
      kind: styled?.kind ?? "text",
      claimIds: placements
        .filter((placement) => start >= placement.start && end <= placement.end)
        .map((placement) => placement.claimId),
    });
  }
  return atoms;
}

/** The claims a block makes, in the order they appear — the margin ticks. */
export function claimsInBlock(block: Block, layout: AnswerLayout): string[] {
  return layout.placements
    .filter((placement) => placement.start < block.end && placement.end > block.start)
    .map((placement) => placement.claimId);
}
