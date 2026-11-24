// Pure helpers over revolut_transactions.json (types.ts: RevolutTransactionsFile).
// No React here so the Dashboard, the Transactions page and any test can share
// one definition of "this month", "by category" and "which colour".
import type { RevolutTransaction, RevolutTransactionsFile, TransactionMonth } from "./types";
import type { Translations } from "./i18n/en";

/** Fixed category order: a category keeps its colour whatever the month shows
 * (identity follows the entity, never its rank). Anything Revolut adds later
 * lands after these, in first-seen order, on the overflow colours. */
export const CATEGORY_ORDER = [
  "groceries",
  "restaurants",
  "shopping",
  "transport",
  "utilities",
  "services",
  "health",
  "entertainment",
  "transfers",
  "travel",
  "cash",
  "general",
] as const;

const CHART_SLOTS = 10;

/** CSS colour for a category by its rank in `ranking` (largest first), so the
 * biggest categories get neighbouring tones of the chart gradient. Categories
 * missing from the ranking, past the ten slots, or "other" are neutral. */
export function categoryColorVar(category: string, ranking: string[]): string {
  const slot = ranking.indexOf(category);
  if (slot < 0 || slot >= CHART_SLOTS || category === "other") return "var(--chart-other)";
  return `var(--chart-${slot + 1})`;
}

export function categoryLabel(t: Translations, category: string): string {
  const labels = t.transactions.categories as Record<string, string>;
  return labels[category] ?? category.replace(/_/g, " ");
}

export function kindLabel(t: Translations, kind: RevolutTransaction["kind"]): string {
  return t.transactions.kinds[kind];
}

/** "YYYY-MM" of a local date (today by default). */
export function monthKey(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

/** The `n` month keys ending at `end` (inclusive), oldest first. */
export function monthsBack(n: number, end = monthKey()): string[] {
  const [y, m] = end.split("-").map(Number);
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(y, m - 1 - (n - 1 - i), 1);
    return monthKey(d);
  });
}

export function previousMonth(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return monthKey(new Date(y, m - 2, 1));
}

export function monthOf(file: RevolutTransactionsFile | null, month: string): TransactionMonth | null {
  return file?.months.find((m) => m.month === month) ?? null;
}

export function inMonth(txs: RevolutTransaction[], month: string): RevolutTransaction[] {
  return txs.filter((t) => t.date.startsWith(month));
}

export interface CategoryTotal {
  category: string;
  amount: number;
  count: number;
  fraction: number;
}

/** Expenses per category (net of refunds), descending, with share of the total.
 * Works from the rows rather than `byCategory` so a filtered/searched list
 * can be summarised the same way. */
export function expensesByCategory(txs: RevolutTransaction[]): CategoryTotal[] {
  const totals = new Map<string, { amount: number; count: number }>();
  for (const t of txs) {
    if (t.kind !== "expense" && t.kind !== "refund") continue;
    const entry = totals.get(t.category) ?? { amount: 0, count: 0 };
    entry.amount -= t.amountEur;
    entry.count += 1;
    totals.set(t.category, entry);
  }
  const rows = [...totals]
    .map(([category, { amount, count }]) => ({ category, amount, count }))
    .filter((r) => r.amount > 0)
    .sort((a, b) => b.amount - a.amount);
  const total = rows.reduce((s, r) => s + r.amount, 0);
  return rows.map((r) => ({ ...r, fraction: total > 0 ? r.amount / total : 0 }));
}

/** Top `n` categories plus an "other" bucket for the rest — for small donuts. */
export function topCategories(rows: CategoryTotal[], n: number): CategoryTotal[] {
  if (rows.length <= n) return rows;
  const head = rows.slice(0, n);
  const rest = rows.slice(n);
  const amount = rest.reduce((s, r) => s + r.amount, 0);
  const count = rest.reduce((s, r) => s + r.count, 0);
  const fraction = rest.reduce((s, r) => s + r.fraction, 0);
  return [...head, { category: "other", amount, count, fraction }];
}

/** Days elapsed in a month: the full month for past months, days so far for
 * the current one — the divisor behind "average per day". */
export function daysElapsed(month: string, today = new Date()): number {
  const [y, m] = month.split("-").map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  if (month < monthKey(today)) return daysInMonth;
  if (month > monthKey(today)) return 0;
  return today.getDate();
}

/** Percent change from `previous` to `current`; null when there is no base. */
export function deltaPct(current: number, previous: number | undefined | null): number | null {
  if (!previous) return null;
  return ((current - previous) / previous) * 100;
}

/** Interest credited in the last `days` days, oldest first, zero-filled so a
 * sparkline has one point per calendar day. */
export function interestSeries(
  file: RevolutTransactionsFile | null,
  days: number,
  today = new Date(),
): { date: string; amount: number }[] {
  const byDate = new Map((file?.interest.daily ?? []).map((d) => [d.date, d.amount]));
  const out: { date: string; amount: number }[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    out.push({ date: key, amount: byDate.get(key) ?? 0 });
  }
  return out;
}

/** Sum of interest for a "YYYY-MM" month. */
export function interestInMonth(file: RevolutTransactionsFile | null, month: string): number {
  return file?.interest.byMonth.find((m) => m.month === month)?.amount ?? 0;
}

/** Matches merchant, description or category, case- and accent-insensitive. */
export function matchesQuery(t: RevolutTransaction, query: string): boolean {
  const q = normalise(query);
  if (!q) return true;
  return [t.merchant, t.description, t.category].some((s) => s && normalise(s).includes(q));
}

function normalise(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

/** Parses a "YYYY-MM-DD" (or full ISO) date as a *local* day. `new Date("2026-03-05")`
 * is UTC midnight and renders as the previous day west of Greenwich. */
export function localDay(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** Calendar days from the earliest transaction to `today`, inclusive — the
 * divisor for "average per day" over all time. At least 1. */
export function daysSinceFirst(txs: RevolutTransaction[], today = new Date()): number {
  if (txs.length === 0) return 1;
  let first = txs[0].date;
  for (const t of txs) if (t.date < first) first = t.date;
  const start = localDay(first);
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.max(1, Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1);
}

/** Net EUR movement of the rows. Internal moves are left out (they net to zero
 * across accounts) unless `includeTransfers`, e.g. when only those are listed. */
export function netOfRows(txs: RevolutTransaction[], includeTransfers = false): number {
  let sum = 0;
  for (const t of txs) if (includeTransfers || t.kind !== "transfer") sum += t.amountEur;
  return sum;
}
