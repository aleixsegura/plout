import type { DepositMovement, HistoryDay } from "./types";

/** One point of the investment-return series: cumulative since the first
 * usable snapshot, so the series starts at 0. */
export interface PerformancePoint {
  date: string;
  /** Return in EUR since the start: change in value minus money moved in. */
  plEur: number;
  /** Time-weighted return since the start, in percent. */
  pct: number;
  /** Invested value on the day (the perimeter `legs` defines). */
  valueEur: number;
  /** Money moved in (positive) or out during the interval ending on the day. */
  flowEur: number;
}

/** A price series to measure against: ascending dates with EUR closes. */
export interface PriceLine {
  dates: string[];
  closesEur: number[];
}

/** One source's invested value and its running "value minus money in" on a
 * day. Differences of `gain` between two days are that source's return. */
interface Leg {
  value: number;
  gain: number;
}

/** What each source contributes to the investment return. The Revolut current
 * account is left out on purpose — salary lands there, and it holds no
 * investments. Of the Boosted savings only the interest counts as return; the
 * transfers into it are money moved, not earned. Null when the day lacks the
 * running totals (snapshots recorded before they existed, not yet backfilled). */
function legs(day: HistoryDay): { degiro: Leg | null; revolut: Leg | null } | null {
  let degiro: Leg | null = null;
  if (day.degiro) {
    if (day.degiro.netDepositsEur == null) return null;
    const value = day.degiro.valueEur + (day.degiro.cashEur ?? 0);
    degiro = { value, gain: value - day.degiro.netDepositsEur };
  }
  let revolut: Leg | null = null;
  if (day.revolut) {
    const { cryptoValueEur, cashSavingsEur, netInvestedEur, interestEur } = day.revolut;
    if (netInvestedEur == null || interestEur == null) return null;
    revolut = {
      value: cryptoValueEur + cashSavingsEur,
      gain: cryptoValueEur - netInvestedEur + interestEur,
    };
  }
  return { degiro, revolut };
}

/** Investment return across the snapshots that carry running totals.
 *
 * Each interval's return is the sum, over the sources present on both days,
 * of the change in `gain`. A source that appears or disappears between two
 * snapshots contributes nothing that interval — its whole value is a flow.
 * The percentage chains each interval's Modified Dietz return (flows assumed
 * mid-interval), so a large deposit doesn't dilute or inflate it the way
 * "P/L ÷ starting value" would. */
export function performanceSeries(days: HistoryDay[]): PerformancePoint[] {
  const usable = days.flatMap((d) => {
    const l = legs(d);
    return l ? [{ date: d.date, ...l }] : [];
  });
  if (usable.length === 0) return [];

  const value = (u: (typeof usable)[number]) => (u.degiro?.value ?? 0) + (u.revolut?.value ?? 0);
  const points: PerformancePoint[] = [
    { date: usable[0].date, plEur: 0, pct: 0, valueEur: value(usable[0]), flowEur: 0 },
  ];
  let plEur = 0;
  let growth = 1;
  for (let i = 1; i < usable.length; i++) {
    const prev = usable[i - 1];
    const cur = usable[i];
    let ret = 0;
    let startValue = 0;
    let flows = 0;
    for (const key of ["degiro", "revolut"] as const) {
      const a = prev[key];
      const b = cur[key];
      if (a && b) {
        const r = b.gain - a.gain;
        ret += r;
        startValue += a.value;
        flows += b.value - a.value - r;
      } else if (b) {
        flows += b.value;
      } else if (a) {
        flows -= a.value;
      }
    }
    const base = startValue + flows / 2;
    if (base > 0) growth *= 1 + ret / base;
    plEur += ret;
    points.push({ date: cur.date, plEur, pct: (growth - 1) * 100, valueEur: value(cur), flowEur: flows });
  }
  return points;
}

/** Last close on or before `date` ("YYYY-MM-DD" or a full ISO timestamp);
 * null when the line starts later. */
export function closeOn(line: PriceLine, date: string): number | null {
  const day = date.slice(0, 10);
  let lo = 0;
  let hi = line.dates.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (line.dates[mid] <= day) lo = mid + 1;
    else hi = mid;
  }
  return lo > 0 ? line.closesEur[lo - 1] : null;
}

/** The same money in the index instead: the starting value bought at the first
 * snapshot's close, and each interval's flow bought (or, when negative, sold)
 * at the average of the interval's two closes — the same mid-interval
 * assumption the return % makes. Returns that shadow portfolio's return in
 * EUR per point, comparable one-to-one with `plEur`; null when the line has
 * no price for the first snapshot. */
export function benchmarkReturns(points: PerformancePoint[], line: PriceLine): number[] | null {
  if (points.length === 0) return [];
  const prices = points.map((p) => closeOn(line, p.date));
  if (prices.some((p) => p == null)) return null;
  const px = prices as number[];
  let units = points[0].valueEur / px[0];
  let invested = points[0].valueEur;
  const out = [0];
  for (let i = 1; i < points.length; i++) {
    units += points[i].flowEur / ((px[i - 1] + px[i]) / 2);
    invested += points[i].flowEur;
    out.push(units * px[i] - invested);
  }
  return out;
}

/** Since the account opened: what DEGIRO's deposits would be worth had each
 * gone into the index on its booking day (withdrawals sold back the same way).
 * `deposited` is the net total, so `valueEur − deposited` is the index's P/L to
 * set against DEGIRO's own account P/L. Null when the line starts after the
 * first deposit. */
export function depositsInBenchmark(
  movements: DepositMovement[],
  line: PriceLine,
  asOf: string,
): { valueEur: number; deposited: number; plEur: number } | null {
  let units = 0;
  let deposited = 0;
  for (const m of movements) {
    if (m.date.slice(0, 10) > asOf.slice(0, 10)) continue;
    const price = closeOn(line, m.date);
    if (price == null) return null;
    units += m.amountEur / price;
    deposited += m.amountEur;
  }
  const last = closeOn(line, asOf);
  if (last == null) return null;
  const valueEur = units * last;
  return { valueEur, deposited, plEur: valueEur - deposited };
}
