// What the analysis endpoints send to Claude, built from the exports the app
// already reads. Everything here is deterministic arithmetic over those files:
// the weights, HHI and yields are computed once, here, so the model reasons
// over exact figures instead of re-deriving them from raw rows.
//
// Nothing is written: the files are read on each request and the result only
// lives in the request that asked for it.

import fs from "node:fs";
import path from "node:path";

// The export shapes are mirrored in src/types.ts for the browser. The server
// only needs a handful of fields, read defensively: an older export missing a
// field must degrade the context, never fail the request.
type Json = any;

export interface Exports {
  positions: Json | null;
  revolut: Json | null;
  marketCaps: Json | null;
  dividends: Json | null;
  fees: Json | null;
  history: Json | null;
  prices: Json | null;
  symbols: Json | null;
  /** Every `<watchlist>.json` listed in index.json, by slug. */
  watchlists: Record<string, Json>;
}

function readJson(dir: string, name: string): Json | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
  } catch {
    return null;
  }
}

export function loadExports(dataDir: string): Exports {
  const index = readJson(dataDir, "index.json");
  const watchlists: Record<string, Json> = {};
  for (const w of index?.watchlists ?? []) {
    if (typeof w?.slug !== "string" || !/^[\w-]+$/.test(w.slug)) continue;
    const file = readJson(dataDir, `${w.slug}.json`);
    if (file) watchlists[w.slug] = file;
  }
  return {
    positions: readJson(dataDir, "positions.json"),
    revolut: readJson(dataDir, "revolut.json"),
    marketCaps: readJson(dataDir, "marketcaps.json"),
    dividends: readJson(dataDir, "dividends.json"),
    fees: readJson(dataDir, "fees.json"),
    history: readJson(dataDir, "history.json"),
    prices: readJson(dataDir, "prices.json"),
    symbols: readJson(dataDir, "symbols.json"),
    watchlists,
  };
}

// --- symbol spellings ------------------------------------------------------

/** "XMPL.DE" and "XMPL" are the same asset from two sources (a watchlist and
 * DEGIRO). Same idea as marketcaps.py keying every spelling: compare on the
 * part before the exchange suffix, case-insensitively. */
export function symbolKey(symbol: string): string {
  return symbol.trim().toUpperCase().split(".")[0];
}

const sameAsset = (a: string | null | undefined, b: string) =>
  typeof a === "string" && symbolKey(a) === symbolKey(b);

// --- holdings --------------------------------------------------------------

const round = (v: number, digits = 2) => {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
};
const pct = (part: number, whole: number) => (whole > 0 ? round((part / whole) * 100, 2) : null);

interface Holding {
  symbol: string;
  name: string;
  platform: "DEGIRO" | "Revolut";
  type: string;
  sector: string;
  currency: string;
  quantity: number;
  valueEur: number;
  costEur: number | null;
  plEur: number | null;
}

const SECTOR_BY_TYPE: Record<string, string> = { CRYPTO: "Crypto" };

function holdings(ex: Exports): Holding[] {
  const sectors: Record<string, string> = ex.symbols?.sectors ?? {};
  const out: Holding[] = [];
  for (const p of ex.positions?.positions ?? []) {
    const type = String(p.productType ?? "").toUpperCase() || "UNKNOWN";
    out.push({
      symbol: p.symbol,
      name: p.name || p.symbol,
      platform: "DEGIRO",
      type,
      sector: sectors[p.symbol] ?? "Other",
      currency: p.currency,
      quantity: p.quantity,
      valueEur: p.valueEur ?? 0,
      costEur: p.costEur ?? null,
      plEur: p.plEur ?? null,
    });
  }
  for (const p of ex.revolut?.positions ?? []) {
    out.push({
      symbol: p.symbol,
      name: p.name || p.symbol,
      platform: "Revolut",
      type: "CRYPTO",
      sector: SECTOR_BY_TYPE.CRYPTO,
      currency: p.priceCurrency ?? p.currency ?? "",
      quantity: p.quantity,
      valueEur: p.valueEur ?? 0,
      costEur: p.costEur ?? null,
      plEur: p.plEur ?? null,
    });
  }
  return out.sort((a, b) => b.valueEur - a.valueEur);
}

function cashEur(ex: Exports) {
  const degiro = ex.positions?.summary?.cashEur ?? 0;
  const revolut = ex.revolut?.totalCashEur ?? 0;
  return { degiro, revolut, total: degiro + revolut };
}

/** Net dividends per asset over the export's window, in EUR. */
function dividendsBySymbol(ex: Exports): Map<string, { netEur: number; grossEur: number; payments: number }> {
  const map = new Map<string, { netEur: number; grossEur: number; payments: number }>();
  for (const d of ex.dividends?.payments ?? []) {
    const key = symbolKey(String(d.symbol ?? ""));
    const row = map.get(key) ?? { netEur: 0, grossEur: 0, payments: 0 };
    row.netEur += d.netEur ?? 0;
    row.grossEur += d.grossEur ?? 0;
    row.payments += 1;
    map.set(key, row);
  }
  return map;
}

/** Return over the last `days` calendar days of a prices.json series. */
function seriesReturn(series: Json, days: number): number | null {
  const dates: string[] = series?.dates ?? [];
  const closes: number[] = series?.closesEur ?? [];
  if (dates.length < 2 || closes.length !== dates.length) return null;
  const last = closes[closes.length - 1];
  const cutoff = new Date(dates[dates.length - 1]);
  cutoff.setDate(cutoff.getDate() - days);
  const iso = cutoff.toISOString().slice(0, 10);
  const i = dates.findIndex((d) => d >= iso);
  if (i < 0 || i === dates.length - 1 || !closes[i]) return null;
  return round((last / closes[i] - 1) * 100, 2);
}

function priceSeriesFor(ex: Exports, symbol: string): Json | null {
  const series: Record<string, Json> = ex.prices?.series ?? {};
  const hit = Object.entries(series).find(([s]) => sameAsset(s, symbol));
  return hit ? hit[1] : null;
}

function priceSummary(ex: Exports, symbol: string) {
  const s = priceSeriesFor(ex, symbol);
  if (!s) return null;
  return {
    lastCloseEur: s.closesEur?.[s.closesEur.length - 1] ?? null,
    lastDate: s.dates?.[s.dates.length - 1] ?? null,
    quoteCurrency: s.currency,
    market: s.market,
    returnPct: {
      "1m": seriesReturn(s, 30),
      "3m": seriesReturn(s, 91),
      "1y": seriesReturn(s, 365),
      "3y": seriesReturn(s, 365 * 3),
    },
  };
}

function marketCapFor(ex: Exports, symbol: string) {
  const entries: Record<string, Json> = ex.marketCaps?.entries ?? {};
  const hit = entries[symbol] ?? Object.entries(entries).find(([s]) => sameAsset(s, symbol))?.[1];
  return hit ? { marketCap: hit.marketCap, currency: hit.currency, source: hit.source, at: hit.at } : null;
}

const WATCHLIST_CAP = 60;

/** Keep a long list from bloating the prompt: first `max` items plus a count. */
export function capList(items: string[], max: number): string[] {
  return items.length <= max ? items : [...items.slice(0, max), `… and ${items.length - max} more`];
}

// --- portfolio context (#1) ------------------------------------------------

export function portfolioContext(ex: Exports) {
  const rows = holdings(ex);
  const cash = cashEur(ex);
  const investedEur = rows.reduce((s, h) => s + h.valueEur, 0);
  const totalEur = investedEur + cash.total;
  const divs = dividendsBySymbol(ex);

  const weights = rows.map((h) => h.valueEur / (investedEur || 1));
  const hhi = weights.reduce((s, w) => s + w * w, 0);
  const topN = (n: number) => pct(rows.slice(0, n).reduce((s, h) => s + h.valueEur, 0), investedEur);

  const bySector = new Map<string, number>();
  for (const h of rows) bySector.set(h.sector, (bySector.get(h.sector) ?? 0) + h.valueEur);
  const byCurrency = new Map<string, number>();
  for (const h of rows) byCurrency.set(h.currency || "?", (byCurrency.get(h.currency || "?") ?? 0) + h.valueEur);

  const costDegiro = rows.filter((h) => h.platform === "DEGIRO").reduce((s, h) => s + (h.costEur ?? 0), 0);
  const netDividends = [...divs.values()].reduce((s, d) => s + d.netEur, 0);
  const cryptoEur = rows.filter((h) => h.type === "CRYPTO").reduce((s, h) => s + h.valueEur, 0);

  const days: Json[] = ex.history?.days ?? [];
  const firstDay = days[0];
  const lastDay = days[days.length - 1];

  return {
    asOf: ex.positions?.updatedAt ?? ex.revolut?.updatedAt ?? null,
    baseCurrency: "EUR",
    totals: {
      totalEur: round(totalEur),
      investedEur: round(investedEur),
      cashEur: round(cash.total),
      cashPctOfTotal: pct(cash.total, totalEur),
      cryptoEur: round(cryptoEur),
      cryptoPctOfTotal: pct(cryptoEur, totalEur),
      unrealizedPlEur: round(rows.reduce((s, h) => s + (h.plEur ?? 0), 0)),
      netDepositsEur: ex.positions?.summary?.netDepositsEur ?? null,
    },
    concentration: {
      holdings: rows.length,
      top1PctOfInvested: topN(1),
      top3PctOfInvested: topN(3),
      top5PctOfInvested: topN(5),
      top10PctOfInvested: topN(10),
      // Herfindahl–Hirschman index over invested weights (0–1); 1/HHI is the
      // "effective number" of equally weighted positions.
      hhi: round(hhi, 4),
      effectiveHoldings: hhi > 0 ? round(1 / hhi, 1) : null,
    },
    // "Other" is every equity/ETF symbols.json doesn't classify — a data-quality
    // flag, not a sector.
    sectorWeightsPctOfInvested: Object.fromEntries(
      [...bySector].sort((a, b) => b[1] - a[1]).map(([s, v]) => [s, pct(v, investedEur)]),
    ),
    // Trading currency only: an ETF quoted in EUR can hold mostly USD assets.
    tradingCurrencyPctOfInvested: Object.fromEntries(
      [...byCurrency].sort((a, b) => b[1] - a[1]).map(([c, v]) => [c, pct(v, investedEur)]),
    ),
    cash: {
      degiroEur: round(cash.degiro),
      revolutEur: round(cash.revolut),
      degiroByCurrency: ex.positions?.summary?.cashBreakdown ?? null,
      revolutAccounts: (ex.revolut?.cash ?? []).map((c: Json) => ({
        kind: c.kind,
        currency: c.currency,
        amountEur: c.amountEur,
      })),
    },
    income: {
      dividendWindow: ex.dividends ? { from: ex.dividends.fromDate, to: ex.dividends.toDate } : null,
      netDividendsEur: round(netDividends),
      dividendYieldOnCostPct: pct(netDividends, costDegiro),
      feesEur: ex.fees
        ? {
            window: { from: ex.fees.fromDate, to: ex.fees.toDate },
            total: ex.fees.totalEur,
            byCategory: ex.fees.byCategory,
            byYear: (ex.fees.byYear ?? []).map((y: Json) => ({ year: y.year, amountEur: y.amountEur })),
          }
        : null,
    },
    history:
      firstDay && lastDay
        ? {
            from: firstDay.date,
            to: lastDay.date,
            totalEurFrom: firstDay.totalEur,
            totalEurTo: lastDay.totalEur,
            snapshots: days.length,
          }
        : null,
    holdings: rows.map((h) => ({
      symbol: h.symbol,
      name: h.name,
      platform: h.platform,
      type: h.type,
      sector: h.sector,
      tradingCurrency: h.currency,
      valueEur: round(h.valueEur),
      pctOfInvested: pct(h.valueEur, investedEur),
      plEur: h.plEur === null ? null : round(h.plEur),
      plPct: h.plEur !== null && h.costEur ? pct(h.plEur, h.costEur) : null,
      marketCap: marketCapFor(ex, h.symbol)?.marketCap ?? null,
      netDividendsEur: divs.has(symbolKey(h.symbol)) ? round(divs.get(symbolKey(h.symbol))!.netEur) : null,
      returnPct: priceSummary(ex, h.symbol)?.returnPct ?? null,
    })),
    // Names watched but not (necessarily) held: context for "what else is on
    // the radar", kept to identifiers so the request stays small.
    watchlists: Object.fromEntries(
      Object.entries(ex.watchlists).map(([slug, w]) => [
        w.name ?? slug,
        capList((w.stocks ?? []).map((s: Json) => `${s.symbol} (${s.symbolType ?? "?"})`), WATCHLIST_CAP),
      ]),
    ),
  };
}

// --- single-asset context (#2) ---------------------------------------------

export function assetContext(ex: Exports, symbol: string) {
  const rows = holdings(ex);
  const investedEur = rows.reduce((s, h) => s + h.valueEur, 0);
  const held = rows.filter((h) => sameAsset(h.symbol, symbol));

  // A watchlist row describes the asset even when nothing is held.
  const watchRows: { watchlist: string; row: Json }[] = [];
  for (const [slug, w] of Object.entries(ex.watchlists)) {
    for (const s of w.stocks ?? []) if (sameAsset(s.symbol, symbol)) watchRows.push({ watchlist: w.name ?? slug, row: s });
  }

  if (held.length === 0 && watchRows.length === 0) return null;

  const first = held[0];
  const listing = watchRows[0]?.row;
  const type = first?.type ?? String(listing?.symbolType ?? "UNKNOWN").toUpperCase();
  const isEtf = type === "ETF" || type === "FUND" || type === "MUTUAL_FUND";

  const divs = ex.dividends?.payments?.filter((d: Json) => sameAsset(d.symbol, symbol)) ?? [];
  const sector = first?.sector ?? null;
  const sectorEur = sector ? rows.filter((h) => h.sector === sector).reduce((s, h) => s + h.valueEur, 0) : 0;

  let position = null;
  if (held.length > 0) {
    const valueEur = held.reduce((s, h) => s + h.valueEur, 0);
    const degiro = ex.positions?.positions?.find((p: Json) => sameAsset(p.symbol, symbol));
    const revolut = ex.revolut?.positions?.find((p: Json) => sameAsset(p.symbol, symbol));
    const src = degiro ?? revolut;
    const plEur = held.every((h) => h.plEur === null) ? null : held.reduce((s, h) => s + (h.plEur ?? 0), 0);
    const costEur = held.every((h) => h.costEur === null) ? null : held.reduce((s, h) => s + (h.costEur ?? 0), 0);
    position = {
      platforms: held.map((h) => h.platform),
      quantity: held.reduce((s, h) => s + h.quantity, 0),
      avgPrice: src?.avgPrice ?? null,
      avgPriceCurrency: degiro ? degiro.currency : revolut?.avgPriceCurrency ?? null,
      lastPrice: src?.lastPrice ?? null,
      lastPriceCurrency: degiro ? degiro.currency : revolut?.priceCurrency ?? null,
      valueEur: round(valueEur),
      costEur: costEur === null ? null : round(costEur),
      plEur: plEur === null ? null : round(plEur),
      plPct: plEur !== null && costEur ? pct(plEur, costEur) : null,
      realizedPlEur: degiro?.realizedPlEur ?? null,
      pctOfInvested: pct(valueEur, investedEur),
      rankByValue: rows.findIndex((h) => sameAsset(h.symbol, symbol)) + 1,
      holdingsCount: rows.length,
      sector,
      sectorPctOfInvested: sector ? pct(sectorEur, investedEur) : null,
      thisPositionPctOfSector: sector ? pct(valueEur, sectorEur) : null,
      dividends: {
        window: ex.dividends ? { from: ex.dividends.fromDate, to: ex.dividends.toDate } : null,
        payments: divs.length,
        netEur: round(divs.reduce((s: number, d: Json) => s + (d.netEur ?? 0), 0)),
        grossEur: round(divs.reduce((s: number, d: Json) => s + (d.grossEur ?? 0), 0)),
      },
    };
  }

  // For an ETF, the single names held elsewhere are what an overlap check
  // compares its holdings against; for crypto, the rest of the crypto sleeve.
  const otherSingleNames = isEtf
    ? rows.filter((h) => h.type === "STOCK").map((h) => ({ symbol: h.symbol, name: h.name, pctOfInvested: pct(h.valueEur, investedEur) }))
    : undefined;
  const cryptoSleeve =
    type === "CRYPTO" || type === "CRYPTO_CURRENCY"
      ? rows.filter((h) => h.type === "CRYPTO").map((h) => ({ symbol: h.symbol, name: h.name, valueEur: round(h.valueEur), pctOfInvested: pct(h.valueEur, investedEur) }))
      : undefined;

  return {
    symbol,
    name: first?.name ?? listing?.name ?? symbol,
    type,
    exchange: listing?.exchange ?? ex.positions?.positions?.find((p: Json) => sameAsset(p.symbol, symbol))?.exchange ?? null,
    isin: ex.positions?.positions?.find((p: Json) => sameAsset(p.symbol, symbol))?.isin ?? null,
    // Meaningless for an ETF, which is why marketcaps.py skips them.
    marketCap: isEtf ? null : marketCapFor(ex, symbol),
    price: priceSummary(ex, symbol),
    onWatchlists: watchRows.map((w) => w.watchlist),
    position,
    otherSingleNamesHeld: otherSingleNames,
    cryptoSleeve,
  };
}
