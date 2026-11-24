import { describe, expect, it } from "vitest";
import { holdingsTotals, rowFigures } from "./holdingsTotals";
import type { Position, RevolutPosition } from "./types";

const pos = { valueEur: 110, plEur: 10, costEur: 100, todayPlEur: 2 } as Position;
const coin = { valueEur: 500, quantity: 1 } as RevolutPosition;

describe("holdingsTotals", () => {
  it("sums value and today, and keeps basis-less rows out of P/L", () => {
    const t = holdingsTotals([rowFigures(pos, undefined), rowFigures(undefined, coin)]);
    expect(t.value).toBe(610);
    expect(t.today).toBe(2);
    expect(t.pl).toBe(10);
    expect(t.plPct).toBe(10);
  });
  it("has no percentage without any cost basis", () => {
    expect(holdingsTotals([rowFigures(undefined, coin)]).plPct).toBeNull();
    expect(holdingsTotals([]).value).toBe(0);
  });
});
