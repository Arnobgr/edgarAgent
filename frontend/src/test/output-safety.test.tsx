import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnswerProse } from "../components/AnswerProse";

/**
 * The output channel, asserted rather than assumed (Phase 6, Step 2).
 *
 * The audit that opened this phase found nothing to fix: there is no HTML
 * sink in this SPA, and no anchor in it points anywhere but a hash route. But
 * both properties hold **by absence** — the renderer emits no external link
 * because nobody has added one yet, not because anything filters one. A
 * property that is true by absence breaks silently, and the page that would
 * carry the resulting phishing link is the page whose whole promise is that
 * its contents were verified.
 *
 * So these tests fail the day someone renders a source link from an
 * accession. That is the point: the failure is the design review, and the fix
 * is a constructed `sec.gov` path rather than a URL taken from a payload.
 */

// `import.meta.glob` rather than `node:fs`: the source scan needs no
// `@types/node` and no dependency this package does not already have, which
// matters under `frontend/.npmrc` — every added package is a supply-chain
// decision, and this one would have been taken to read four files.
//
// Ships-to-the-browser sources only. The suites and fixtures are excluded
// because this very file names every pattern it forbids.
const SOURCES = Object.entries(
  import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }),
)
  .filter(([path]) => !/\.test\.tsx?$/.test(path) && !path.startsWith("../test/"))
  .map(([path, text]) => ({ path, text: text as string }));

// A scan that found no files would pass every assertion below silently.
if (SOURCES.length === 0) throw new Error("the source glob matched nothing");

// Answer text carrying every shape an injected link could take: markdown link
// syntax, a bare URL, a `javascript:` scheme, and raw HTML.
const HOSTILE_ANSWER = [
  "Revenue rose sharply [c1].",
  "",
  "See [the filing](https://evil.example/steal?token=abc) for detail.",
  "Visit https://evil.example/phish now.",
  'Click <a href="https://evil.example">here</a> to confirm.',
  "[javascript:alert(document.cookie)]",
  "<img src=x onerror=alert(1)>",
].join("\n");

describe("output channel", () => {
  it("renders no anchor and no href, whatever the answer contains", () => {
    const { container } = render(
      <AnswerProse answer={HOSTILE_ANSWER} grounding={[]} lit={null} onLit={() => undefined} />,
    );

    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.querySelectorAll("[href]")).toHaveLength(0);
    expect(container.querySelectorAll("img, iframe, script, object, embed")).toHaveLength(0);
  });

  it("renders raw HTML in an answer as text, proving there is no sink", () => {
    const { container } = render(
      <AnswerProse answer={HOSTILE_ANSWER} grounding={[]} lit={null} onLit={() => undefined} />,
    );

    // The tag arrives at the reader as the characters `<a href=...`, which is
    // only possible if it was never parsed as markup.
    expect(container.textContent).toContain('<a href="https://evil.example">');
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("has no HTML sink anywhere in the SPA", () => {
    const offenders = SOURCES.filter(
      (file) =>
        file.text.includes("dangerouslySetInnerHTML") ||
        file.text.includes("innerHTML") ||
        file.text.includes("document.write"),
    ).map((file) => file.path);

    expect(offenders).toEqual([]);
  });

  it("points every anchor at an internal hash route", () => {
    // Matches `href="..."` and `href={...}`. A template literal counts as
    // internal only when it *starts* with `#`, so `` href={`${base}/x`} ``
    // fails here and has to be argued for.
    const hrefs = SOURCES.flatMap((file) =>
      [...file.text.matchAll(/href=(?:"([^"]*)"|\{`([^`]*)`\}|\{"([^"]*)"\})/g)].map((match) => ({
        path: file.path,
        value: match[1] ?? match[2] ?? match[3] ?? "",
      })),
    );

    expect(hrefs.length).toBeGreaterThan(0);
    const external = hrefs.filter((href) => !href.value.startsWith("#"));
    expect(external).toEqual([]);
  });
});
