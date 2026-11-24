import { describe, expect, it } from "vitest";
import { spentThroughDay, upcomingSummary } from "./dashboardMath";
import type { RevolutTransaction, UpcomingDividend } from "./types";

const tx = (date: string, amountEur: number, kind: RevolutTransaction["kind"] = "expense") =>
  ({ date, amountEur, kind }) as RevolutTransaction;

describe("spentThroughDay", () => {
  const txs = [
    tx("2026-10-02T10:00:00", -40),
    tx("2026-10-10T10:00:00", -60),
    tx("2026-10-12T10:00:00", 10, "refund"),
    tx("2026-10-20T10:00:00", -500),
    tx("2026-10-05T10:00:00", 2000, "income"),
    tx("2026-10-06T10:00:00", -300, "transfer"),
    tx("2026-11-01T10:00:00", -99),
  ];
  it("sums net expenses up to and including the day", () => {
    expect(spentThroughDay(txs, "2026-10", 10)).toBe(100);
    expect(spentThroughDay(txs, "2026-10", 12)).toBe(90);
  });
  it("ignores other months and non-spending kinds", () => {
    expect(spentThroughDay(txs, "2026-10", 31)).toBe(590);
    expect(spentThroughDay(txs, "2026-09", 31)).toBe(0);
  });
});

describe("upcomingSummary", () => {
  const u = (payDate: string | null, amountEur: number | null) => ({ payDate, amountEur }) as UpcomingDividend;
  it("sorts by date, drops undated, totals known amounts", () => {
    const s = upcomingSummary([u("2026-12-01", 5), u(null, 9), u("2026-11-01", 2.5), u("2026-11-20", null)]);
    expect(s.rows.map((r) => r.payDate)).toEqual(["2026-11-01", "2026-11-20", "2026-12-01"]);
    expect(s.totalEur).toBe(7.5);
    expect(s.hasUnknown).toBe(true);
  });
  it("handles a missing list", () => {
    expect(upcomingSummary(undefined)).toEqual({ rows: [], totalEur: 0, hasUnknown: false });
  });
});
