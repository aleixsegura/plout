import { describe, expect, it } from "vitest";
import { buildLots } from "./lots";
import type { Position, TaxTrade } from "./types";

function trade(date: string, side: "buy" | "sell", quantity: number, price: number): TaxTrade {
  return {
    id: null,
    platform: "DEGIRO",
    date,
    side,
    productId: "7",
    symbol: "X",
    name: "X",
    assetType: "stock",
    currency: "EUR",
    quantity,
    price,
    totalEur: (side === "buy" ? -1 : 1) * price * quantity,
    totalPlusFeesEur: 0,
    feesEur: 2,
  };
}

function position(quantity: number, valueEur: number): Position {
  return {
    id: "7",
    symbol: "X",
    name: "X",
    quantity,
    avgPrice: 0,
    currency: "EUR",
    lastPrice: valueEur / quantity,
    valueEur,
    costEur: 0,
    plEur: 0,
    todayPlEur: 0,
    averageFxRate: null,
    exchange: null,
  };
}

describe("buildLots", () => {
  it("lets sells eat the oldest shares first", () => {
    const trades = [
      trade("2026-03-01", "buy", 10, 20),
      trade("2026-01-01", "buy", 10, 10),
      trade("2026-05-01", "sell", 15, 30),
    ];
    const b = buildLots(trades, position(5, 125))!;
    expect(b.lots).toHaveLength(1);
    expect(b.lots[0]).toMatchObject({ date: "2026-03-01", quantity: 5, price: 20 });
    expect(b.lots[0].costEur).toBeCloseTo(100);
    expect(b.lots[0].valueEur).toBeCloseTo(125);
    expect(b.lots[0].plEur).toBeCloseTo(25);
    expect(b.lots[0].plPct).toBeCloseTo(0.25);
    expect(b.lots[0].feesEur).toBeCloseTo(1);
    expect(b.unmatchedQuantity).toBe(0);
  });

  it("reports shares the ledger doesn't know about yet", () => {
    const b = buildLots([trade("2026-01-01", "buy", 10, 10)], position(14, 140))!;
    expect(b.lots[0].quantity).toBe(10);
    expect(b.lots[0].valueEur).toBeCloseTo(100);
    expect(b.unmatchedQuantity).toBe(4);
  });

  it("takes later sales out of the oldest lots", () => {
    const trades = [trade("2026-01-01", "buy", 10, 10), trade("2026-02-01", "buy", 10, 12)];
    const b = buildLots(trades, position(12, 120))!;
    expect(b.soldSinceLedger).toBe(8);
    expect(b.lots.map((l) => [l.date, l.quantity])).toEqual([
      ["2026-01-01", 2],
      ["2026-02-01", 10],
    ]);
  });

  it("returns null when the ledger has nothing for the position", () => {
    expect(buildLots([], position(1, 1))).toBeNull();
  });
});
