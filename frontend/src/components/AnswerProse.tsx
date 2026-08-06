import { useMemo, useState } from "react";

import type { Atom, Block } from "../lib/answer";
import { atomsFor, claimsInBlock, layoutAnswer } from "../lib/answer";
import { STATUS_META, severest } from "../lib/status";
import type { GroundingRow } from "../types/api";
import { ClaimPopover } from "./ClaimPopover";
import { Tick } from "./Tick";

interface AnswerProseProps {
  answer: string;
  grounding: GroundingRow[];
  /** The claim currently under the reader's attention, from either this view
   *  or the table below it. Shared so the two stay in step. */
  lit: string | null;
  onLit: (claimId: string | null) => void;
}

interface Group {
  claimIds: string[];
  atoms: Atom[];
}

/** Consecutive atoms covered by the same claims become one element, so a
 *  sentence is a single tab stop rather than one per bold run inside it. */
function group(atoms: Atom[]): Group[] {
  const groups: Group[] = [];
  for (const atom of atoms) {
    const key = atom.claimIds.join("|");
    const last = groups[groups.length - 1];
    if (last && last.claimIds.join("|") === key) last.atoms.push(atom);
    else groups.push({ claimIds: atom.claimIds, atoms: [atom] });
  }
  return groups;
}

/** `0001628280-24-002390:part-i--item-7-p014` → `24-002390`. The filer prefix
 *  is the same ten digits for every filing by one company, and the chunk
 *  suffix is an internal locator; what is left still identifies the filing to
 *  anyone who wants to look it up, and it fits in a footnote. */
function shortCitation(raw: string): string {
  const accession = /(\d{10})-(\d{2})-(\d{6})/.exec(raw);
  return accession ? `${accession[2]}-${accession[3]}` : raw.slice(0, 12);
}

function AtomText({ atom }: { atom: Atom }) {
  if (atom.kind === "strong") return <strong className="font-semibold">{atom.text}</strong>;
  if (atom.kind === "citation") {
    return (
      <span className="citation" title={atom.text}>
        {shortCitation(atom.text)}
      </span>
    );
  }
  return <>{atom.text}</>;
}

/**
 * The answer, with every claim in it marked by how it fared.
 *
 * This is the view the whole project exists to make possible: the prose reads
 * as prose, and the verification is *in* it rather than in a report beside it.
 * Two rules keep it honest. A sentence is only marked when the payload says
 * where it is — nothing here guesses a position from word overlap, because a
 * colour on a sentence no verifier ruled on is the one error this surface
 * must never make. And a sentence carrying several claims takes the worst of
 * their verdicts, so a shared sentence cannot hide a bad claim behind a good
 * one.
 */
export function AnswerProse({ answer, grounding, lit, onLit }: AnswerProseProps) {
  const layout = useMemo(() => layoutAnswer(answer, grounding), [answer, grounding]);
  const rows = useMemo(
    () => new Map(grounding.map((row) => [row.claim_id, row])),
    [grounding],
  );
  const [popover, setPopover] = useState<{ claimIds: string[]; rect: DOMRect } | null>(null);
  const [pinned, setPinned] = useState(false);

  const statusOf = (claimIds: string[]) =>
    severest(claimIds.map((id) => rows.get(id)?.status ?? "pending"));

  const show = (claimIds: string[], target: HTMLElement) => {
    setPopover({ claimIds, rect: target.getBoundingClientRect() });
    onLit(claimIds[0] ?? null);
  };

  const hide = () => {
    if (pinned) return;
    setPopover(null);
    onLit(null);
  };

  const dismiss = () => {
    setPinned(false);
    setPopover(null);
    onLit(null);
  };

  return (
    <div className="relative">
      <article className="prose-answer text-ink">
        {layout.blocks.map((block, index) => (
          <BlockRow
            key={`${block.start}-${index}`}
            block={block}
            groups={group(atomsFor(block, layout))}
            ticks={claimsInBlock(block, layout)}
            statusOf={statusOf}
            lit={lit}
            onLit={onLit}
            onShow={show}
            onHide={hide}
            onPin={() => setPinned(true)}
          />
        ))}
      </article>

      {layout.spanless.length > 0 || layout.unlocated.length > 0 ? (
        <p className="mt-6 border-l-2 border-slate/40 bg-rule/25 px-4 py-3 text-[13px] leading-relaxed text-slate">
          {layout.spanless.length > 0 ? (
            <>
              <strong className="font-semibold text-ink">
                {layout.spanless.length} claim{layout.spanless.length > 1 ? "s" : ""} could not be
                marked in the text above.
              </strong>{" "}
              The run recorded the verdict but not which sentence the claim came from, so marking
              one here would be a guess. Every claim is in the table below with its verdict and
              sources.
            </>
          ) : null}
          {layout.unlocated.length > 0 ? (
            <>
              {layout.spanless.length > 0 ? " " : null}
              <strong className="font-semibold text-ink">
                {layout.unlocated.length} claim{layout.unlocated.length > 1 ? "s" : ""} named a
                sentence that is no longer in the answer.
              </strong>{" "}
              Retracting another claim removed the sentence they shared.
            </>
          ) : null}
        </p>
      ) : null}

      {popover ? (
        <ClaimPopover
          rows={popover.claimIds.map((id) => rows.get(id)).filter((row): row is GroundingRow => !!row)}
          anchor={popover.rect}
          onDismiss={dismiss}
        />
      ) : null}
    </div>
  );
}

interface BlockRowProps {
  block: Block;
  groups: Group[];
  ticks: string[];
  statusOf: (claimIds: string[]) => GroundingRow["status"];
  lit: string | null;
  onLit: (claimId: string | null) => void;
  onShow: (claimIds: string[], target: HTMLElement) => void;
  onHide: () => void;
  onPin: () => void;
}

function BlockRow({
  block,
  groups,
  ticks,
  statusOf,
  lit,
  onLit,
  onShow,
  onHide,
  onPin,
}: BlockRowProps) {
  const content = groups.map((item, index) => {
    if (item.claimIds.length === 0) {
      return (
        <span key={index}>
          {item.atoms.map((atom, i) => (
            <AtomText key={i} atom={atom} />
          ))}
        </span>
      );
    }
    const status = statusOf(item.claimIds);
    return (
      <button
        key={index}
        type="button"
        className="claim-mark text-left"
        data-tone={STATUS_META[status].tone}
        data-lit={item.claimIds.includes(lit ?? "")}
        aria-label={`${STATUS_META[status].label}. ${item.claimIds.length} claim${
          item.claimIds.length > 1 ? "s" : ""
        } on this sentence.`}
        onMouseEnter={(event) => onShow(item.claimIds, event.currentTarget)}
        onMouseLeave={onHide}
        onFocus={(event) => onShow(item.claimIds, event.currentTarget)}
        onBlur={onHide}
        onClick={onPin}
      >
        {item.atoms.map((atom, i) => (
          <AtomText key={i} atom={atom} />
        ))}
      </button>
    );
  });

  return (
    <div className="grid grid-cols-[0.75rem_1fr] gap-x-2.5 md:grid-cols-[1rem_1fr] md:gap-x-3">
      <div className="flex flex-col items-start gap-1 pt-[0.66em]">
        {ticks.map((claimId, index) => (
          <Tick
            key={claimId}
            status={statusOf([claimId])}
            lit={lit === claimId}
            order={index}
            onEnter={() => onLit(claimId)}
            onLeave={() => onLit(null)}
          />
        ))}
      </div>
      <BlockBody block={block}>{content}</BlockBody>
    </div>
  );
}

function BlockBody({ block, children }: { block: Block; children: React.ReactNode }) {
  if (block.kind === "heading") {
    return <h3 className="mt-6 mb-1 font-sans text-base font-semibold text-ink">{children}</h3>;
  }
  if (block.kind === "bullet") {
    return (
      <p className="mt-1 flex gap-2">
        <span aria-hidden className="select-none text-slate">
          —
        </span>
        <span>{children}</span>
      </p>
    );
  }
  if (block.kind === "ordered") {
    return (
      <p className="mt-1 flex gap-2">
        <span aria-hidden className="select-none font-mono text-[0.85em] text-slate">
          {block.level}.
        </span>
        <span>{children}</span>
      </p>
    );
  }
  return <p className="mt-4 first:mt-0">{children}</p>;
}
