// Purchase lots still open in a DEGIRO position, rebuilt from the trade ledger
// in taxes.json. DEGIRO itself only reports the position's average price, so
// the per-purchase split comes from replaying every buy and sell in date order
// and letting sells consume the oldest shares first (FIFO — the method the
// Spanish tax return uses, so these lots are the ones a sale would report).

import type { Position, TaxTrade } from "./types";

/** Shares this small are rounding noise left by fractional quantities. */
const EPS = 1e-9;

export interface Lot {
  date: string;
  quantity: number;
  /** Purchase price per share, in the product's own currency. */
  price: number;
  currency: string;
  /** What these shares cost in EUR at the trade's FX, commissions excluded
   * (DEGIRO's P/L leaves them out too). */
  costEur: number;
  /** Purchase commissions for these shares, pro rata. */
  feesEur: number;
  /** The position's current EUR value, pro rata by quantity. */
  valueEur: number;
  plEur: number;
  /** plEur / costEur, as a fraction; null when the lot cost nothing. */
  plPct: number | null;
}

export interface LotBreakdown {
  lots: Lot[];
  /** Shares DEGIRO holds that the ledger has no buy for — bought after the
   * last `plout taxes` run, or moved in from another broker. */
  unmatchedQuantity: number;
  /** Shares the ledger still holds that DEGIRO no longer does — sold after the
   * last `plout taxes` run. Already taken out of the oldest lots. */
  soldSinceLedger: number;
  /** Some shares were sold along the way, so FIFO and DEGIRO's average cost
   * no longer agree on what the remaining shares cost. */
  hadSells: boolean;
}

/** The ledger's trades for one DEGIRO position. Product id is the exact key;
 * ISIN and then symbol cover ledgers written before it was recorded. */
export function tradesForPosition(trades: TaxTrade[], position: Position): TaxTrade[] {
  const degiro = trades.filter((t) => t.platform.toLowerCase() === "degiro");
  const byId = degiro.filter((t) => t.productId != null && String(t.productId) === String(position.id));
  if (byId.length > 0) return byId;
  if (position.isin) {
    const byIsin = degiro.filter((t) => t.isin === position.isin);
    if (byIsin.length > 0) return byIsin;
  }
  return degiro.filter((t) => t.symbol === position.symbol);
}

/** Ledger order: by date, and on the same day buys before sells — a sell can't
 * close shares that weren't there yet. */
function chronological(trades: TaxTrade[]): TaxTrade[] {
  return [...trades].sort(
    (a, b) => a.date.localeCompare(b.date) || (a.side === b.side ? 0 : a.side === "buy" ? -1 : 1),
  );
}

interface OpenLot {
  date: string;
  quantity: number;
  price: number;
  currency: string;
  costPerShare: number;
  feesPerShare: number;
}

function consume(open: OpenLot[], quantity: number): void {
  let left = quantity;
  while (left > EPS && open.length > 0) {
    const lot = open[0];
    const take = Math.min(lot.quantity, left);
    lot.quantity -= take;
    left -= take;
    if (lot.quantity <= EPS) open.shift();
  }
}

/** Shares of `trades` held at the end of `date` (inclusive), whatever order
 * the ledger lists them in. */
export function quantityHeldOn(trades: TaxTrade[], date: string): number {
  let qty = 0;
  for (const t of trades) {
    if (t.date.slice(0, 10) > date) continue;
    qty += t.side === "buy" ? t.quantity : -t.quantity;
  }
  return Math.max(qty, 0);
}

export function buildLots(trades: TaxTrade[], position: Position): LotBreakdown | null {
  const own = chronological(tradesForPosition(trades, position));
  if (own.length === 0 || position.quantity <= 0) return null;

  const open: OpenLot[] = [];
  for (const t of own) {
    if (t.quantity <= 0) continue;
    if (t.side === "buy") {
      open.push({
        date: t.date.slice(0, 10),
        quantity: t.quantity,
        price: t.price,
        currency: t.currency,
        // The ledger books buys negative; the magnitude is the cost.
        costPerShare: Math.abs(t.totalEur) / t.quantity,
        feesPerShare: Math.abs(t.feesEur) / t.quantity,
      });
    } else {
      consume(open, t.quantity);
    }
  }

  const ledgerQty = open.reduce((sum, l) => sum + l.quantity, 0);
  let soldSinceLedger = 0;
  if (ledgerQty > position.quantity + EPS) {
    soldSinceLedger = ledgerQty - position.quantity;
    consume(open, soldSinceLedger);
  }
  const unmatchedQuantity = Math.max(position.quantity - ledgerQty, 0);
  if (open.length === 0) return null;

  const valuePerShare = position.valueEur / position.quantity;
  const lots = open.map((l): Lot => {
    const costEur = l.costPerShare * l.quantity;
    const valueEur = valuePerShare * l.quantity;
    const plEur = valueEur - costEur;
    return {
      date: l.date,
      quantity: l.quantity,
      price: l.price,
      currency: l.currency,
      costEur,
      feesEur: l.feesPerShare * l.quantity,
      valueEur,
      plEur,
      plPct: costEur > 0 ? plEur / costEur : null,
    };
  });
  return {
    lots,
    unmatchedQuantity: unmatchedQuantity > EPS ? unmatchedQuantity : 0,
    soldSinceLedger: soldSinceLedger > EPS ? soldSinceLedger : 0,
    hadSells: soldSinceLedger > EPS || own.some((t) => t.side === "sell"),
  };
}
