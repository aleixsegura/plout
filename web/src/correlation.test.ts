import { describe, expect, it } from "vitest";
import {
  alignReturns,
  calendar,
  clusterOrder,
  computeStats,
  concentration,
  contiguousOrder,
  correlatedClusters,
  diversification,
  inverseVolatilityWeights,
  marketGraph,
  maximumIndependentSet,
  riskContributions,
  shrinkCorrelation,
  type PriceSeriesInput,
  type ReturnsPanel,
} from "./correlation";

/** A deterministic pseudo-random walk, so tests don't depend on Math.random. */
function noise(seed: number, n: number): number[] {
  let s = seed;
  return Array.from({ length: n }, () => {
    s = (s * 1103515245 + 12345) % 2 ** 31;
    return s / 2 ** 31 - 0.5;
  });
}

function panelOf(returns: number[][]): ReturnsPanel {
  return {
    symbols: returns.map((_, i) => `S${i}`),
    dates: returns[0].map((_, t) => `d${t}`),
    returns,
    excluded: [],
  };
}

describe("calendar", () => {
  it("skips weekends, or keeps only Fridays", () => {
    // 2024-01-05 is a Friday.
    expect(calendar("2024-01-05", "2024-01-09", "daily")).toEqual([
      "2024-01-05",
      "2024-01-08",
      "2024-01-09",
    ]);
    expect(calendar("2024-01-01", "2024-01-14", "weekly")).toEqual(["2024-01-05", "2024-01-12"]);
  });
});

describe("alignReturns", () => {
  it("folds a weekend move into Monday's return", () => {
    const dates = calendar("2023-01-02", "2024-01-08", "daily");
    const crypto: PriceSeriesInput = { dates: [], closesEur: [] };
    // Daily series including weekends: price doubles over one weekend only.
    for (let ms = Date.parse("2023-01-02T00:00:00Z"); ms <= Date.parse("2024-01-08T00:00:00Z"); ms += 86_400_000) {
      const day = new Date(ms).toISOString().slice(0, 10);
      crypto.dates.push(day);
      crypto.closesEur.push(day >= "2024-01-06" ? 200 : 100);
    }
    const flat = { dates, closesEur: dates.map(() => 50) };
    const panel = alignReturns({ BTC: crypto, ETF: flat }, ["BTC", "ETF"], 1, "daily");
    const btc = panel.returns[panel.symbols.indexOf("BTC")];
    expect(panel.dates[panel.dates.length - 1]).toBe("2024-01-08");
    expect(btc[btc.length - 1]).toBeCloseTo(Math.log(2));
    expect(btc.slice(0, -1).every((r) => r === 0)).toBe(true);
  });

  it("excludes short histories and missing series, keeps a late listing's nulls", () => {
    const dates = calendar("2023-01-02", "2024-12-31", "daily");
    const long = { dates, closesEur: dates.map((_, i) => 100 + i) };
    const lateDates = dates.slice(-200);
    const late = { dates: lateDates, closesEur: lateDates.map((_, i) => 10 + i) };
    const tiny = { dates: dates.slice(-20), closesEur: dates.slice(-20).map(() => 1) };
    const panel = alignReturns({ A: long, B: late, C: tiny }, ["A", "B", "C", "D"], 2, "daily");
    expect(panel.symbols).toEqual(["A", "B"]);
    expect(panel.excluded.sort()).toEqual(["C", "D"]);
    expect(panel.returns[1][0]).toBeNull();
  });
});

describe("computeStats", () => {
  const a = noise(1, 300);
  const b = noise(2, 300);

  it("gives 1 for identical and −1 for opposite series", () => {
    const { corr } = computeStats(panelOf([a, a, a.map((x) => -x)]), "daily");
    expect(corr[0][1]).toBeCloseTo(1);
    expect(corr[0][2]).toBeCloseTo(-1);
  });

  it("is symmetric with a unit diagonal and counts the pair overlap", () => {
    const withGap = b.map((x, t) => (t < 100 ? null : x));
    const { corr, overlap } = computeStats(panelOf([a, withGap as number[]]), "daily");
    expect(corr[0][0]).toBe(1);
    expect(corr[0][1]).toBe(corr[1][0]);
    expect(overlap[0][1]).toBe(200);
  });

  it("builds the covariance on the shrunk correlation, leaving corr as observed", () => {
    const stats = computeStats(panelOf([a, b, a.map((x, t) => x + b[t])]), "daily");
    const { corr: shrunk, intensity } = stats.shrinkage;
    expect(intensity).toBeGreaterThan(0);
    expect(stats.corr[0][2]).not.toBeCloseTo(shrunk[0][2], 6);
    expect(stats.cov[0][2]).toBeCloseTo(shrunk[0][2] * stats.sigma[0] * stats.sigma[2]);
    expect(stats.cov[1][1]).toBeCloseTo(stats.sigma[1] ** 2);
  });

  it("annualizes volatility with √252", () => {
    const { sigma } = computeStats(panelOf([a]), "daily");
    const mean = a.reduce((s, x) => s + x, 0) / a.length;
    const sd = Math.sqrt(a.reduce((s, x) => s + (x - mean) ** 2, 0) / (a.length - 1));
    expect(sigma[0]).toBeCloseTo(sd * Math.sqrt(252));
  });
});

describe("shrinkCorrelation", () => {
  const quadratic = (m: number[][], w: number[]) =>
    w.reduce((s, wi, i) => s + w.reduce((t, wj, j) => t + wi * m[i][j] * wj, 0), 0);

  it("shrinks noisy correlations hard and well-measured ones barely", () => {
    // Ten unrelated assets, 60 returns: every sample correlation is noise.
    const short = computeStats(panelOf(Array.from({ length: 10 }, (_, k) => noise(k + 1, 60))), "weekly");
    expect(short.shrinkage.intensity).toBeGreaterThan(0.7);
    // Two tight blocks over 3,000 returns: the structure is real.
    const [f, g] = [noise(11, 3000), noise(12, 3000)];
    const blocks = [f, f, g, g].map((base, k) => {
      const own = noise(20 + k, 3000);
      return base.map((x, t) => x + 0.3 * own[t]);
    });
    expect(computeStats(panelOf(blocks), "daily").shrinkage.intensity).toBeLessThan(0.05);
  });

  it("moves every pair toward the average and fills missing ones with it", () => {
    const corr = [
      [1, 0.8, NaN],
      [0.8, 1, 0.2],
      [NaN, 0.2, 1],
    ];
    const v = corr.map((row) => row.map(() => 0.01));
    const { target, intensity, corr: shrunk } = shrinkCorrelation(corr, v);
    expect(target).toBeCloseTo(0.5);
    expect(intensity).toBeCloseTo(0.02 / 0.18);
    expect(shrunk[0][1]).toBeCloseTo(0.8 - intensity * 0.3);
    expect(shrunk[0][2]).toBe(target);
    expect(shrunk[1][1]).toBe(1);
  });

  it("raises λ until an inconsistent matrix becomes valid", () => {
    // 0 and 2 both track 1 closely yet move opposite each other: impossible.
    const corr = [
      [1, 0.9, -0.9],
      [0.9, 1, 0.9],
      [-0.9, 0.9, 1],
    ];
    const zero = corr.map((row) => row.map(() => 0));
    const w = [1, -1, 1];
    expect(quadratic(corr, w)).toBeLessThan(0);
    const { target, intensity, corr: shrunk } = shrinkCorrelation(corr, zero);
    expect(intensity).toBeGreaterThan(0);
    expect(quadratic(shrunk, w)).toBeGreaterThanOrEqual(-1e-6);
    // …and no further than needed: just below it the matrix is still invalid.
    const lambda = intensity - 0.01;
    const less = corr.map((row, i) => row.map((r, j) => (i === j ? 1 : (1 - lambda) * r + lambda * target)));
    expect(quadratic(less, w)).toBeLessThan(0);
  });
});

describe("market graph and MIS", () => {
  // Fig. 1 of the paper: N = 6, MIS {b, c, e}. Size-3 sets tie there ({a, b, e}
  // too), so the paper's pick is given the lowest internal correlation.
  const names = ["a", "b", "c", "d", "e", "f"];
  const links = [["a", "f"], ["a", "d"], ["a", "c"], ["f", "b"], ["f", "d"], ["b", "d"], ["e", "d"]];
  const mis = ["b", "c", "e"];
  const corr = names.map((x, i) =>
    names.map((y, j) => {
      if (i === j) return 1;
      if (links.some(([p, q]) => (p === x && q === y) || (p === y && q === x))) return 0.8;
      return mis.includes(x) && mis.includes(y) ? 0 : 0.1;
    }),
  );

  it("builds edges at C ≥ θ", () => {
    const graph = marketGraph(corr, 0.5);
    expect(graph.edges).toHaveLength(7);
    expect(graph.degree[names.indexOf("d")]).toBe(4);
    expect(graph.density).toBeCloseTo(7 / 15);
  });

  it("finds the paper's MIS", () => {
    const { adjacency } = marketGraph(corr, 0.5);
    const set = maximumIndependentSet(adjacency, corr);
    expect(set.map((i) => names[i])).toEqual(mis);
    for (const i of set) for (const j of set) expect(adjacency[i][j]).toBe(false);
  });

  it("takes every node when θ is above all correlations", () => {
    const { adjacency } = marketGraph(corr, 0.95);
    expect(maximumIndependentSet(adjacency, corr)).toHaveLength(6);
  });

  it("breaks ties by the lowest internal correlation", () => {
    // Path 0–1–2 plus node 3 joined to everything: {0, 2} is the only maximum set.
    const c = [
      [1, 0.9, 0.3, 0.9],
      [0.9, 1, 0.9, 0.9],
      [0.3, 0.9, 1, 0.9],
      [0.9, 0.9, 0.9, 1],
    ];
    const { adjacency } = marketGraph(c, 0.5);
    expect(maximumIndependentSet(adjacency, c)).toEqual([0, 2]);
    // Two disjoint edges 0–1, 2–3: four maximum sets; {1, 2} has ρ = −0.5.
    const d = [
      [1, 0.9, 0.2, 0.1],
      [0.9, 1, -0.5, 0.0],
      [0.2, -0.5, 1, 0.9],
      [0.1, 0.0, 0.9, 1],
    ];
    expect(maximumIndependentSet(marketGraph(d, 0.5).adjacency, d)).toEqual([1, 2]);
  });

  it("prefers the heavier of equally large sets over the less correlated one", () => {
    // Same two disjoint edges: {1, 2} is the least correlated, {0, 3} the heaviest.
    const d = [
      [1, 0.9, 0.2, 0.1],
      [0.9, 1, -0.5, 0.0],
      [0.2, -0.5, 1, 0.9],
      [0.1, 0.0, 0.9, 1],
    ];
    const { adjacency } = marketGraph(d, 0.5);
    expect(maximumIndependentSet(adjacency, d, [0.4, 0.1, 0.1, 0.4])).toEqual([0, 3]);
    // Equal weights fall back to the correlation tie-break.
    expect(maximumIndependentSet(adjacency, d, [0.25, 0.25, 0.25, 0.25])).toEqual([1, 2]);
  });

  it("never trades size for weight", () => {
    // Star: the heavy hub against three light leaves; the leaves win on count.
    const c = [
      [1, 0.9, 0.9, 0.9],
      [0.9, 1, 0, 0],
      [0.9, 0, 1, 0],
      [0.9, 0, 0, 1],
    ];
    expect(maximumIndependentSet(marketGraph(c, 0.5).adjacency, c, [0.9, 0.03, 0.03, 0.04])).toEqual([1, 2, 3]);
  });
});

describe("clusterOrder", () => {
  it("puts correlated pairs next to each other", () => {
    const corr = [
      [1, 0.1, 0.9, 0.0],
      [0.1, 1, 0.0, 0.8],
      [0.9, 0.0, 1, 0.1],
      [0.0, 0.8, 0.1, 1],
    ];
    const order = clusterOrder(corr);
    expect(order).toHaveLength(4);
    expect(Math.abs(order.indexOf(0) - order.indexOf(2))).toBe(1);
    expect(Math.abs(order.indexOf(1) - order.indexOf(3))).toBe(1);
  });
});

describe("risk", () => {
  const sigma = [0.2, 0.3, 0.4];
  const statsFor = (rho: number) => {
    const corr = sigma.map((_, i) => sigma.map((_, j) => (i === j ? 1 : rho)));
    const cov = corr.map((row, i) => row.map((r, j) => r * sigma[i] * sigma[j]));
    return {
      sigma,
      corr,
      cov,
      overlap: corr.map((row) => row.map(() => 100)),
      shrinkage: { target: rho, intensity: 0, corr },
    };
  };

  it("risk contributions sum to 100%", () => {
    const rc = riskContributions([0.5, 0.3, 0.2], statsFor(0.4).cov);
    expect(rc.reduce((s, x) => s + x, 0)).toBeCloseTo(1);
  });

  it("has no diversification when everything moves in lockstep", () => {
    const d = diversification([0.5, 0.3, 0.2], statsFor(1));
    expect(d.ratio).toBeCloseTo(1);
    expect(d.effectiveBets).toBeCloseTo(1);
    expect(d.averageCorrelation).toBeCloseTo(1);
  });

  it("reaches √N bets for equal-risk uncorrelated assets", () => {
    const d = diversification(inverseVolatilityWeights(sigma), statsFor(0));
    expect(d.ratio).toBeCloseTo(Math.sqrt(3));
    expect(d.effectiveBets).toBeCloseTo(3);
    expect(d.effectiveHoldings).toBeLessThan(3);
  });

  it("inverse-volatility weights are ∝ 1/σ", () => {
    const w = inverseVolatilityWeights(sigma);
    expect(w[0] / w[1]).toBeCloseTo(1.5);
    expect(w.reduce((s, x) => s + x, 0)).toBeCloseTo(1);
  });
});

describe("contiguousOrder", () => {
  it("pulls each group together at its first member's place", () => {
    expect(contiguousOrder([4, 0, 3, 1, 2], [[1, 0]])).toEqual([4, 0, 1, 3, 2]);
    expect(contiguousOrder([0, 1, 2], [])).toEqual([0, 1, 2]);
  });
});

describe("concentration", () => {
  const symmetric = (n: number, pairs: [number, number, number][]) => {
    const corr = Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j): number => (i === j ? 1 : 0)));
    for (const [i, j, rho] of pairs) corr[i][j] = corr[j][i] = rho;
    return corr;
  };
  const statsOf = (corr: number[][], sigma: number[]) => ({
    sigma,
    corr,
    cov: corr.map((row, i) => row.map((r, j) => r * sigma[i] * sigma[j])),
    overlap: corr.map((row) => row.map(() => 100)),
    shrinkage: { target: 0, intensity: 0, corr },
  });

  it("keeps a block whose mean ρ clears the bar even if some pairs don't", () => {
    // 0–1 and 2–3 are tight, the cross pairs looser: mean (0.65+0.34+0.18+0.53+0.28+0.57)/6 = 0.425.
    const corr = symmetric(5, [
      [0, 1, 0.65],
      [0, 2, 0.34],
      [0, 3, 0.18],
      [1, 2, 0.53],
      [1, 3, 0.28],
      [2, 3, 0.57],
    ]);
    expect(correlatedClusters(corr, 0.4, 0.3).map((c) => [...c].sort())).toEqual([[0, 1, 2, 3]]);
  });

  it("doesn't chain unrelated holdings through a shared neighbour", () => {
    const corr = symmetric(3, [
      [0, 1, 0.45],
      [1, 2, 0.45],
    ]);
    // {0,1,2} would average 0.30, so only one of the pairs forms.
    expect(correlatedClusters(corr, 0.4, 0.3)).toHaveLength(1);
    expect(correlatedClusters(corr, 0.4, 0.3)[0]).toHaveLength(2);
  });

  it("keeps out a holding that only rides on a tight core", () => {
    // {0,1,2} averages (0.9+0.25+0.25)/3 ≈ 0.47, but 2 averages 0.25 with the rest.
    const corr = symmetric(3, [
      [0, 1, 0.9],
      [0, 2, 0.25],
      [1, 2, 0.25],
    ]);
    expect(correlatedClusters(corr, 0.4, 0.3)).toEqual([[0, 1]]);
  });

  it("flags heavy risk shares, outsized ones and big correlated clusters", () => {
    const corr = symmetric(5, [[3, 4, 0.9]]);
    const stats = statsOf(corr, [0.1, 0.1, 0.8, 0.3, 0.3]);
    const c = concentration([0.5, 0.2, 0.06, 0.12, 0.12], stats);
    const flagged = c.holdings.map((h) => h.index);
    expect(flagged).toContain(2); // 6% of the weight, far more of the risk
    expect(flagged).not.toContain(1);
    expect(c.clusters).toHaveLength(1);
    expect([...c.clusters[0].members].sort()).toEqual([3, 4]);
    expect(c.clusters[0].weight).toBeCloseTo(0.24);
    expect(c.clusters[0].correlation).toBeCloseTo(0.9);
    // Two equal holdings at ρ = 0.9: 2 / (1 + 0.9) bets.
    expect(c.clusters[0].bets).toBeCloseTo(2 / 1.9);
  });

  it("leaves a small, mildly correlated pair alone", () => {
    const corr = symmetric(4, [[2, 3, 0.5]]);
    const c = concentration([0.45, 0.45, 0.05, 0.05], statsOf(corr, [0.2, 0.2, 0.2, 0.2]));
    expect(c.clusters).toEqual([]);
  });
});
