// Correlation engine for the Correlation page: returns, correlation matrix,
// market graph + maximum independent set, and the MPT risk figures built on
// them. Pure functions, no React, so the θ slider and window toggles can rerun
// them cheaply and the maths stays testable (correlation.test.ts).
//
// Method follows Hidaka et al., "Correlation-Diversified Portfolio
// Construction by Finding Maximum Independent Set in Large-Scale Market Graph"
// (IEEE Access, 2023): log returns (Eq. 3), Pearson correlation over the
// look-back window (Eq. 5), an edge between two assets when C_ij ≥ θ, and the
// MIS of that graph as the largest set of mutually uncorrelated holdings.
// The risk figures use a shrunk covariance instead (shrinkCorrelation): with a
// year of weekly returns, raw correlations carry ±0.13 of estimation noise.

export type Frequency = "daily" | "weekly";

export const PERIODS_PER_YEAR: Record<Frequency, number> = { daily: 252, weekly: 52 };

/** Fewest returns an asset needs in the window to be correlated at all —
 * about six months either way. */
export const MIN_OBSERVATIONS: Record<Frequency, number> = { daily: 120, weekly: 26 };

/** Above this many assets the exact MIS search gives way to a greedy one. */
const EXACT_MIS_LIMIT = 60;
/** Equally large independent sets whose weights differ by less than this tie. */
const TIE_EPSILON = 1e-9;

export interface PriceSeriesInput {
  dates: string[];
  closesEur: number[];
}

export interface ReturnsPanel {
  symbols: string[];
  /** Date of each return (the later of its two closes). */
  dates: string[];
  /** returns[asset][t]: log return, null before the asset's history starts. */
  returns: (number | null)[][];
  /** Symbols dropped for having fewer than MIN_OBSERVATIONS returns. */
  excluded: string[];
}

const DAY_MS = 86_400_000;
const isoDate = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const parseIso = (iso: string) => Date.parse(`${iso}T00:00:00Z`);

/** Weekdays from `start` to `end` inclusive — or only Fridays, for weekly. */
export function calendar(start: string, end: string, frequency: Frequency): string[] {
  const out: string[] = [];
  for (let ms = parseIso(start); ms <= parseIso(end); ms += DAY_MS) {
    const weekday = new Date(ms).getUTCDay();
    if (frequency === "weekly" ? weekday === 5 : weekday !== 0 && weekday !== 6) out.push(isoDate(ms));
  }
  return out;
}

/**
 * Log returns of every symbol on one shared calendar.
 *
 * Each series is forward-filled onto the calendar, so crypto's weekend moves
 * fold into Monday's return (log returns add up) and an exchange holiday reads
 * as a flat day followed by a two-day move. An asset that listed inside the
 * window simply has nulls before its first close; pairwise statistics then use
 * whatever overlap each pair has instead of cutting everyone's window short.
 */
export function alignReturns(
  series: Record<string, PriceSeriesInput>,
  symbols: string[],
  years: number,
  frequency: Frequency,
): ReturnsPanel {
  const present = symbols.filter((s) => series[s]?.dates.length);
  const end = present.reduce((max, s) => {
    const last = series[s].dates[series[s].dates.length - 1];
    return last > max ? last : max;
  }, "");
  if (!end) return { symbols: [], dates: [], returns: [], excluded: symbols };
  const start = isoDate(parseIso(end) - Math.round(years * 365) * DAY_MS);
  const days = calendar(start, end, frequency);

  const kept: string[] = [];
  const returns: (number | null)[][] = [];
  const excluded = symbols.filter((s) => !present.includes(s));
  for (const symbol of present) {
    const { dates, closesEur } = series[symbol];
    const levels: (number | null)[] = [];
    let i = -1;
    for (const day of days) {
      while (i + 1 < dates.length && dates[i + 1] <= day) i++;
      levels.push(i >= 0 && closesEur[i] > 0 ? closesEur[i] : null);
    }
    const r: (number | null)[] = [];
    for (let t = 1; t < levels.length; t++) {
      const prev = levels[t - 1];
      const cur = levels[t];
      r.push(prev !== null && cur !== null ? Math.log(cur / prev) : null);
    }
    if (r.filter((x) => x !== null).length < MIN_OBSERVATIONS[frequency]) {
      excluded.push(symbol);
      continue;
    }
    kept.push(symbol);
    returns.push(r);
  }
  return { symbols: kept, dates: days.slice(1), returns, excluded };
}

// ── Statistics ────────────────────────────────────────────────────────────────

export interface Stats {
  /** Annualized volatility of each asset (sample std of its own returns). */
  sigma: number[];
  /** Pairwise-complete Pearson correlation; NaN when a pair barely overlaps. */
  corr: number[][];
  /** Number of returns each pair's correlation was computed over. */
  overlap: number[][];
  /** Annualized covariance built on the shrunk correlation, ρ̃_ij·σ_i·σ_j.
   * Every risk figure reads this; the heatmap and market graph show `corr`. */
  cov: number[][];
  shrinkage: Shrinkage;
}

/** Sample correlation pulled toward a constant one, ρ̃ = (1−λ)·ρ + λ·ρ̄. */
export interface Shrinkage {
  /** ρ̄: the mean off-diagonal sample correlation, floored at 0. */
  target: number;
  /** λ, 0–1: how far the sample was pulled toward the target. */
  intensity: number;
  corr: number[][];
}

interface PairMoments {
  rho: number;
  n: number;
  /** Estimated sampling variance of ρ (Schäfer & Strimmer, 2005). */
  variance: number;
}

function pearson(a: (number | null)[], b: (number | null)[]): PairMoments {
  let n = 0;
  let sa = 0;
  let sb = 0;
  for (let t = 0; t < a.length; t++) {
    const x = a[t];
    const y = b[t];
    if (x === null || y === null) continue;
    n++;
    sa += x;
    sb += y;
  }
  if (n < 3) return { rho: NaN, n, variance: NaN };
  const ma = sa / n;
  const mb = sb / n;
  let num = 0;
  let da = 0;
  let db = 0;
  for (let t = 0; t < a.length; t++) {
    const x = a[t];
    const y = b[t];
    if (x === null || y === null) continue;
    num += (x - ma) * (y - mb);
    da += (x - ma) ** 2;
    db += (y - mb) ** 2;
  }
  if (da === 0 || db === 0) return { rho: NaN, n, variance: NaN };
  // ρ is the mean of the standardized cross products w_t (times n/(n−1)), so
  // its variance follows from how much those products scatter.
  const scale = Math.sqrt((da / (n - 1)) * (db / (n - 1)));
  const wMean = num / scale / n;
  let scatter = 0;
  for (let t = 0; t < a.length; t++) {
    const x = a[t];
    const y = b[t];
    if (x === null || y === null) continue;
    scatter += (((x - ma) * (y - mb)) / scale - wMean) ** 2;
  }
  return {
    rho: Math.max(-1, Math.min(1, num / Math.sqrt(da * db))),
    n,
    variance: (n / (n - 1) ** 3) * scatter,
  };
}

/** Cholesky with a hair of diagonal jitter, so a singular but valid matrix
 * (all ones, say) still passes. */
function isPositiveSemidefinite(m: number[][]): boolean {
  const n = m.length;
  const L = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = 0; j <= i; j++) {
      let s = m[i][j] + (i === j ? 1e-9 : 0);
      for (let k = 0; k < j; k++) s -= L[i][k] * L[j][k];
      if (i === j) {
        if (s <= 0) return false;
        L[i][i] = Math.sqrt(s);
      } else {
        L[i][j] = s / L[j][j];
      }
    }
  }
  return true;
}

/**
 * Shrinks the correlation matrix toward constant correlation, the
 * Ledoit & Wolf ("Honey, I Shrunk the Sample Covariance Matrix", 2004) target,
 * with the intensity estimated as in Schäfer & Strimmer (2005):
 * λ = Σ Var(ρ_ij) / Σ (ρ_ij − ρ̄)². That estimate works pair by pair, so it
 * suits the pairwise-complete matrix here; it treats ρ̄ as known, which errs
 * slightly toward more shrinkage. A pair with no correlation takes ρ̄.
 *
 * A pairwise-complete matrix need not be positive semi-definite. The target
 * is, and the valid matrices form a convex set, so if the sample-optimal λ
 * leaves the matrix invalid it is raised to the smallest λ that doesn't.
 */
export function shrinkCorrelation(corr: number[][], variance: number[][]): Shrinkage {
  const n = corr.length;
  const pairs: { rho: number; variance: number }[] = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (Number.isFinite(corr[i][j])) pairs.push({ rho: corr[i][j], variance: variance[i][j] });
    }
  }
  // A negative average across a whole portfolio is implausible, and ρ̄ ≥ 0
  // keeps the target positive definite.
  const target = Math.max(0, pairs.reduce((s, p) => s + p.rho, 0) / (pairs.length || 1));
  const noise = pairs.reduce((s, p) => s + (Number.isFinite(p.variance) ? p.variance : 0), 0);
  const spread = pairs.reduce((s, p) => s + (p.rho - target) ** 2, 0);

  const blend = (lambda: number) =>
    corr.map((row, i) =>
      row.map((rho, j) =>
        i === j ? 1 : Number.isFinite(rho) ? (1 - lambda) * rho + lambda * target : target,
      ),
    );

  let intensity = spread > 0 ? Math.min(1, noise / spread) : 0;
  if (!isPositiveSemidefinite(blend(intensity))) {
    let lo = intensity;
    let hi = 1;
    for (let k = 0; k < 40; k++) {
      const mid = (lo + hi) / 2;
      if (isPositiveSemidefinite(blend(mid))) hi = mid;
      else lo = mid;
    }
    intensity = hi;
  }
  return { target, intensity, corr: blend(intensity) };
}

function stdev(xs: (number | null)[]): number {
  const v = xs.filter((x): x is number => x !== null);
  if (v.length < 2) return NaN;
  const mean = v.reduce((s, x) => s + x, 0) / v.length;
  return Math.sqrt(v.reduce((s, x) => s + (x - mean) ** 2, 0) / (v.length - 1));
}

export function computeStats(panel: ReturnsPanel, frequency: Frequency): Stats {
  const n = panel.symbols.length;
  const sigma = panel.returns.map((r) => stdev(r) * Math.sqrt(PERIODS_PER_YEAR[frequency]));
  const corr = Array.from({ length: n }, () => new Array<number>(n).fill(NaN));
  const overlap = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  const variance = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i; j < n; j++) {
      const pair = pearson(panel.returns[i], panel.returns[j]);
      corr[i][j] = corr[j][i] = i === j ? 1 : pair.rho;
      overlap[i][j] = overlap[j][i] = pair.n;
      variance[i][j] = variance[j][i] = i === j ? 0 : pair.variance;
    }
  }
  const shrinkage = shrinkCorrelation(corr, variance);
  const cov = shrinkage.corr.map((row, i) => row.map((rho, j) => rho * sigma[i] * sigma[j]));
  return { sigma, corr, overlap, cov, shrinkage };
}

/**
 * Leaf order of an average-linkage hierarchical clustering on the correlation
 * distance √(2(1−ρ)), so correlated blocks sit next to each other in the
 * heatmap instead of wherever the holdings list happened to put them.
 */
export function clusterOrder(corr: number[][]): number[] {
  const dist = (i: number, j: number) => {
    const rho = corr[i][j];
    return Math.sqrt(2 * (1 - (Number.isFinite(rho) ? rho : 0)));
  };
  let clusters = corr.map((_, i) => [i]);
  while (clusters.length > 1) {
    let best = { a: 0, b: 1, d: Infinity };
    for (let a = 0; a < clusters.length; a++) {
      for (let b = a + 1; b < clusters.length; b++) {
        let total = 0;
        for (const i of clusters[a]) for (const j of clusters[b]) total += dist(i, j);
        const d = total / (clusters[a].length * clusters[b].length);
        if (d < best.d) best = { a, b, d };
      }
    }
    const merged = [...clusters[best.a], ...clusters[best.b]];
    clusters = clusters.filter((_, k) => k !== best.a && k !== best.b);
    clusters.push(merged);
  }
  return clusters[0] ?? [];
}

/** `order` with each group's members pulled together at the first one's
 * place, keeping their relative order, so the heatmap can outline the group as
 * one block. Indices in no group keep their place. */
export function contiguousOrder(order: number[], groups: number[][]): number[] {
  const groupOf = new Map<number, number[]>();
  for (const group of groups) for (const i of group) groupOf.set(i, group);
  const out: number[] = [];
  const placed = new Set<number>();
  for (const i of order) {
    if (placed.has(i)) continue;
    const members = groupOf.get(i);
    const block = members ? order.filter((j) => members.includes(j)) : [i];
    for (const j of block) placed.add(j);
    out.push(...block);
  }
  return out;
}

// ── Market graph ──────────────────────────────────────────────────────────────

export interface Edge {
  i: number;
  j: number;
  rho: number;
}

export interface MarketGraph {
  adjacency: boolean[][];
  edges: Edge[];
  degree: number[];
  /** Edges present out of all n(n−1)/2 possible (0–1). */
  density: number;
}

/** Nodes are assets; i–j is an edge when C_ij ≥ θ (the paper's market graph). */
export function marketGraph(corr: number[][], theta: number): MarketGraph {
  const n = corr.length;
  const adjacency = Array.from({ length: n }, () => new Array<boolean>(n).fill(false));
  const edges: Edge[] = [];
  const degree = new Array<number>(n).fill(0);
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (corr[i][j] >= theta) {
        adjacency[i][j] = adjacency[j][i] = true;
        edges.push({ i, j, rho: corr[i][j] });
        degree[i]++;
        degree[j]++;
      }
    }
  }
  const pairs = (n * (n - 1)) / 2;
  return { adjacency, edges, degree, density: pairs ? edges.length / pairs : 0 };
}

function meanInternalCorrelation(set: number[], corr: number[][]): number {
  let total = 0;
  let count = 0;
  for (let a = 0; a < set.length; a++) {
    for (let b = a + 1; b < set.length; b++) {
      const rho = corr[set[a]][set[b]];
      if (Number.isFinite(rho)) {
        total += rho;
        count++;
      }
    }
  }
  return count ? total / count : 0;
}

/**
 * Maximum independent set of the market graph: the most holdings that are
 * pairwise below θ. Exact branch and bound for portfolio-sized graphs (the
 * paper needed an Ising machine for 2,000 nodes; a dozen is instant), greedy
 * minimum-degree (Butenko) beyond EXACT_MIS_LIMIT. Among equally large sets
 * the one holding the most portfolio weight wins, then the one with the
 * lowest mean internal correlation. Indices ascending.
 */
export function maximumIndependentSet(
  adjacency: boolean[][],
  corr: number[][],
  weights: number[] = [],
): number[] {
  const n = adjacency.length;
  const weightOf = (set: number[]) => set.reduce((s, i) => s + (weights[i] ?? 0), 0);
  if (n > EXACT_MIS_LIMIT) return greedyIndependentSet(adjacency, weights);

  let best: number[] = [];
  let bestWeight = -Infinity;
  let bestScore = Infinity;

  const search = (chosen: number[], candidates: number[]) => {
    if (chosen.length + candidates.length < best.length) return;
    // A candidate with no neighbour left among the candidates belongs to
    // every maximum set of what remains, so it's taken without branching.
    const isolated = candidates.filter((v) => !candidates.some((u) => adjacency[v][u]));
    if (isolated.length) {
      search(
        [...chosen, ...isolated],
        candidates.filter((v) => !isolated.includes(v)),
      );
      return;
    }
    if (candidates.length === 0) {
      const weight = weightOf(chosen);
      const score = meanInternalCorrelation(chosen, corr);
      const heavier = weight > bestWeight + TIE_EPSILON;
      const asHeavy = Math.abs(weight - bestWeight) <= TIE_EPSILON;
      if (chosen.length > best.length || heavier || (asHeavy && score < bestScore)) {
        best = [...chosen].sort((a, b) => a - b);
        bestWeight = weight;
        bestScore = score;
      }
      return;
    }
    // Branch on the candidate with the most neighbours: taking it prunes the
    // most, and leaving it out is where the bound bites first.
    let v = candidates[0];
    let maxDegree = -1;
    for (const c of candidates) {
      const d = candidates.filter((u) => adjacency[c][u]).length;
      if (d > maxDegree) {
        maxDegree = d;
        v = c;
      }
    }
    search(
      [...chosen, v],
      candidates.filter((u) => u !== v && !adjacency[v][u]),
    );
    search(
      chosen,
      candidates.filter((u) => u !== v),
    );
  };

  search([], Array.from({ length: n }, (_, i) => i));
  return best;
}

function greedyIndependentSet(adjacency: boolean[][], weights: number[]): number[] {
  let remaining = adjacency.map((_, i) => i);
  const chosen: number[] = [];
  while (remaining.length) {
    const degreeOf = (v: number) => remaining.filter((u) => adjacency[v][u]).length;
    // Fewest links first; the heavier holding when they tie.
    const v = remaining.reduce((a, b) => {
      const d = degreeOf(b) - degreeOf(a);
      return d < 0 || (d === 0 && (weights[b] ?? 0) > (weights[a] ?? 0)) ? b : a;
    });
    chosen.push(v);
    remaining = remaining.filter((u) => u !== v && !adjacency[v][u]);
  }
  return chosen.sort((a, b) => a - b);
}

// ── Portfolio risk (MPT) ──────────────────────────────────────────────────────

export function normalize(weights: number[]): number[] {
  const total = weights.reduce((s, w) => s + Math.max(0, w), 0);
  return weights.map((w) => (total > 0 ? Math.max(0, w) / total : 0));
}

/** Inverse-volatility weights, w_i ∝ 1/σ_i (the paper's IVW, Eq. 2). */
export function inverseVolatilityWeights(sigma: number[]): number[] {
  return normalize(sigma.map((s) => (s > 0 && Number.isFinite(s) ? 1 / s : 0)));
}

/** σ_p = √(wᵀΣw). Clamped at zero against rounding: the shrunk Σ is positive
 * semi-definite, but only up to the jitter its check allows. */
export function portfolioVolatility(w: number[], cov: number[][]): number {
  let variance = 0;
  for (let i = 0; i < w.length; i++) for (let j = 0; j < w.length; j++) variance += w[i] * w[j] * cov[i][j];
  return Math.sqrt(Math.max(0, variance));
}

export interface Diversification {
  /** Annualized portfolio volatility with the actual weights. */
  volatility: number;
  /** Σ w_i·σ_i — the volatility if every holding moved in lockstep. */
  weightedVolatility: number;
  /** weightedVolatility / volatility: 1 means no diversification at all. */
  ratio: number;
  /** Σ_{i≠j} w_i w_j ρ_ij / Σ_{i≠j} w_i w_j. */
  averageCorrelation: number;
  /** 1 / Σ w_i² — how many equal positions the weights amount to. */
  effectiveHoldings: number;
  /** ratio² — how many independent bets the risk amounts to (Choueifaty). */
  effectiveBets: number;
  /** Portfolio volatility under equal (EW) and inverse-volatility (IVW) weights. */
  equalWeightVolatility: number;
  inverseVolatilityVolatility: number;
}

export function diversification(weights: number[], stats: Stats): Diversification {
  const w = normalize(weights);
  const { sigma, cov, corr } = stats;
  const volatility = portfolioVolatility(w, cov);
  const weightedVolatility = w.reduce((s, wi, i) => s + wi * (Number.isFinite(sigma[i]) ? sigma[i] : 0), 0);
  const ratio = volatility > 0 ? weightedVolatility / volatility : 1;

  let num = 0;
  let den = 0;
  for (let i = 0; i < w.length; i++) {
    for (let j = 0; j < w.length; j++) {
      if (i === j || !Number.isFinite(corr[i][j])) continue;
      num += w[i] * w[j] * corr[i][j];
      den += w[i] * w[j];
    }
  }
  const sumSquares = w.reduce((s, x) => s + x * x, 0);
  return {
    volatility,
    weightedVolatility,
    ratio,
    averageCorrelation: den > 0 ? num / den : NaN,
    effectiveHoldings: sumSquares > 0 ? 1 / sumSquares : 0,
    effectiveBets: ratio * ratio,
    equalWeightVolatility: portfolioVolatility(normalize(w.map(() => 1)), cov),
    inverseVolatilityVolatility: portfolioVolatility(inverseVolatilityWeights(sigma), cov),
  };
}

/** Share of portfolio variance each holding is responsible for:
 * RC_i = w_i·(Σw)_i / σ_p². Sums to 1. */
export function riskContributions(weights: number[], cov: number[][]): number[] {
  const w = normalize(weights);
  const marginal = w.map((_, i) => w.reduce((s, wj, j) => s + cov[i][j] * wj, 0));
  const variance = w.reduce((s, wi, i) => s + wi * marginal[i], 0);
  return w.map((wi, i) => (variance > 0 ? (wi * marginal[i]) / variance : 0));
}

// ── Concentration ─────────────────────────────────────────────────────────────

/** When a holding or a group of them is flagged as concentrating risk. */
export const CONCENTRATION = {
  /** A holding carrying this share of the variance, whatever its weight… */
  riskShare: 0.2,
  /** …or this share when it is also this many times its weight. */
  outsizedRiskShare: 0.1,
  outsizedRatio: 1.5,
  /** A cluster's mean pairwise ρ stays at or above this, with no member
   * averaging less than clusterMemberCorrelation with the rest… */
  clusterCorrelation: 0.4,
  clusterMemberCorrelation: 0.3,
  /** …and it is flagged once it holds this much weight or variance. */
  clusterWeight: 0.15,
  clusterRisk: 0.2,
} as const;

export interface ConcentratedHolding {
  index: number;
  weight: number;
  /** Share of the portfolio variance (riskContributions). */
  risk: number;
}

export interface CorrelatedCluster {
  /** Heaviest first. */
  members: number[];
  /** Mean observed ρ over the pairs inside the cluster. */
  correlation: number;
  weight: number;
  risk: number;
  /** The cluster's own diversification ratio², as in Diversification.effectiveBets. */
  bets: number;
}

export interface Concentration {
  holdings: ConcentratedHolding[];
  clusters: CorrelatedCluster[];
}

/**
 * Groups of holdings that move together: agglomerative clustering that merges
 * whichever two clusters leave the highest mean pairwise ρ inside the merged
 * one, as long as that mean stays at or above `minCorrelation` and every
 * member averages at least `minMemberCorrelation` with the rest. The second
 * bar keeps a tight core from dragging in holdings it barely moves with, and
 * unlike linking every pair over a threshold, one holding correlated with two
 * unrelated ones can't chain them into a block. Singletons are left out; a
 * pair with no correlation counts as ρ = 0.
 */
export function correlatedClusters(
  corr: number[][],
  minCorrelation: number,
  minMemberCorrelation: number,
): number[][] {
  const rhoOf = (i: number, j: number) => (Number.isFinite(corr[i][j]) ? corr[i][j] : 0);
  /** Mean pairwise ρ of the group, or null when it misses either bar. */
  const cohesion = (members: number[]): number | null => {
    let total = 0;
    for (const i of members) {
      let own = 0;
      for (const j of members) if (j !== i) own += rhoOf(i, j);
      if (own / (members.length - 1) < minMemberCorrelation) return null;
      total += own;
    }
    const mean = total / (members.length * (members.length - 1));
    return mean >= minCorrelation ? mean : null;
  };
  let clusters = corr.map((_, i) => [i]);
  for (;;) {
    let best: { a: number; b: number; mean: number } | null = null;
    for (let a = 0; a < clusters.length; a++) {
      for (let b = a + 1; b < clusters.length; b++) {
        const mean = cohesion([...clusters[a], ...clusters[b]]);
        if (mean !== null && (!best || mean > best.mean)) best = { a, b, mean };
      }
    }
    if (!best) break;
    const { a, b } = best;
    const merged = [...clusters[a], ...clusters[b]];
    clusters = clusters.filter((_, k) => k !== a && k !== b);
    clusters.push(merged);
  }
  return clusters.filter((c) => c.length > 1);
}

/**
 * Holdings and correlated clusters that carry an outsized share of the risk,
 * per CONCENTRATION; each list sorted by risk share, largest first. Clusters
 * come from the observed correlations, like the heatmap; risk shares and bets
 * from the shrunk covariance, like the rest of the risk figures.
 */
export function concentration(weights: number[], stats: Stats): Concentration {
  const w = normalize(weights);
  const risk = riskContributions(w, stats.cov);
  const holdings = w
    .map((weight, index) => ({ index, weight, risk: risk[index] }))
    .filter(
      (h) =>
        h.risk >= CONCENTRATION.riskShare ||
        (h.risk >= CONCENTRATION.outsizedRiskShare && h.risk >= CONCENTRATION.outsizedRatio * h.weight),
    )
    .sort((a, b) => b.risk - a.risk);

  const clusters = correlatedClusters(stats.corr, CONCENTRATION.clusterCorrelation, CONCENTRATION.clusterMemberCorrelation)
    .map((group) => {
      const members = [...group].sort((a, b) => w[b] - w[a]);
      let sum = 0;
      for (let a = 0; a < members.length; a++)
        for (let b = a + 1; b < members.length; b++) {
          const rho = stats.corr[members[a]][members[b]];
          sum += Number.isFinite(rho) ? rho : 0;
        }
      const inside = normalize(members.map((i) => w[i]));
      const weighted = inside.reduce((s, x, k) => s + x * stats.sigma[members[k]], 0);
      const volatility = portfolioVolatility(
        inside,
        members.map((i) => members.map((j) => stats.cov[i][j])),
      );
      return {
        members,
        correlation: sum / ((members.length * (members.length - 1)) / 2),
        weight: members.reduce((s, i) => s + w[i], 0),
        risk: members.reduce((s, i) => s + risk[i], 0),
        bets: volatility > 0 ? (weighted / volatility) ** 2 : 1,
      };
    })
    .filter((c) => c.weight >= CONCENTRATION.clusterWeight || c.risk >= CONCENTRATION.clusterRisk)
    .sort((a, b) => b.risk - a.risk);

  return { holdings, clusters };
}
