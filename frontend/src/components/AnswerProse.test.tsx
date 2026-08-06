import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RETRACTED_HEADER } from "../lib/answer";
import { LANDING_RUN, SPANLESS_RUN, recordedRun } from "../test/fixtures";
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
    expect(marks.length).toBe(result.grounding.length);
    for (const mark of marks) {
      expect(mark).toHaveAttribute("data-tone", "verified");
    }
  });

  it("does not print the retraction section the answer appends to itself", () => {
    const payload = recordedRun("32d18b0d4184");
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
      .find((element) => element.textContent?.includes("The FY 2025 decline"));
    expect(mark).toBeDefined();
    await user.hover(mark!);

    const popover = await screen.findByRole("dialog", { name: /claim verification/i });
    expect(within(popover).getByText(/XBRL reconciliation/)).toBeInTheDocument();
    expect(within(popover).getByText(numeric!.sources[0]!.accession_no)).toBeInTheDocument();
    expect(within(popover).getByText(/TSLA 10-K/)).toBeInTheDocument();
  });

  it("says so plainly when a run recorded no spans, rather than guessing", async () => {
    const { result } = renderRun(SPANLESS_RUN);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(
      screen.getByText(new RegExp(`${result.grounding.length} claims could not be marked`)),
    ).toBeInTheDocument();
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
