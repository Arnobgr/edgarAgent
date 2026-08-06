import type { ClaimStatus } from "../types/api";
import { STATUS_META } from "../lib/status";

interface TickProps {
  status: ClaimStatus;
  lit: boolean;
  /** Index in a strip, for the staggered draw-in. */
  order?: number;
  onEnter?: () => void;
  onLeave?: () => void;
  onSelect?: () => void;
  title?: string;
}

/**
 * One claim, at its smallest.
 *
 * The same mark appears in the strip under the question, in the margin beside
 * the paragraph that makes the claim, and on the claim's table row — so the
 * reader learns one object and then recognises it in three places. Lighting
 * any one of them lights the others, which is how the strip becomes a map of
 * the answer rather than a decoration on it.
 */
export function Tick({ status, lit, order = 0, onEnter, onLeave, onSelect, title }: TickProps) {
  const meta = STATUS_META[status];
  const label = title ?? meta.label;
  const interactive = Boolean(onSelect);
  return (
    <span
      className="tick"
      data-tone={meta.tone}
      data-lit={lit}
      style={{ animationDelay: `${Math.min(order, 24) * 25}ms` }}
      title={label}
      aria-hidden={!interactive}
      {...(interactive
        ? {
            role: "button",
            tabIndex: 0,
            "aria-label": label,
            onClick: onSelect,
            onKeyDown: (event: React.KeyboardEvent) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect?.();
              }
            },
          }
        : {})}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
    />
  );
}
