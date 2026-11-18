import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { assetContext, loadExports, portfolioContext, symbolKey } from "./portfolioContext";

const DEMO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../demo-data");
const ex = loadExports(DEMO);

describe("symbolKey", () => {
  it("treats a watchlist spelling and a DEGIRO spelling as the same asset", () => {
    expect(symbolKey("XMPL.DE")).toBe(symbolKey("xmpl"));
    expect(symbolKey("VWCE.DE")).not.toBe(symbolKey("VWRL"));
  });
});

describe("portfolioContext", () => {
  const ctx = portfolioContext(ex);

  it("weights sum to the whole invested value", () => {
    const sum = ctx.holdings.reduce((s, h) => s + (h.pctOfInvested ?? 0), 0);
    expect(sum).toBeCloseTo(100, 0);
  });

  it("orders holdings heaviest first and reports a consistent HHI", () => {
    const values = ctx.holdings.map((h) => h.valueEur);
    expect(values).toEqual([...values].sort((a, b) => b - a));
    const hhi = ctx.holdings.reduce((s, h) => s + ((h.pctOfInvested ?? 0) / 100) ** 2, 0);
    expect(ctx.concentration.hhi).toBeCloseTo(hhi, 3);
    expect(ctx.concentration.top1PctOfInvested).toBe(ctx.holdings[0].pctOfInvested);
  });
});

describe("assetContext", () => {
  it("resolves a held asset by either spelling", () => {
    const a = assetContext(ex, "VWCE");
    const b = assetContext(ex, "VWCE.DE");
    expect(a?.position?.valueEur).toBeGreaterThan(0);
    expect(a?.position?.valueEur).toBe(b?.position?.valueEur);
  });

  it("skips market cap for ETFs and lists the single names held for overlap", () => {
    const etf = assetContext(ex, "VWCE.DE");
    expect(etf?.marketCap).toBeNull();
    expect(etf?.otherSingleNamesHeld?.length).toBeGreaterThan(0);
  });

  it("returns null for something neither held nor watched", () => {
    expect(assetContext(ex, "NOT-A-SYMBOL")).toBeNull();
  });
});
