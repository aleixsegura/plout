import type { Stock } from "./types";
import type { Translations } from "./i18n/en";
import { symbolConfig } from "./symbolConfig";

// Sectors are NOT present in the exported data (DEGIRO's positions carry no
// sector field). Until the exporter enriches each symbol with a sector — most
// likely via FMP's company profile — holdings are classified by hand in
// symbols.json, falling back to symbolType for crypto and currency positions.
//
// SECTOR_ORDER fixes each sector's identity and tie-break order. Colors follow
// the rank in the chart (largest slice = --sector-1, ...), so the biggest
// slices always get neighbouring tones of the gradient in index.css.
export const SECTOR_ORDER = [
  "Technology",
  "Materials",
  "Utilities",
  "Crypto",
  "Energy",
  "Healthcare",
  "Consumer Staples",
  "Financials",
  "Industrials",
  "Bonds",
  "Currency / FX",
  "Cash",
] as const;

export type Sector = (typeof SECTOR_ORDER)[number] | "Other";

// Equities and ETFs carry no sector in the source data, so they're classified
// by hand in symbols.json (see symbolConfig.ts), keyed by DEGIRO's symbol.
// Unknown names → Other.
function configuredSector(symbol: string): Sector | undefined {
  const sector = symbolConfig().sectors[symbol];
  return (SECTOR_ORDER as readonly string[]).includes(sector) ? (sector as Sector) : undefined;
}

/** Display label for a sector, in the active language. The `Sector` type
 * itself stays English (it's a data-identity key: color slots, breakdown
 * grouping), so this is the only place a sector name reaches the UI. */
export function sectorLabel(t: Translations, sector: Sector): string {
  return t.sectors[sector];
}

export function sectorFor(stock: Stock): Sector {
  if (stock.symbolType === "CRYPTO_CURRENCY") return "Crypto";
  if (stock.symbolType === "CURRENCY") return "Currency / FX";
  return configuredSector(stock.symbol) ?? "Other";
}

/** CSS custom-property reference for a color slot, matching index.css. */
export function sectorVar(colorSlot: number): string {
  return colorSlot === 0 ? "var(--sector-other)" : `var(--sector-${colorSlot})`;
}

export interface SectorSlice {
  sector: Sector;
  count: number;
  /** Fraction of total holdings, 0–1. */
  fraction: number;
  /** EUR market value of the sector; null when weighting by count. */
  value: number | null;
  /** Color slot (1-based, by rank) matching --sector-N in CSS; 0 = Other. */
  colorSlot: number;
}

/**
 * Group stocks into sector slices, returned heaviest-weight first (the same
 * order the donut and its legend render), with any unclassified symbols folded
 * into an "Other" slice. Color slots follow this ranking.
 *
 * With `valueBySymbol` (EUR market value per symbol, from DEGIRO
 * and Revolut positions) slices are value-weighted; stocks without a value
 * are excluded entirely. Without it, every holding counts equally.
 *
 * `cashEur` (DEGIRO + Revolut cash) injects a stock-less "Cash" slice; it
 * only applies in value-weighted mode — cash has no meaningful "count".
 */
export function sectorBreakdown(
  stocks: Stock[],
  valueBySymbol?: Map<string, number>,
  cashEur?: number,
): SectorSlice[] {
  const byValue = valueBySymbol !== undefined && valueBySymbol.size > 0;
  const counts = new Map<Sector, number>();
  const values = new Map<Sector, number>();

  for (const s of stocks) {
    const value = byValue ? valueBySymbol.get(s.symbol) : undefined;
    if (byValue && value === undefined) continue;
    const sector = sectorFor(s);
    counts.set(sector, (counts.get(sector) ?? 0) + 1);
    values.set(sector, (values.get(sector) ?? 0) + (value ?? 0));
  }

  if (byValue && cashEur !== undefined && cashEur > 0) {
    values.set("Cash", cashEur);
  }

  const totalCount = [...counts.values()].reduce((a, b) => a + b, 0) || 1;
  const totalValue = [...values.values()].reduce((a, b) => a + b, 0) || 1;
  const slices: SectorSlice[] = [];

  const push = (sector: Sector, colorSlot: number) => {
    const count = counts.get(sector) ?? 0;
    const value = values.get(sector) ?? 0;
    // Cash is a value without a holding: keep zero-count slices that carry value.
    if (count === 0 && value === 0) return;
    slices.push({
      sector,
      count,
      fraction: byValue ? value / totalValue : count / totalCount,
      value: byValue ? value : null,
      colorSlot,
    });
  };

  SECTOR_ORDER.forEach((sector, i) => push(sector, i + 1));
  push("Other", 0);

  // Heaviest sector first; ties keep SECTOR_ORDER (Other last) so the order is
  // stable across renders. Colors are then dealt out by rank; "Other" stays
  // neutral. Past the ten slots the ramp wraps.
  slices.sort((a, b) => b.fraction - a.fraction);
  let rank = 0;
  for (const s of slices) s.colorSlot = s.sector === "Other" ? 0 : (rank++ % 10) + 1;
  return slices;
}
