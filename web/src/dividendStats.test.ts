import { describe, expect, it } from "vitest";
import { monthWindow, monthlyIncome, payerTotals } from "./dividendStats";
import type { DividendPayment } from "./types";
import type { ProjectedPayment } from "./dividendForecast";

const pay = (date: string, symbol: string, net: number): DividendPayment => ({
  date, productId: 1, symbol, name: symbol, currency: "EUR", gross: net, tax: 0, net,
});

describe("monthWindow", () => {
  it("spans back and ahead across year boundaries", () => {
    const w = monthWindow("2026-02-10", 11, 11);
    expect(w).toHaveLength(23);
    expect(w[0]).toBe("2025-03");
    expect(w[11]).toBe("2026-02");
    expect(w[22]).toBe("2027-01");
  });
});

describe("monthlyIncome", () => {
  it("buckets paid and projected net and ignores out-of-window dates", () => {
    const projected = [{ date: "2026-03-05", netEur: 7 }] as ProjectedPayment[];
    const b = monthlyIncome(
      [pay("2026-02-03", "A", 10), pay("2026-02-20", "B", 5), pay("2020-01-01", "A", 99)],
      projected,
      "2026-02-10",
    );
    expect(b.find((x) => x.month === "2026-02")?.paid).toBe(15);
    expect(b.find((x) => x.month === "2026-03")?.projected).toBe(7);
    expect(b.reduce((s, x) => s + x.paid, 0)).toBe(15);
  });
});

describe("payerTotals", () => {
  it("sorts by net and computes shares", () => {
    const r = payerTotals([pay("2026-01-01", "A", 10), pay("2026-02-01", "B", 30), pay("2026-03-01", "A", 10)]);
    expect(r.map((x) => x.symbol)).toEqual(["B", "A"]);
    expect(r[0].share).toBeCloseTo(0.6);
    expect(r[1].count).toBe(2);
  });
});
