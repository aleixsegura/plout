// Dividends the current holdings should pay over the next twelve months.
//
// Nothing here calls out: the dividend per share is worked out from what
// DEGIRO already exported. A past payment's gross divided by the shares held
// that day (replayed from the taxes.json ledger) gives its per-share amount,
// and an announced payment spells its own out ("Dividendo 1.0300 * 19.00
// Acciones"). Each holding's last year of payments is then rolled forward one
// year at today's share count — which keeps uneven patterns like an interim
// plus a final dividend — and announced payments replace their estimate.

import { quantityHeldOn, tradesForPosition } from "./lots";
import type { DividendsFile, Position, TaxTrade, UpcomingDividend } from "./types";

const DAY_MS = 86_400_000;
const YEAR_DAYS = 365;

export type Frequency = 1 | 2 | 4 | 12;

/** One known dividend per share: paid, or announced by DEGIRO. */
interface PerSharePoint {
  date: string;
  perShare: number;
  /** Withholding as a fraction of gross; null when the source didn't say. */
  taxRate: number | null;
  announced: boolean;
}

export interface ProjectedPayment {
  date: string;
  symbol: string;
  name: string;
  currency: string;
  perShare: number;
  quantity: number;
  /** In `currency`. */
  gross: number;
  grossEur: number;
  /** grossEur after the holding's usual withholding; equal to grossEur when no
   * withholding is known. */
  netEur: number;
  /** Announced by DEGIRO rather than estimated from the past year. */
  announced: boolean;
}

export interface HoldingForecast {
  symbol: string;
  name: string;
  currency: string;
  quantity: number;
  frequency: Frequency;
  /** No two payments to measure a cadence from: guessed from the listing. */
  frequencyAssumed: boolean;
  /** The most recent dividend per share, paid or announced. */
  lastPerShare: number;
  /** Sum of the projected payments' dividend per share. */
  annualPerShare: number;
  annualGross: number;
  annualGrossEur: number;
  annualNetEur: number;
  withholdingRate: number | null;
  /** annualGrossEur over the position's current value. */
  yieldPct: number | null;
  nextDate: string | null;
}

export interface DividendForecast {
  holdings: HoldingForecast[];
  payments: ProjectedPayment[];
  annualGrossEur: number;
  annualNetEur: number;
}

// ── Dates ──────────────────────────────────────────────────────────────────────

function toMs(day: string): number {
  return Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)));
}

function fromMs(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function addDays(day: string, days: number): string {
  return fromMs(toMs(day) + Math.round(days) * DAY_MS);
}

function daysBetween(a: string, b: string): number {
  return Math.round((toMs(b) - toMs(a)) / DAY_MS);
}

// ── Inputs ─────────────────────────────────────────────────────────────────────

/** "Dividendo 1.0300 * 19.00 Acciones" → { perShare: 1.03, shares: 19 }. */
export function parseUpcomingDescription(description: string): { perShare: number; shares: number } | null {
  const m = description.match(/(-?\d+(?:[.,]\d+)?)\s*[*x×]\s*(\d+(?:[.,]\d+)?)/);
  if (!m) return null;
  const perShare = Math.abs(Number(m[1].replace(",", ".")));
  const shares = Number(m[2].replace(",", "."));
  return perShare > 0 && shares > 0 ? { perShare, shares } : null;
}

function normalizeName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, " ")
    .replace(/\b(inc|corp|corporation|plc|sa|se|nv|ag|ltd|co|class [a-z]|adr on)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** DEGIRO's upcoming payments carry a name but no product id. */
function upcomingForPosition(upcoming: UpcomingDividend[], position: Position): UpcomingDividend[] {
  const target = normalizeName(position.name || position.symbol);
  if (!target) return [];
  return upcoming.filter((u) => {
    const name = normalizeName(u.name);
    return name !== "" && (name === target || name.startsWith(target) || target.startsWith(name));
  });
}

function points(
  position: Position,
  dividends: DividendsFile,
  trades: TaxTrade[],
): { points: PerSharePoint[]; currency: string | null; eurPerUnit: number | null } {
  const own = tradesForPosition(trades, position);
  const sharesOn = (date: string) => (own.length > 0 ? quantityHeldOn(own, date) : position.quantity);

  const out: PerSharePoint[] = [];
  let currency: string | null = null;
  let eurPerUnit: number | null = null;

  const paid = dividends.payments
    .filter((p) => String(p.productId) === String(position.id) || p.symbol === position.symbol)
    .sort((a, b) => a.date.localeCompare(b.date));
  for (const p of paid) {
    const shares = sharesOn(p.date);
    if (shares <= 0 || p.gross <= 0) continue;
    out.push({
      date: p.date,
      perShare: p.gross / shares,
      taxRate: Math.min(Math.abs(p.tax) / p.gross, 1),
      announced: false,
    });
    currency = p.currency;
    if (p.grossEur != null) eurPerUnit = p.grossEur / p.gross;
  }

  // An announced payment comes as a gross line and, usually, a withholding
  // line on the same day.
  const byDay = new Map<string, UpcomingDividend[]>();
  for (const u of upcomingForPosition(dividends.upcoming ?? [], position)) {
    if (!u.payDate) continue;
    const day = u.payDate.slice(0, 10);
    byDay.set(day, [...(byDay.get(day) ?? []), u]);
  }
  for (const [day, lines] of byDay) {
    // Already in the paid history (the export ran before and after the pay date).
    if (out.some((p) => !p.announced && Math.abs(daysBetween(p.date, day)) <= 7)) continue;
    const gross = lines.find((l) => (l.amount ?? 0) > 0);
    if (!gross || gross.amount == null) continue;
    const parsed = parseUpcomingDescription(gross.description);
    const perShare = parsed ? parsed.perShare : gross.amount / position.quantity;
    const taxLine = lines.find((l) => (l.amount ?? 0) < 0);
    const taxParsed = taxLine ? parseUpcomingDescription(taxLine.description) : null;
    const taxRate = taxParsed
      ? taxParsed.perShare / perShare
      : taxLine?.amount != null
        ? Math.abs(taxLine.amount) / gross.amount
        : null;
    out.push({ date: day, perShare, taxRate: taxRate === null ? null : Math.min(taxRate, 1), announced: true });
    currency = currency ?? gross.currency;
    if (eurPerUnit === null && gross.amountEur != null && gross.amount) eurPerUnit = gross.amountEur / gross.amount;
  }

  out.sort((a, b) => a.date.localeCompare(b.date));
  return { points: out, currency, eurPerUnit };
}

// ── Cadence ────────────────────────────────────────────────────────────────────

function snapFrequency(gapDays: number): Frequency {
  if (gapDays < 60) return 12;
  if (gapDays < 135) return 4;
  if (gapDays < 250) return 2;
  return 1;
}

function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** Payments per year from the gaps between the last two years' points. With a
 * single point there's no gap, so it's a guess: US companies pay quarterly
 * almost without exception, Europeans mostly once a year. */
function frequencyFor(pts: PerSharePoint[], position: Position, today: string): { frequency: Frequency; assumed: boolean } {
  const recent = pts.filter((p) => daysBetween(p.date, today) <= 2 * YEAR_DAYS);
  const gaps: number[] = [];
  for (let i = 1; i < recent.length; i++) {
    const gap = daysBetween(recent[i - 1].date, recent[i].date);
    if (gap > 7) gaps.push(gap);
  }
  if (gaps.length > 0) return { frequency: snapFrequency(median(gaps)), assumed: false };
  const us = position.isin?.startsWith("US") ?? position.currency === "USD";
  return { frequency: us ? 4 : 1, assumed: true };
}

// ── Projection ─────────────────────────────────────────────────────────────────

/** Projected payments for one holding in (today, today + 1 year]. */
function project(pts: PerSharePoint[], frequency: Frequency, today: string): PerSharePoint[] {
  const end = addDays(today, YEAR_DAYS);
  const inWindow = (d: string) => d > today && d <= end;
  const period = YEAR_DAYS / frequency;
  const out: PerSharePoint[] = [];
  const clashes = (d: string) => out.some((p) => Math.abs(daysBetween(p.date, d)) < period / 2);

  // Announced payments are facts; they go in first.
  for (const p of pts) if (p.announced && inWindow(p.date)) out.push(p);

  // Last year's payments, one year on — keeps each payment's own size.
  for (const p of pts) {
    const next = addDays(p.date, YEAR_DAYS);
    if (inWindow(next) && !clashes(next)) out.push({ ...p, date: next, announced: false });
  }

  // Too few for the cadence (recently bought, or a single known payment):
  // continue from the latest one at the latest amount. Not for a payer that
  // has already skipped a whole cycle: that dividend may well be gone.
  const last = pts[pts.length - 1];
  if (out.length < frequency && last && daysBetween(last.date, today) <= YEAR_DAYS + period / 2) {
    for (let k = 1; k <= frequency * 2 + 1; k++) {
      const next = addDays(last.date, period * k);
      if (next > end) break;
      if (inWindow(next) && !clashes(next)) out.push({ ...last, date: next, announced: false });
    }
  }
  return out.sort((a, b) => a.date.localeCompare(b.date));
}

export function forecastDividends(
  positions: Position[],
  dividends: DividendsFile,
  trades: TaxTrade[],
  today: string,
): DividendForecast {
  const holdings: HoldingForecast[] = [];
  const payments: ProjectedPayment[] = [];

  for (const position of positions) {
    if (position.quantity <= 0) continue;
    const { points: pts, currency: payCurrency, eurPerUnit: paidRate } = points(position, dividends, trades);
    if (pts.length === 0) continue;

    const currency = payCurrency ?? position.currency;
    // Today's FX from the position itself when it trades in the payout
    // currency; otherwise the rate of the last payment.
    const marketValue = position.lastPrice !== null ? position.lastPrice * position.quantity : 0;
    const eurPerUnit =
      currency === "EUR"
        ? 1
        : currency === position.currency && marketValue > 0
          ? position.valueEur / marketValue
          : paidRate;
    if (eurPerUnit === null) continue;

    const { frequency, assumed } = frequencyFor(pts, position, today);
    const taxRates = pts.map((p) => p.taxRate).filter((r): r is number => r !== null);
    const withholdingRate = taxRates.length > 0 ? taxRates[taxRates.length - 1] : null;

    const projected = project(pts, frequency, today);
    let annualPerShare = 0;
    let annualGrossEur = 0;
    let annualNetEur = 0;
    for (const p of projected) {
      const gross = p.perShare * position.quantity;
      const grossEur = gross * eurPerUnit;
      const rate = p.taxRate ?? withholdingRate ?? 0;
      const netEur = grossEur * (1 - rate);
      annualPerShare += p.perShare;
      annualGrossEur += grossEur;
      annualNetEur += netEur;
      payments.push({
        date: p.date,
        symbol: position.symbol,
        name: position.name || position.symbol,
        currency,
        perShare: p.perShare,
        quantity: position.quantity,
        gross,
        grossEur,
        netEur,
        announced: p.announced,
      });
    }

    holdings.push({
      symbol: position.symbol,
      name: position.name || position.symbol,
      currency,
      quantity: position.quantity,
      frequency,
      frequencyAssumed: assumed,
      lastPerShare: pts[pts.length - 1].perShare,
      annualPerShare,
      annualGross: annualPerShare * position.quantity,
      annualGrossEur,
      annualNetEur,
      withholdingRate,
      yieldPct: position.valueEur > 0 ? (annualGrossEur / position.valueEur) * 100 : null,
      nextDate: projected[0]?.date ?? null,
    });
  }

  holdings.sort((a, b) => b.annualGrossEur - a.annualGrossEur);
  payments.sort((a, b) => a.date.localeCompare(b.date) || a.symbol.localeCompare(b.symbol));
  return {
    holdings,
    payments,
    annualGrossEur: holdings.reduce((s, h) => s + h.annualGrossEur, 0),
    annualNetEur: holdings.reduce((s, h) => s + h.annualNetEur, 0),
  };
}

/** Local "YYYY-MM-DD" — the forecast's "today". */
export function localToday(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
