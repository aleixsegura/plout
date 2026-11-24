import type { Position, RevolutPosition } from "./types";

/** One row's EUR figures, from its DEGIRO position or (crypto) Revolut one. */
export interface RowFigures {
  value: number | null;
  pl: number | null;
  cost: number | null;
  today: number | null;
}

export function rowFigures(p: Position | undefined, rp: RevolutPosition | undefined): RowFigures {
  if (p) return { value: p.valueEur, pl: p.plEur, cost: p.costEur, today: p.todayPlEur };
  if (rp)
    return {
      value: rp.valueEur,
      pl: rp.plEur ?? null,
      cost: rp.costEur ?? null,
      today: rp.todayPlEur ?? null,
    };
  return { value: null, pl: null, cost: null, today: null };
}

export interface HoldingsTotals {
  value: number;
  /** Sum over rows that carry a cost basis only (crypto without one is out). */
  pl: number;
  cost: number;
  today: number;
  plPct: number | null;
}

/** Column totals for the table footer. P/L and its % cover only the rows with a
 * cost basis, so a basis-less coin doesn't read as a 100% gain. */
export function holdingsTotals(rows: RowFigures[]): HoldingsTotals {
  let value = 0;
  let pl = 0;
  let cost = 0;
  let today = 0;
  for (const r of rows) {
    value += r.value ?? 0;
    today += r.today ?? 0;
    if (r.pl !== null && r.cost !== null) {
      pl += r.pl;
      cost += r.cost;
    }
  }
  return { value, pl, cost, today, plPct: cost > 0 ? (pl / cost) * 100 : null };
}
