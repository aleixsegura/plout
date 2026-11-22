import { describe, expect, it } from "vitest";
import { forecastDividends, parseUpcomingDescription } from "./dividendForecast";
import type { DividendsFile, Position, TaxTrade } from "./types";

function position(over: Partial<Position>): Position {
  return {
    id: "1",
    symbol: "X",
    name: "X Corp",
    isin: "US0000000001",
    quantity: 100,
    avgPrice: 10,
    currency: "USD",
    lastPrice: 10,
    valueEur: 900, // 1 USD = 0.9 EUR
    costEur: 900,
    plEur: 0,
    todayPlEur: 0,
    averageFxRate: null,
    exchange: "NSY",
    ...over,
  };
}

function buy(date: string, quantity: number, productId = "1"): TaxTrade {
  return {
    id: null,
    platform: "DEGIRO",
    date,
    side: "buy",
    productId,
    symbol: "X",
    name: "X Corp",
    assetType: "stock",
    currency: "USD",
    quantity,
    price: 10,
    totalEur: -9 * quantity,
    totalPlusFeesEur: -9 * quantity - 1,
    feesEur: 1,
  };
}

function file(over: Partial<DividendsFile>): DividendsFile {
  return { updatedAt: "", source: "", fromDate: "", toDate: "", payments: [], upcoming: [], ...over };
}

describe("parseUpcomingDescription", () => {
  it("reads DEGIRO's per-share × shares text", () => {
    expect(parseUpcomingDescription("Dividendo 1.0300 * 19.00 Acciones")).toEqual({ perShare: 1.03, shares: 19 });
    expect(parseUpcomingDescription("Retención del dividendo -0.1547 * 19.00 acciones")).toEqual({
      perShare: 0.1547,
      shares: 19,
    });
    expect(parseUpcomingDescription("Dividendo trimestral")).toBeNull();
  });
});

describe("forecastDividends", () => {
  it("divides by the shares held on the pay date and rolls last year forward", () => {
    // 60 shares when the interim was paid, 100 now.
    const trades = [buy("2026-01-10", 60), buy("2026-10-01", 40)];
    const dividends = file({
      payments: [
        { date: "2026-04-08", productId: 1, symbol: "X", name: "X", currency: "USD", gross: 120, tax: -18, net: 102 },
        { date: "2026-08-25", productId: 1, symbol: "X", name: "X", currency: "USD", gross: 30, tax: -4.5, net: 25.5 },
      ],
    });
    const f = forecastDividends([position({})], dividends, trades, "2026-11-22");
    expect(f.payments.map((p) => p.date)).toEqual(["2027-04-08", "2027-08-25"]);
    expect(f.payments[0].perShare).toBeCloseTo(2);
    expect(f.payments[0].gross).toBeCloseTo(200);
    expect(f.payments[1].gross).toBeCloseTo(50);
    const h = f.holdings[0];
    expect(h.frequency).toBe(2);
    expect(h.annualGross).toBeCloseTo(250);
    expect(h.annualGrossEur).toBeCloseTo(225);
    expect(h.annualNetEur).toBeCloseTo(225 * 0.85);
  });

  it("takes an announced payment as fact and assumes quarterly for a lone US payer", () => {
    const dividends = file({
      upcoming: [
        { payDate: "2026-11-13", name: "X Corp, Inc. Class A", description: "Dividendo 1.0300 * 19.00 Acciones", currency: "USD", amount: 19.57, amountEur: 17.5 },
        { payDate: "2026-11-13", name: "X Corp, Inc. Class A", description: "Retención del dividendo -0.1545 * 19.00 acciones", currency: "USD", amount: -2.94, amountEur: -2.6 },
      ],
    });
    const p = position({ quantity: 19, valueEur: 171 });
    const f = forecastDividends([p], dividends, [buy("2026-09-08", 19)], "2026-11-22");
    const h = f.holdings[0];
    expect(h.frequencyAssumed).toBe(true);
    expect(h.frequency).toBe(4);
    expect(f.payments).toHaveLength(4);
    expect(h.annualPerShare).toBeCloseTo(4.12);
    expect(h.withholdingRate).toBeCloseTo(0.15);
  });

  it("lists a future announced payment and does not double it with last year's", () => {
    const dividends = file({
      payments: [
        { date: "2025-12-15", productId: 1, symbol: "X", name: "X", currency: "EUR", gross: 100, tax: 0, net: 100 },
      ],
      upcoming: [
        { payDate: "2026-12-14", name: "X Corp", description: "Dividendo 1.2 * 100 Acciones", currency: "EUR", amount: 120, amountEur: 120 },
      ],
    });
    const p = position({ isin: "DE0000000001", currency: "EUR", valueEur: 1000 });
    const f = forecastDividends([p], dividends, [], "2026-11-22");
    expect(f.payments).toHaveLength(1);
    expect(f.payments[0]).toMatchObject({ date: "2026-12-14", announced: true });
    expect(f.payments[0].grossEur).toBeCloseTo(120);
  });

  it("skips holdings with no dividend record and stale payers", () => {
    const dividends = file({
      payments: [
        { date: "2023-05-01", productId: 1, symbol: "X", name: "X", currency: "USD", gross: 10, tax: 0, net: 10 },
      ],
    });
    const f = forecastDividends([position({}), position({ id: "2", symbol: "Y", name: "Y" })], dividends, [], "2026-11-22");
    expect(f.payments).toHaveLength(0);
    expect(f.annualGrossEur).toBe(0);
  });
});
