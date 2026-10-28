import { useMemo, useState } from "react";
import Card from "./Card";
import KpiCard, { SUB } from "./KpiCard";
import Heatmap from "./charts/Heatmap";
import HBar from "./charts/HBar";
import MarketGraph from "./charts/MarketGraph";
import {
  alignReturns,
  clusterOrder,
  computeStats,
  concentration,
  CONCENTRATION,
  contiguousOrder,
  diversification,
  inverseVolatilityWeights,
  marketGraph,
  maximumIndependentSet,
  normalize,
  riskContributions,
  type Frequency,
} from "./correlation";
import { useAppData } from "./data";
import { pct1, rho } from "./format";
import { useTranslation } from "./i18n/LanguageContext";
import { useHoldings } from "./useHoldings";

const LOCALE = { en: "en-GB", ca: "ca-ES", es: "es-ES" } as const;
const WINDOWS = [1, 3] as const;
const FREQUENCIES: Frequency[] = ["daily", "weekly"];

const TH =
  "border-b border-hairline-strong px-2.5 py-2.5 text-left text-[11.5px] font-semibold uppercase tracking-[0.04em] text-muted whitespace-nowrap";
const TD = "border-b border-hairline px-2.5 py-2 whitespace-nowrap";
const NUM = "text-right tabular-nums";

const chipClass = (active: boolean) =>
  `rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-focus ${
    active ? "border-accent bg-accent-subtle text-accent" : "border-hairline bg-surface text-secondary hover:border-hairline-strong hover:text-primary"
  }`;

const pct = (fraction: number) => (Number.isFinite(fraction) ? `${pct1.format(fraction * 100)}%` : "—");
const ratio2 = (v: number) =>
  Number.isFinite(v) ? v.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
const times = (v: number) => v.toLocaleString("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const points = (fraction: number) =>
  `${fraction >= 0 ? "+" : "−"}${(Math.abs(fraction) * 100).toLocaleString("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} pp`;

export default function CorrelationPage() {
  const { prices } = useAppData();
  const { stocks, valueBySymbol } = useHoldings();
  const { t, lang } = useTranslation();
  const [years, setYears] = useState<(typeof WINDOWS)[number]>(3);
  const [frequency, setFrequency] = useState<Frequency>("daily");
  const [theta, setTheta] = useState(0.3);

  const nameBySymbol = useMemo(() => new Map(stocks.map((s) => [s.symbol, s.name])), [stocks]);
  const symbols = useMemo(() => stocks.map((s) => s.symbol), [stocks]);

  const panel = useMemo(
    () => (prices ? alignReturns(prices.series, symbols, years, frequency) : null),
    [prices, symbols, years, frequency],
  );
  const stats = useMemo(() => (panel ? computeStats(panel, frequency) : null), [panel, frequency]);
  const weights = useMemo(
    () => normalize(panel?.symbols.map((s) => valueBySymbol.get(s) ?? 0) ?? []),
    [panel, valueBySymbol],
  );
  const order = useMemo(() => (stats ? clusterOrder(stats.corr) : []), [stats]);
  const graph = useMemo(() => (stats ? marketGraph(stats.corr, theta) : null), [stats, theta]);
  const core = useMemo(
    () => (graph && stats ? new Set(maximumIndependentSet(graph.adjacency, stats.corr, weights)) : new Set<number>()),
    [graph, stats, weights],
  );

  const title = <h1 className="mb-4 font-display text-[26px] font-semibold tracking-tight">{t.nav.correlation}</h1>;

  if (!prices) {
    return (
      <>
        {title}
        <p className="text-sm text-secondary">{t.correlation.empty}</p>
      </>
    );
  }
  if (!panel || !stats || !graph || panel.symbols.length < 2) {
    return (
      <>
        {title}
        <p className="text-sm text-secondary">{t.correlation.tooFew}</p>
      </>
    );
  }

  const n = panel.symbols.length;
  const div = diversification(weights, stats);
  const contributions = riskContributions(weights, stats.cov);
  const flags = concentration(weights, stats);
  const flagged = new Set(flags.holdings.map((h) => h.index));
  const clusterOf = new Map(flags.clusters.flatMap((c) => c.members.map((i) => [i, c] as const)));
  const movesWith = (i: number) =>
    clusterOf
      .get(i)
      ?.members.filter((j) => j !== i)
      .map((j) => panel.symbols[j])
      .join(", ");
  const ivw = inverseVolatilityWeights(stats.sigma);
  const coreWeight = [...core].reduce((s, i) => s + weights[i], 0);
  const locale = LOCALE[lang];
  const longDate = (iso: string) =>
    new Date(iso).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
  const observations = Math.max(...stats.overlap.map((row, i) => row[i]));
  const riskRows = panel.symbols
    .map((symbol, i) => ({ symbol, i }))
    .sort((a, b) => contributions[b.i] - contributions[a.i]);
  const maxShare = Math.max(...contributions, ...weights, 1e-9);

  return (
    <>
      {title}
      <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2">
        <div className="flex items-center gap-1.5">
          <span className="mr-1 text-xs font-semibold uppercase tracking-[0.04em] text-muted">{t.correlation.window}</span>
          {WINDOWS.map((y) => (
            <button key={y} type="button" className={chipClass(years === y)} onClick={() => setYears(y)} aria-pressed={years === y}>
              {t.correlation.years(y)}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="mr-1 text-xs font-semibold uppercase tracking-[0.04em] text-muted">{t.correlation.returns}</span>
          {FREQUENCIES.map((f) => (
            <button
              key={f}
              type="button"
              className={chipClass(frequency === f)}
              onClick={() => setFrequency(f)}
              aria-pressed={frequency === f}
              title={f === "weekly" ? t.correlation.weeklyHint : undefined}
            >
              {t.correlation[f]}
            </button>
          ))}
        </div>
        <span className="text-xs tabular-nums text-muted">
          {t.correlation.period(longDate(panel.dates[0]), longDate(panel.dates[panel.dates.length - 1]), observations)}
        </span>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label={t.correlation.volatility}
          value={pct(div.volatility)}
          sub={<span className={`${SUB} text-secondary`}>{t.correlation.volatilitySub(pct(div.weightedVolatility))}</span>}
        />
        <KpiCard
          label={t.correlation.ratio}
          value={ratio2(div.ratio)}
          sub={<span className={`${SUB} text-secondary`}>{t.correlation.ratioSub}</span>}
        />
        <KpiCard
          label={t.correlation.avgCorrelation}
          value={rho(div.averageCorrelation)}
          sub={<span className={`${SUB} text-secondary`}>{t.correlation.avgCorrelationSub}</span>}
        />
        <KpiCard
          label={t.correlation.bets}
          value={ratio2(div.effectiveBets)}
          sub={
            <span className={`${SUB} text-secondary`}>
              {t.correlation.betsSub(n, ratio2(div.effectiveHoldings))}
            </span>
          }
        />
      </div>

      <div className="panel-grid grid grid-cols-12">
        <Card className="col-span-12 xl:col-span-7" title={t.correlation.matrix}>
          <p className="-mt-2 mb-4 text-xs text-secondary">{t.correlation.matrixHint}</p>
          <Heatmap
            labels={panel.symbols}
            matrix={stats.corr}
            order={contiguousOrder(
              order,
              flags.clusters.map((c) => c.members),
            )}
            highlight={core}
            groups={flags.clusters.map((c) => c.members)}
            legend={{ negative: t.correlation.legendNegative, positive: t.correlation.legendPositive }}
            ariaLabel={t.correlation.matrixAria}
            tooltip={(i, j) => (
              <>
                <div className="mb-1 font-semibold text-primary">
                  {panel.symbols[i]} · {panel.symbols[j]}
                </div>
                <div className="flex items-center justify-between gap-4">
                  <span className="text-secondary">ρ</span>
                  <span className="font-semibold tabular-nums text-primary">{rho(stats.corr[i][j])}</span>
                </div>
                <div className="text-muted tabular-nums">{t.correlation.overlap(stats.overlap[i][j])}</div>
              </>
            )}
          />
        </Card>

        <Card className="col-span-12 xl:col-span-5" title={t.correlation.graph}>
          <p className="-mt-2 mb-3 text-xs text-secondary">{t.correlation.graphHint}</p>
          <label className="mb-2 flex items-center gap-3 text-xs text-secondary">
            <span className="whitespace-nowrap font-semibold uppercase tracking-[0.04em] text-muted">{t.correlation.threshold}</span>
            <input
              type="range"
              min={0}
              max={0.9}
              step={0.01}
              value={theta}
              onChange={(e) => setTheta(Number(e.target.value))}
              className="w-full accent-[var(--accent)]"
            />
            <span className="w-10 text-right font-semibold tabular-nums text-primary">{rho(theta)}</span>
          </label>
          <MarketGraph
            nodes={panel.symbols.map((label, i) => ({
              label,
              weight: weights[i],
              highlighted: core.has(i),
              linked: core.has(i) && graph.degree[i] > 0,
              flagged: flagged.has(i),
            }))}
            groups={flags.clusters.map((c) => ({ members: c.members, label: t.correlation.clusterRisk(pct(c.risk)) }))}
            edges={graph.edges}
            corr={stats.corr}
            theta={theta}
            ariaLabel={t.correlation.graphAria}
            tooltip={(i) => (
              <>
                <div className="font-semibold text-primary">{panel.symbols[i]}</div>
                <div className="mb-1 max-w-[180px] truncate text-muted">{nameBySymbol.get(panel.symbols[i])}</div>
                <div className={core.has(i) ? "text-accent" : "text-secondary"}>
                  {!core.has(i)
                    ? t.correlation.outsideCore
                    : graph.degree[i] > 0
                      ? t.correlation.inCoreLinked
                      : t.correlation.inCore}
                </div>
                <div className="tabular-nums text-secondary">
                  {t.correlation.weightOf(pct(weights[i]))} · {t.correlation.links(graph.degree[i])}
                </div>
                {flagged.has(i) && (
                  <div className="tabular-nums text-loss">{t.correlation.riskOf(pct(contributions[i]))}</div>
                )}
                {clusterOf.has(i) && <div className="max-w-[200px] text-loss">{t.correlation.movesWith(movesWith(i) ?? "")}</div>}
              </>
            )}
          />
          <dl className="mt-3 grid grid-cols-3 gap-3 border-t border-hairline pt-3 text-xs">
            <div>
              <dt className="text-muted">{t.correlation.core}</dt>
              <dd className="text-sm font-semibold tabular-nums">{t.correlation.coreValue(core.size, n)}</dd>
            </div>
            <div>
              <dt className="text-muted">{t.correlation.coreWeight}</dt>
              <dd className="text-sm font-semibold tabular-nums">{pct(coreWeight)}</dd>
            </div>
            <div>
              <dt className="text-muted">{t.correlation.density}</dt>
              <dd className="text-sm font-semibold tabular-nums">{pct(graph.density)}</dd>
            </div>
          </dl>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {[...core].map((i) => (
              <span
                key={i}
                className={`rounded-full px-2.5 py-1 text-xs font-medium text-accent ${
                  graph.degree[i] > 0 ? "border border-dashed border-accent" : "bg-accent-subtle"
                }`}
                title={graph.degree[i] > 0 ? t.correlation.inCoreLinked : t.correlation.inCore}
              >
                {panel.symbols[i]}
              </span>
            ))}
          </div>
        </Card>

        <Card className="col-span-12" title={t.correlation.risk}>
          <p className="-mt-2 mb-3 text-xs text-secondary">{t.correlation.riskHint}</p>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th className={TH}>{t.correlation.asset}</th>
                  <th className={`${TH} text-right`}>{t.correlation.weight}</th>
                  <th className={`${TH} text-right`}>{t.correlation.contribution}</th>
                  <th className={`${TH} w-[22%]`} aria-hidden />
                  <th className={`${TH} text-right`}>{t.correlation.gap}</th>
                  <th className={`${TH} text-right`}>{t.correlation.vol}</th>
                  <th className={`${TH} text-right`}>{t.correlation.ivw}</th>
                  <th className={`${TH} text-right`}>{t.correlation.degree}</th>
                </tr>
              </thead>
              <tbody>
                {riskRows.map(({ symbol, i }) => {
                  const gap = contributions[i] - weights[i];
                  const cluster = clusterOf.get(i);
                  return (
                    <tr key={symbol} className={flagged.has(i) ? "bg-loss-subtle" : "hover:bg-hover"}>
                      <td className={TD}>
                        <span className={`font-semibold ${core.has(i) ? "text-accent" : ""}`}>{symbol}</span>
                        {cluster && (
                          <span
                            className="ml-1.5 inline-flex align-[-1px] text-loss"
                            title={t.correlation.movesWith(movesWith(i) ?? "")}
                          >
                            <ClusterGlyph />
                          </span>
                        )}
                        <span className="ml-2 inline-block max-w-[220px] truncate align-bottom text-xs text-muted">
                          {nameBySymbol.get(symbol)}
                        </span>
                      </td>
                      <td className={`${TD} ${NUM}`}>{pct(weights[i])}</td>
                      <td className={`${TD} ${NUM} font-semibold ${flagged.has(i) ? "text-loss" : ""}`}>{pct(contributions[i])}</td>
                      <td className={TD}>
                        <div className="flex flex-col gap-1">
                          <HBar fraction={weights[i] / maxShare} color="var(--text-muted)" />
                          <HBar fraction={contributions[i] / maxShare} color={flagged.has(i) ? "var(--loss)" : "var(--accent)"} />
                        </div>
                      </td>
                      <td className={`${TD} ${NUM} ${gap > 0.005 ? "text-loss" : gap < -0.005 ? "text-gain" : "text-secondary"}`}>
                        {points(gap)}
                      </td>
                      <td className={`${TD} ${NUM} text-secondary`}>{pct(stats.sigma[i])}</td>
                      <td className={`${TD} ${NUM} text-secondary`}>{pct(ivw[i])}</td>
                      <td className={`${TD} ${NUM} text-secondary`}>{graph.degree[i]}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-secondary">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-1.5 w-4 rounded-full bg-muted" aria-hidden />
              {t.correlation.weight}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-1.5 w-4 rounded-full bg-accent" aria-hidden />
              {t.correlation.contribution}
            </span>
            {flags.holdings.length > 0 && (
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-1.5 w-4 rounded-full bg-loss" aria-hidden />
                {t.correlation.concentrated}
              </span>
            )}
            {flags.clusters.length > 0 && (
              <span className="flex items-center gap-1.5 text-secondary">
                <span className="inline-flex text-loss">
                  <ClusterGlyph />
                </span>
                {t.correlation.block}
              </span>
            )}
          </div>
          <p className="mt-3 text-xs text-secondary">
            {t.correlation.reference(pct(div.equalWeightVolatility), pct(div.inverseVolatilityVolatility))}
          </p>
        </Card>
      </div>

      <div className="mt-6 flex flex-col gap-1 text-xs text-muted">
        <p>{t.correlation.scope}</p>
        <p>
          {t.correlation.concentrationNote(
            pct(CONCENTRATION.riskShare),
            pct(CONCENTRATION.outsizedRiskShare),
            times(CONCENTRATION.outsizedRatio),
            rho(CONCENTRATION.clusterCorrelation),
            rho(CONCENTRATION.clusterMemberCorrelation),
          )}
        </p>
        <p>{t.correlation.shrinkage(pct(stats.shrinkage.intensity), rho(stats.shrinkage.target))}</p>
        {panel.excluded.length > 0 && <p>{t.correlation.excluded(panel.excluded.join(", "))}</p>}
        {prices.unresolved.length > 0 && <p>{t.correlation.unresolved(prices.unresolved.join(", "))}</p>}
        <p>{t.correlation.source(prices.source)}</p>
        <p className="tabular-nums">
          {t.common.updated(
            longDate(prices.updatedAt),
            new Date(prices.updatedAt).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }),
          )}
        </p>
      </div>
    </>
  );
}

/** Three touching dots: "moves as a block" next to a holding's symbol. */
function ClusterGlyph() {
  return (
    <svg width="13" height="11" viewBox="0 0 13 11" aria-hidden>
      <circle cx="3.5" cy="7.5" r="3" fill="currentColor" opacity={0.85} />
      <circle cx="9.5" cy="7.5" r="3" fill="currentColor" opacity={0.85} />
      <circle cx="6.5" cy="3" r="3" fill="currentColor" />
    </svg>
  );
}
