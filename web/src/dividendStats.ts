// Pure aggregations behind the Dividends page: month-by-month income (paid and
// projected) and per-stock totals.

import type { ProjectedPayment } from "./dividendForecast";
import type { DividendPayment } from "./types";

export interface MonthBucket {
  /** "YYYY-MM" */
  month: string;
  /** Net EUR actually received. */
  paid: number;
  /** Estimated net EUR still to come (announced or projected). */
  projected: number;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** The months from `back` before the month of `today` to `ahead` after it,
 * inclusive, as "YYYY-MM" keys. */
export function monthWindow(today: string, back: number, ahead: number): string[] {
  const y = Number(today.slice(0, 4));
  const m = Number(today.slice(5, 7)) - 1;
  const out: string[] = [];
  for (let i = -back; i <= ahead; i++) {
    const d = new Date(Date.UTC(y, m + i, 1));
    out.push(`${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}`);
  }
  return out;
}

/** Net income per month over the trailing `back` months and the next `ahead`.
 * The current month holds both what already landed and what is still due. */
export function monthlyIncome(
  payments: DividendPayment[],
  projected: ProjectedPayment[],
  today: string,
  back = 11,
  ahead = 11,
): MonthBucket[] {
  const buckets = new Map<string, MonthBucket>(
    monthWindow(today, back, ahead).map((month) => [month, { month, paid: 0, projected: 0 }]),
  );
  for (const p of payments) {
    const b = buckets.get(p.date.slice(0, 7));
    if (b) b.paid += p.netEur ?? p.net;
  }
  for (const p of projected) {
    const b = buckets.get(p.date.slice(0, 7));
    if (b) b.projected += p.netEur;
  }
  return [...buckets.values()];
}

export interface PayerTotal {
  symbol: string;
  name: string;
  net: number;
  count: number;
  /** Share of all net income, 0–1. */
  share: number;
}

/** Net received per stock, biggest first. */
export function payerTotals(payments: DividendPayment[]): PayerTotal[] {
  const map = new Map<string, PayerTotal>();
  let all = 0;
  for (const p of payments) {
    const net = p.netEur ?? p.net;
    all += net;
    const cur = map.get(p.symbol) ?? { symbol: p.symbol, name: p.name || p.symbol, net: 0, count: 0, share: 0 };
    cur.net += net;
    cur.count += 1;
    map.set(p.symbol, cur);
  }
  const rows = [...map.values()].sort((a, b) => b.net - a.net);
  for (const r of rows) r.share = all > 0 ? r.net / all : 0;
  return rows;
}
