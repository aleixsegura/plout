import { describe, expect, it } from "vitest";
import { benchmarkReturns, closeOn, depositsInBenchmark, performanceSeries, type PerformancePoint } from "./performance";
import type { HistoryDay } from "./types";

function day(
  date: string,
  degiro: { value: number; cash?: number; deposits?: number | null } | null,
  revolut: { crypto: number; savings: number; current?: number; invested?: number | null; interest?: number | null } | null,
): HistoryDay {
  return {
    date,
    degiro: degiro && {
      updatedAt: `${date}T18:00:00+00:00`,
      valueEur: degiro.value,
      cashEur: degiro.cash ?? 0,
      netDepositsEur: degiro.deposits === undefined ? 0 : degiro.deposits,
    },
    revolut: revolut && {
      updatedAt: `${date}T18:00:00+00:00`,
      cryptoValueEur: revolut.crypto,
      cashCurrentEur: revolut.current ?? 0,
      cashSavingsEur: revolut.savings,
      netInvestedEur: revolut.invested === undefined ? 0 : revolut.invested,
      interestEur: revolut.interest === undefined ? 0 : revolut.interest,
    },
    portfolioValueEur: 0,
    cashEur: 0,
    totalEur: 0,
  };
}

describe("performanceSeries", () => {
  it("ignores salary moving into the current and savings accounts", () => {
    const series = performanceSeries([
      day("2026-07-01", { value: 1000, deposits: 1000 }, { crypto: 0, savings: 5000, current: 100 }),
      day("2026-08-01", { value: 1000, deposits: 1000 }, { crypto: 0, savings: 7500, current: 2600, interest: 10 }),
    ]);
    // Only the 10 € of interest is return; the 2,500 € transfer and salary aren't.
    expect(series[series.length - 1].plEur).toBeCloseTo(10);
  });

  it("subtracts DEGIRO deposits from the change in value", () => {
    const series = performanceSeries([
      day("2026-07-01", { value: 900, cash: 100, deposits: 1000 }, null),
      day("2026-08-01", { value: 2100, cash: 100, deposits: 2000 }, null),
    ]);
    expect(series.map((p) => p.plEur)).toEqual([0, 200]);
    // Modified Dietz: 200 / (1000 + 1000/2).
    expect(series[1].pct).toBeCloseTo((200 / 1500) * 100);
  });

  it("treats a crypto sale as money out, not a loss", () => {
    const series = performanceSeries([
      day("2026-09-01", null, { crypto: 10000, savings: 0, invested: 0 }),
      day("2026-09-15", null, { crypto: 3500, savings: 6700, invested: -6700 }),
    ]);
    // Value went 10,000 → 3,500 crypto, but 6,700 came out: +200 return.
    expect(series[1].plEur).toBeCloseTo(200);
  });

  it("chains interval returns into a time-weighted %", () => {
    const series = performanceSeries([
      day("2026-07-01", { value: 1000, deposits: 1000 }, null),
      day("2026-08-01", { value: 1100, deposits: 1000 }, null),
      // Deposit 10,000 then flat: a deposit must not move the %.
      day("2026-09-01", { value: 11100, deposits: 11000 }, null),
    ]);
    expect(series[1].pct).toBeCloseTo(10);
    expect(series[2].pct).toBeCloseTo(10);
    expect(series[2].plEur).toBeCloseTo(100);
  });

  it("starts at the first day with running totals", () => {
    const series = performanceSeries([
      day("2026-07-01", { value: 1000, deposits: null }, null),
      day("2026-08-01", { value: 1100, deposits: 1000 }, null),
      day("2026-09-01", { value: 1200, deposits: 1000 }, null),
    ]);
    expect(series.map((p) => p.date)).toEqual(["2026-08-01", "2026-09-01"]);
    expect(series[1].plEur).toBeCloseTo(100);
  });

  it("counts a source that appears as a flow, not return", () => {
    const series = performanceSeries([
      day("2026-07-01", { value: 1000, deposits: 1000 }, null),
      day("2026-08-01", { value: 1000, deposits: 1000 }, { crypto: 5000, savings: 0, invested: 0 }),
    ]);
    expect(series[1].plEur).toBe(0);
  });
});

describe("closeOn", () => {
  const line = { dates: ["2026-08-03", "2026-08-04", "2026-08-07"], closesEur: [10, 11, 12] };
  it("takes the last close on or before the day", () => {
    expect(closeOn(line, "2026-08-04")).toBe(11);
    expect(closeOn(line, "2026-08-06T22:00:00+00:00")).toBe(11);
    expect(closeOn(line, "2026-09-01")).toBe(12);
    expect(closeOn(line, "2026-08-01")).toBeNull();
  });
});

describe("benchmarkReturns", () => {
  const point = (date: string, valueEur: number, flowEur: number): PerformancePoint => ({
    date,
    plEur: 0,
    pct: 0,
    valueEur,
    flowEur,
  });

  it("buys the starting value and each flow, then marks to market", () => {
    const line = { dates: ["2026-07-01", "2026-08-01", "2026-09-01"], closesEur: [100, 100, 110] };
    const out = benchmarkReturns(
      [point("2026-07-01", 1000, 0), point("2026-08-01", 2000, 1000), point("2026-09-01", 2000, 0)],
      line,
    )!;
    // 20 units bought at 100, worth 2,200 against 2,000 put in.
    expect(out[0]).toBe(0);
    expect(out[1]).toBeCloseTo(0);
    expect(out[2]).toBeCloseTo(200);
  });

  it("is null when the index has no price for the first snapshot", () => {
    expect(benchmarkReturns([point("2026-07-01", 1000, 0)], { dates: ["2026-08-01"], closesEur: [1] })).toBeNull();
  });
});

describe("depositsInBenchmark", () => {
  const line = { dates: ["2025-04-01", "2026-01-01", "2026-10-01"], closesEur: [100, 120, 150] };
  const move = (date: string, amountEur: number) => ({
    date,
    kind: amountEur >= 0 ? ("deposit" as const) : ("withdrawal" as const),
    description: "",
    currency: "EUR",
    amount: amountEur,
    amountEur,
  });

  it("puts each deposit in the index on its day and withdrawals sell it back", () => {
    const res = depositsInBenchmark(
      [move("2025-04-03T10:00:00+02:00", 1000), move("2026-01-05T10:00:00+01:00", 1200), move("2026-10-02T10:00:00+02:00", -300)],
      line,
      "2026-10-27T23:00:00+00:00",
    )!;
    // 10 + 10 − 2 units, worth 18 × 150.
    expect(res.deposited).toBe(1900);
    expect(res.valueEur).toBeCloseTo(2700);
    expect(res.plEur).toBeCloseTo(800);
  });

  it("is null when a deposit predates the index", () => {
    expect(depositsInBenchmark([move("2025-01-01", 100)], line, "2026-10-27")).toBeNull();
  });
});
