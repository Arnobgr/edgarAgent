import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RETRACTED_HEADER } from "../lib/answer";
import { LANDING_RUN, recordedRun } from "../test/fixtures";
import { AnswerProse } from "./AnswerProse";

function renderRun(jobId: string) {
  const { result } = recordedRun(jobId);
  return {
    result,
    ...render(
      <AnswerProse
        answer={result.answer}
        grounding={result.grounding}
        lit={null}
        onLit={() => undefined}
      />,
    ),
  };
}

describe("AnswerProse", () => {
  it("marks one element per claim run, tinted by verdict", () => {
    const { result } = renderRun(LANDING_RUN);
    const marks = screen.getAllByRole("button");
    // One mark per quoted sentence, not per claim: two claims of the landing
    // run quote the identical sentence, and the sweep gives that sentence one
    // atom carrying both (see `atomsFor`).
    expect(marks.length).toBe(new Set(result.grounding.map((row) => row.answer_span)).size);
    for (const mark of marks) {
      expect(mark).toHaveAttribute("data-tone", "verified");
    }
  });

  it("does not print the retraction section the answer appends to itself", () => {
    // The landing run carries the header, so this needs no second id to go
    // stale when `recorded/` is regenerated (ADR 0043).
    const payload = recordedRun(LANDING_RUN);
    expect(payload.result.answer).toContain(RETRACTED_HEADER);
    render(
      <AnswerProse
        answer={payload.result.answer}
        grounding={payload.result.grounding}
        lit={null}
        onLit={() => undefined}
      />,
    );
    // The retracted claim is rendered from the payload, by RetractedClaims,
    // with a verdict and a source. Leaving the appended prose in as well
    // would show it twice, once without either.
    expect(screen.queryByText(/Retracted claims/i)).toBeNull();
    expect(screen.queryByText(/could not be verified against their cited sources/i)).toBeNull();
  });

  it("shows the source behind a claim when the reader hovers it", async () => {
    const user = userEvent.setup();
    const { result } = renderRun(LANDING_RUN);
    const numeric = result.grounding.find((row) => row.method === "numeric_reconciliation");
    expect(numeric).toBeDefined();

    const mark = screen
      .getAllByRole("button")
      .find((element) => element.textContent?.includes("AAPL reported total assets of"));
    expect(mark).toBeDefined();
    await user.hover(mark!);

    const popover = await screen.findByRole("dialog", { name: /claim verification/i });
    expect(within(popover).getByText(/XBRL reconciliation/)).toBeInTheDocument();
    expect(within(popover).getByText(numeric!.sources[0]!.accession_no)).toBeInTheDocument();
    expect(within(popover).getByText(/AAPL 10-K/)).toBeInTheDocument();
  });

  it("says so plainly when a run recorded no spans, rather than guessing", async () => {
    // Built rather than fetched since ADR 0043: the 08-13 corpus has no run
    // whose prose cannot be marked. The branch is still in the view, so the
    // test stays and stops depending on a corpus that may or may not supply
    // a subject.
    render(
      <AnswerProse
        answer="Apple (AAPL) carries the largest total assets of the three."
        grounding={[
          {
            claim_id: "c1",
            text: "Apple carries the largest total assets",
            claim_type: "numeric",
            status: "grounded",
            verdict: "grounded",
            method: "numeric_reconciliation",
            supporting_span: "Total assets $364,980",
            answer_span: "",
            feedback: null,
            sources: [],
          },
          {
            claim_id: "c2",
            text: "of the three companies compared",
            claim_type: "qualitative",
            status: "grounded",
            verdict: "grounded",
            method: "semantic",
            supporting_span: "the three issuers",
            answer_span: "",
            feedback: null,
            sources: [],
          },
        ]}
        lit={null}
        onLit={() => undefined}
      />,
    );
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText(/2 claims could not be marked/)).toBeInTheDocument();
    // The prose is still shown in full — only the marking is withheld.
    expect(screen.getByText(/Apple \(AAPL\) carries the largest total assets/)).toBeInTheDocument();
  });

  it("gives a sentence carrying two claims the more serious of their verdicts", async () => {
    const user = userEvent.setup();
    render(
      <AnswerProse
        answer="Revenue rose 12% and margin fell 3 points."
        grounding={[
          {
            claim_id: "c1",
            text: "Revenue rose 12%",
            claim_type: "numeric",
            status: "grounded",
            verdict: "grounded",
            method: "numeric_reconciliation",
            supporting_span: "Revenue was $12",
            answer_span: "Revenue rose 12% and margin fell 3 points.",
            feedback: null,
            sources: [],
          },
          {
            claim_id: "c2",
            text: "margin fell 3 points",
            claim_type: "numeric",
            status: "partial",
            verdict: "partial",
            method: "numeric_reconciliation",
            supporting_span: null,
            answer_span: "margin fell 3 points.",
            feedback: "Only the direction is supported.",
            sources: [],
          },
        ]}
        lit={null}
        onLit={() => undefined}
      />,
    );

    const marks = screen.getAllByRole("button");
    const shared = marks.find((m) => m.textContent === "margin fell 3 points.");
    expect(shared).toBeDefined();
    // `partial` beats `grounded` on a shared sentence: a softer verdict here
    // would hide the harder one behind it.
    expect(shared).toHaveAttribute("data-tone", "partial");

    await user.hover(shared!);
    const popover = await screen.findByRole("dialog", { name: /claim verification/i });
    expect(within(popover).getByText("Revenue rose 12%")).toBeInTheDocument();
    expect(within(popover).getByText("margin fell 3 points")).toBeInTheDocument();
  });
});
