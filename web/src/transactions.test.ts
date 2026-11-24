import { describe, expect, it } from "vitest";
import { daysSinceFirst, localDay, netOfRows } from "./transactions";
import type { RevolutTransaction } from "./types";

const tx = (date: string, amountEur: number, kind: RevolutTransaction["kind"] = "expense") =>
  ({ date, amountEur, kind }) as RevolutTransaction;

describe("localDay", () => {
  it("keeps the calendar day regardless of timezone", () => {
    const d = localDay("2026-03-05T23:30:00");
    expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2026, 2, 5]);
  });
});

describe("daysSinceFirst", () => {
  it("counts inclusive calendar days from the earliest row", () => {
    const rows = [tx("2026-03-10T09:00:00", -1), tx("2026-03-01T22:00:00", -1)];
    expect(daysSinceFirst(rows, new Date(2026, 2, 10, 8))).toBe(10);
  });
  it("is 1 with no rows", () => expect(daysSinceFirst([])).toBe(1));
});

describe("netOfRows", () => {
  const rows = [tx("2026-03-01", -10), tx("2026-03-02", 25, "income"), tx("2026-03-03", -100, "transfer")];
  it("skips internal moves by default", () => expect(netOfRows(rows)).toBe(15));
  it("can include them", () => expect(netOfRows(rows, true)).toBe(-85));
});
