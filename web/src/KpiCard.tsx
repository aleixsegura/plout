import type { ReactNode } from "react";

const CARD = "flex flex-col gap-1 card pt-3 pb-1";
// Dense strip: same tile, tightened so a row of summary figures reads as one
// row instead of taking a screenful.
const CARD_DENSE = "flex flex-col gap-0.5 card pt-2";
const LABEL = "text-xs font-semibold uppercase tracking-[0.03em] text-muted";
const LABEL_DENSE = "text-[0.65rem] font-semibold uppercase tracking-[0.04em] text-muted";
const VALUE = "text-[1.35rem] font-semibold leading-tight tabular-nums";
const VALUE_DENSE = "text-[1.05rem] font-semibold leading-tight tabular-nums";
export const SUB = "text-[0.85rem] tabular-nums";
export const SUB_DENSE = "text-[0.7rem] tabular-nums";

export interface BreakdownRow {
  label: string;
  value: string;
  /** Text color for the value (e.g. gain/loss); defaults to primary. */
  valueClass?: string;
}

/** A KPI tile. When `breakdown` is given, the card becomes hoverable/focusable
 * and reveals a per-source popover — the shared pattern for the cash and the
 * DEGIRO-vs-Revolut P/L splits. */
export default function KpiCard({
  label,
  value,
  valueClass,
  sub,
  breakdown,
  ariaLabel,
  dense = false,
}: {
  label: string;
  value: string;
  valueClass?: string;
  sub?: ReactNode;
  breakdown?: BreakdownRow[];
  ariaLabel?: string;
  /** Strip variant: tighter tile for pages with many indicators. */
  dense?: boolean;
}) {
  const card = dense ? CARD_DENSE : CARD;
  const labelClass = dense ? LABEL_DENSE : LABEL;
  const valueSize = dense ? VALUE_DENSE : VALUE;

  if (!breakdown || breakdown.length === 0) {
    return (
      <div className={card}>
        <span className={labelClass}>{label}</span>
        <span className={`${valueSize} ${valueClass ?? ""}`}>{value}</span>
        {sub}
      </div>
    );
  }
  return (
    <div
      className={`${card} group relative cursor-default focus:outline-none focus-visible:ring-2 focus-visible:ring-focus`}
      tabIndex={0}
      aria-label={ariaLabel}
    >
      <span className={labelClass}>{label}</span>
      <span className={`${valueSize} ${valueClass ?? ""}`}>{value}</span>
      {sub}
      <div
        className="pointer-events-none absolute left-0 top-full z-20 mt-2 min-w-[11rem] rounded-xl border border-hairline-strong bg-elevated px-3.5 py-3 opacity-0 shadow-pop transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
        role="tooltip"
      >
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {breakdown.map((r) => (
            <li key={r.label} className="flex items-baseline justify-between gap-4">
              <span className="whitespace-nowrap text-xs text-secondary">{r.label}</span>
              <span className={`text-xs font-medium tabular-nums ${r.valueClass ?? "text-primary"}`}>
                {r.value}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
