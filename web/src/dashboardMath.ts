// Pure helpers behind the Dashboard cards, kept out of the page for testing.
import type { RevolutTransaction, UpcomingDividend } from "./types";

/** Net spending (expenses minus refunds) of `month` through day-of-month `day`
 * inclusive. Compares a month in progress with the same stretch of the last
 * one; a total-vs-total delta would always read "much lower" early in the
 * month. */
export function spentThroughDay(txs: RevolutTransaction[], month: string, day: number): number {
  let spent = 0;
  for (const t of txs) {
    if (t.kind !== "expense" && t.kind !== "refund") continue;
    if (!t.date.startsWith(month) || Number(t.date.slice(8, 10)) > day) continue;
    spent -= t.amountEur;
  }
  return spent;
}

/** Upcoming payments with a date, soonest first (the export's order isn't
 * guaranteed), and the sum of the ones that carry an EUR estimate. */
export function upcomingSummary(upcoming: UpcomingDividend[] | undefined): {
  rows: UpcomingDividend[];
  totalEur: number;
  hasUnknown: boolean;
} {
  const rows = (upcoming ?? []).filter((u) => u.payDate).sort((a, b) => a.payDate!.localeCompare(b.payDate!));
  return {
    rows,
    totalEur: rows.reduce((s, u) => s + (u.amountEur ?? 0), 0),
    hasUnknown: rows.some((u) => u.amountEur === null),
  };
}
