import { describe, expect, it } from "vitest";

import { LANDING_RUN, recordedRun, recordedRuns } from "../test/fixtures";
import type { GroundingRow } from "../types/api";
import {
  RETRACTED_HEADER,
  atomsFor,
  claimsInBlock,
  layoutAnswer,
  parseBlocks,
  splitAnswer,
} from "./answer";

function row(overrides: Partial<GroundingRow> & Pick<GroundingRow, "claim_id">): GroundingRow {
  return {
    text: "a claim",
    claim_type: "qualitative",
    status: "grounded",
    verdict: "grounded",
    method: "semantic",
    supporting_span: null,
    answer_span: "",
    feedback: null,
    sources: [],
    ...overrides,
  };
}

/** What a reader actually sees for one claim: the atoms carrying it, in order,
 *  with bold markers and citation brackets already consumed. */
function highlighted(claimId: string, answer: string, grounding: GroundingRow[]): string {
  const layout = layoutAnswer(answer, grounding);
  return layout.blocks
    .flatMap((block) => atomsFor(block, layout))
    .filter((atom) => atom.claimIds.includes(claimId))
    .map((atom) => atom.text)
    .join("");
}

describe("splitAnswer", () => {
  it("separates the retraction section the answer appends to itself", () => {
    const { body, notice } = splitAnswer(
      `Revenue rose.\n\n${RETRACTED_HEADER}\n\nThe following claims could not be verified:\n- A claim.`,
    );
    expect(body).toBe("Revenue rose.");
    expect(notice.startsWith(RETRACTED_HEADER)).toBe(true);
  });

  it("leaves an answer without the header alone", () => {
    expect(splitAnswer("Revenue rose.")).toEqual({ body: "Revenue rose.", notice: "" });
  });

  it("removes it from every recorded answer that carries one", () => {
    const carrying = recordedRuns().filter((p) => p.result.answer.includes(RETRACTED_HEADER));
    // The header is part of the answer contract, not an occasional artifact:
    // if this count reaches zero the split has stopped being exercised.
    expect(carrying.length).toBe(6);
    for (const payload of carrying) {
      expect(layoutAnswer(payload.result.answer, payload.result.grounding).body).not.toContain(
        RETRACTED_HEADER,
      );
    }
  });
});

describe("layoutAnswer", () => {
  it("locates every claim the recorded corpus can locate, and invents no others", () => {
    let placed = 0;
    let spanless = 0;
    let unlocated = 0;
    for (const payload of recordedRuns()) {
      const layout = layoutAnswer(payload.result.answer, payload.result.grounding);
      placed += layout.placements.length;
      spanless += layout.spanless.length;
      unlocated += layout.unlocated.length;
      // Every grounding row lands in exactly one bucket. A row that fell out
      // of all three would be a claim the reader is never told about.
      expect(
        layout.placements.length + layout.spanless.length + layout.unlocated.length,
      ).toBe(payload.result.grounding.length);
    }
    // The accepted 2026-10-10 baseline has 217 grounding rows: 210 `grounded`
    // and 7 `partial`. Three are spanless, all in g-002 (`716e05bc2d5c`). The
    // model omitted the optional `answer_span` field there (ADR 0046), so that
    // run's prose cannot be highlighted at all. The 08-13 corpus had none, and
    // the 08-01 corpus had six, likewise all in one run. Assert the count
    // rather than assume it: it moves with every baseline.
    expect(placed + spanless + unlocated).toBe(217);
    expect(placed).toBe(214);
    expect(spanless).toBe(3);
    // No *delivered* grounding row lost its sentence to another claim's
    // excision. That is not the same as the defect being extinct: ADR 0021
    // point 3 fired once in this very baseline, in g-030, and the graph
    // logged it. A claim it hits is dropped from the grounding
    // table rather than left in it, so it reaches the reader as a retraction
    // and never as an unmarkable row. This asserts the table the reader gets
    // is clean, not that the defect cannot happen.
    expect(unlocated).toBe(0);
  });

  it("colours exactly the sentence the verifier ruled on, across the whole corpus", () => {
    // The thesis of the product as an assertion: what is highlighted is the
    // claim's `answer_span` and nothing either side of it. Markers are the
    // only difference, because they are consumed on the way to the screen.
    //
    // A leading list marker is one of them, and it took the 08-13 corpus to
    // find out: four claims in `5d7e650d2379` quote `1. ` at the head of their
    // span, where the 08-01 corpus had none. The list renders its own number,
    // so the highlight starts at the item's content — the marker is consumed
    // exactly like `**` and the citation brackets, and normalising it here
    // keeps the assertion about the *sentence* rather than about Markdown.
    for (const payload of recordedRuns()) {
      const { grounding, answer } = payload.result;
      for (const claim of grounding) {
        if (!claim.answer_span) continue;
        const shown = highlighted(claim.claim_id, answer, grounding);
        const expected = claim.answer_span
          .replace(/^\s*(?:\d+\.|[-*])\s+/, "")
          .replace(/\*\*/g, "")
          .replace(/[[\]]/g, "");
        expect(shown).toBe(expected);
      }
    }
  });

  it("never hunts for a retracted claim, whose span excision removed on purpose", () => {
    const payload = recordedRun(LANDING_RUN);
    const [retracted] = payload.result.retracted;
    expect(retracted).toBeDefined();
    // Absent from the prose, and that is the system working.
    expect(payload.result.answer).not.toContain(retracted!.answer_span);
    const layout = layoutAnswer(payload.result.answer, payload.result.grounding);
    expect(layout.unlocated).toEqual([]);
    expect(layout.placements.map((p) => p.claimId)).not.toContain(retracted!.claim_id);
  });

  it("separates a claim with no span from one whose sentence was excised", () => {
    const answer = "Revenue rose. Margin fell.";
    const layout = layoutAnswer(answer, [
      row({ claim_id: "c1", answer_span: "Revenue rose." }),
      row({ claim_id: "c2", answer_span: "" }),
      row({ claim_id: "c3", answer_span: "Backlog grew." }),
    ]);
    expect(layout.placements.map((p) => p.claimId)).toEqual(["c1"]);
    expect(layout.spanless.map((r) => r.claim_id)).toEqual(["c2"]);
    expect(layout.unlocated.map((r) => r.claim_id)).toEqual(["c3"]);
  });

  it("reports a whole run whose prose cannot be highlighted", () => {
    // The 08-01 corpus contained such a run and the 08-13 one does not, so
    // this is now built rather than fetched. Keeping it matters more than the
    // fixture did: the view still has a branch for the case, synthesis can
    // still emit a draft with no spans, and a baseline that reintroduces one
    // must not be the first time the path is exercised.
    const answer = "Revenue rose. Margin fell.";
    const layout = layoutAnswer(answer, [
      row({ claim_id: "c1", answer_span: "" }),
      row({ claim_id: "c2", answer_span: "" }),
    ]);
    expect(layout.placements).toEqual([]);
    expect(layout.spanless.map((r) => r.claim_id)).toEqual(["c1", "c2"]);
  });
});

describe("atomsFor", () => {
  it("gives one atom both claims when two claims share a sentence", () => {
    // Overlap is not hypothetical: the recorded corpus has ten overlapping
    // pairs. Wrapping each claim in turn would nest tags wrongly; the sweep
    // returns atoms that know every claim covering them.
    const answer = "Revenue rose 12% and margin fell 3 points.";
    const layout = layoutAnswer(answer, [
      row({ claim_id: "c1", answer_span: "Revenue rose 12% and margin fell 3 points." }),
      row({ claim_id: "c2", answer_span: "Revenue rose 12%" }),
    ]);
    const atoms = atomsFor(layout.blocks[0]!, layout);
    expect(atoms.map((a) => [a.text, a.claimIds])).toEqual([
      ["Revenue rose 12%", ["c1", "c2"]],
      [" and margin fell 3 points.", ["c1"]],
    ]);
  });

  it("consumes bold markers and citation brackets", () => {
    const layout = layoutAnswer("**Services:** revenue was $109,158 million [0000320193-25-000079]", []);
    const atoms = atomsFor(layout.blocks[0]!, layout);
    expect(atoms.map((a) => [a.kind, a.text])).toEqual([
      ["strong", "Services:"],
      ["text", " revenue was $109,158 million "],
      ["citation", "0000320193-25-000079"],
    ]);
  });

  it("splits a bold run that a claim span only partly covers", () => {
    const layout = layoutAnswer("**Revenue rose and margin fell.**", [
      row({ claim_id: "c1", answer_span: "Revenue rose" }),
    ]);
    const atoms = atomsFor(layout.blocks[0]!, layout);
    expect(atoms.map((a) => [a.kind, a.text, a.claimIds])).toEqual([
      ["strong", "Revenue rose", ["c1"]],
      ["strong", " and margin fell.", []],
    ]);
  });

  it("reconstructs every recorded body exactly, minus the markers", () => {
    // The invariant that stops a sweep bug from silently dropping prose: a
    // reader must see the whole answer, not the parts that happened to fall
    // between boundaries.
    for (const payload of recordedRuns()) {
      const layout = layoutAnswer(payload.result.answer, payload.result.grounding);
      for (const block of layout.blocks) {
        const atoms = atomsFor(block, layout);
        const content = layout.body.slice(block.start, block.end);
        expect(atoms.map((a) => a.text).join("")).toBe(
          content.replace(/\*\*/g, "").replace(/\[([^\]\n]+)\]/g, "$1"),
        );
        for (let i = 1; i < atoms.length; i += 1) {
          expect(atoms[i]!.start).toBeGreaterThanOrEqual(atoms[i - 1]!.end);
        }
      }
    }
  });
});

describe("parseBlocks", () => {
  it("reads the Markdown subset synthesis actually emits", () => {
    const body = "Intro line\nstill the same paragraph\n\n## Heading\n\n- a bullet\n2. an item";
    expect(parseBlocks(body).map((b) => [b.kind, b.level, body.slice(b.start, b.end)])).toEqual([
      ["paragraph", 0, "Intro line\nstill the same paragraph"],
      ["heading", 2, "Heading"],
      ["bullet", 0, "a bullet"],
      ["ordered", 2, "an item"],
    ]);
  });

  it("excludes the list marker so a span still lines up with the text", () => {
    const layout = layoutAnswer("- Revenue rose 12%.", [
      row({ claim_id: "c1", answer_span: "Revenue rose 12%." }),
    ]);
    expect(claimsInBlock(layout.blocks[0]!, layout)).toEqual(["c1"]);
    expect(highlighted("c1", "- Revenue rose 12%.", [
      row({ claim_id: "c1", answer_span: "Revenue rose 12%." }),
    ])).toBe("Revenue rose 12%.");
  });
});

describe("claimsInBlock", () => {
  it("lists a block's claims in document order, for the margin ticks", () => {
    const answer = "First claim here.\n\nSecond claim here. Third claim here.";
    const layout = layoutAnswer(answer, [
      row({ claim_id: "c3", answer_span: "Third claim here." }),
      row({ claim_id: "c1", answer_span: "First claim here." }),
      row({ claim_id: "c2", answer_span: "Second claim here." }),
    ]);
    expect(claimsInBlock(layout.blocks[0]!, layout)).toEqual(["c1"]);
    expect(claimsInBlock(layout.blocks[1]!, layout)).toEqual(["c2", "c3"]);
  });
});
