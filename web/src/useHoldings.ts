import { useMemo } from "react";
import type { Position, RevolutPosition, Stock } from "./types";
import { useAppData, useMarketCap } from "./data";
import { externalRealizedByPlatform } from "./realized";
import { buildLots, type LotBreakdown } from "./lots";

// DEGIRO's `productType` → the `symbolType` vocabulary that TypeIcon and
// sectorFor speak. An unmapped or missing type (files from an export before
// productType was added) falls through to the generic icon rather than guessing
// "EQUITY" and mislabelling an ETF.
const SYMBOL_TYPE_BY_PRODUCT_TYPE: Record<string, string> = {
  STOCK: "EQUITY",
  ETF: "ETF",
  FUND: "MUTUAL_FUND",
};

/** A table row for a DEGIRO position. Market cap is filled in afterwards from
 * marketcaps.json. */
function stockFromPosition(p: Position): Stock {
  const productType = p.productType?.toUpperCase() ?? "";
  return {
    symbol: p.symbol,
    name: p.name || p.symbol,
    compactName: p.name || p.symbol,
    exchange: p.exchange ?? "",
    symbolType: SYMBOL_TYPE_BY_PRODUCT_TYPE[productType] ?? "",
    currency: p.currency,
    lastPrice: p.lastPrice,
    marketCap: null,
  };
}

/** Same, for a Revolut holding: everything the exporter captures is crypto. */
function stockFromRevolutPosition(p: RevolutPosition): Stock {
  return {
    symbol: p.symbol,
    name: p.name || p.symbol,
    compactName: p.name || p.symbol,
    exchange: "",
    symbolType: "CRYPTO_CURRENCY",
    currency: p.priceCurrency ?? "",
    lastPrice: p.lastPrice ?? null,
    marketCap: null,
  };
}

/** Everything "what do I hold" means, in one place: the Holdings page and the
 * Dashboard's compact view both read this so they can never disagree. */
export function useHoldings() {
  const { positions, revolut, taxes } = useAppData();
  const marketCapFor = useMarketCap();

  // One row per broker position, in exporter order (DEGIRO sorts by value,
  // descending), Revolut crypto after it.
  const stocks: Stock[] = useMemo(
    () =>
      [
        ...(positions?.positions ?? []).map(stockFromPosition),
        ...(revolut?.positions ?? []).map(stockFromRevolutPosition),
      ].map((s) => ({ ...s, marketCap: marketCapFor(s.symbol) })),
    [positions, revolut, marketCapFor],
  );

  const showPositions = positions !== null;

  const posBySymbol = useMemo(() => {
    const map = new Map<string, Position>();
    for (const p of positions?.positions ?? []) map.set(p.symbol, p);
    return map;
  }, [positions]);

  // Open purchase lots per DEGIRO position, from the taxes.json trade ledger.
  const lotsBySymbol = useMemo(() => {
    const map = new Map<string, LotBreakdown>();
    const trades = taxes?.trades ?? [];
    if (trades.length === 0) return map;
    for (const p of positions?.positions ?? []) {
      const lots = buildLots(trades, p);
      if (lots) map.set(p.symbol, lots);
    }
    return map;
  }, [positions, taxes]);

  const revolutPosBySymbol = useMemo(() => {
    const map = new Map<string, RevolutPosition>();
    for (const p of revolut?.positions ?? []) map.set(p.symbol, p);
    return map;
  }, [revolut]);

  const valueBySymbol = useMemo(() => {
    const map = new Map<string, number>();
    for (const [symbol, p] of posBySymbol) map.set(symbol, p.valueEur);
    for (const [symbol, p] of revolutPosBySymbol)
      if (p.valueEur !== null) map.set(symbol, (map.get(symbol) ?? 0) + p.valueEur);
    return map;
  }, [posBySymbol, revolutPosBySymbol]);

  const revolutValueEur = useMemo(
    () => revolut?.positions.reduce((sum, p) => sum + (p.valueEur ?? 0), 0) ?? 0,
    [revolut],
  );

  // Revolut crypto P&L, aggregated across positions that carry a manual cost
  // basis (crypto_cost_basis.json). `undefined` when none is configured, so the
  // KPIs fall back to the DEGIRO-only figures.
  const revolutPl = useMemo(() => {
    const costed = revolut?.positions.filter((p) => p.plEur != null && p.costEur != null) ?? [];
    if (costed.length === 0) return undefined;
    return {
      plEur: costed.reduce((sum, p) => sum + (p.plEur ?? 0), 0),
      costEur: costed.reduce((sum, p) => sum + (p.costEur ?? 0), 0),
    };
  }, [revolut]);

  // Profit banked outside DEGIRO (Revolut crypto sales, staking) — only the
  // FIFO ledger in taxes.json remembers it once the position is closed.
  const externalRealized = useMemo(() => externalRealizedByPlatform(taxes), [taxes]);

  const totalCashEur = (positions?.summary.cashEur ?? 0) + (revolut?.totalCashEur ?? 0);

  return {
    positions,
    revolut,
    stocks,
    showPositions,
    posBySymbol,
    revolutPosBySymbol,
    lotsBySymbol,
    valueBySymbol,
    revolutValueEur,
    revolutPl,
    externalRealized,
    totalCashEur,
  };
}
