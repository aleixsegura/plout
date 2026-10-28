import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Card from "./Card";
import Donut from "./charts/Donut";
import HBar from "./charts/HBar";
import Sparkline from "./charts/Sparkline";
import { useAppData } from "./data";
import { capFirst, eur, eur0, eurSigned, gainLoss, monthLabel, pctSigned, shortDate } from "./format";
import { useTranslation } from "./i18n/LanguageContext";
import PortfolioChart from "./PortfolioChart";
import { portfolioTotals, revolutInputs, type CashSource } from "./portfolioMath";
import {
  categoryColorVar,
  categoryLabel,
  interestInMonth,
  interestSeries,
  monthKey,
  topCategories,
} from "./transactions";
import TypeIcon from "./TypeIcon";
import type { HistoryDay, PositionsSummary, RevolutTransaction } from "./types";
import { useHoldings } from "./useHoldings";

const LOCALE = { en: "en-GB", ca: "ca-ES", es: "es-ES" } as const;

const EMPTY_SUMMARY: PositionsSummary = {
  totalValueEur: 0,
  totalPlEur: 0,
  totalTodayPlEur: 0,
  cashEur: null,
  netLiquidationEur: 0,
};

const TOP_HOLDINGS = 6;
const RECENT_ROWS = 8;
const DONUT_CATEGORIES = 5;

const LABEL = "text-[11.5px] font-semibold uppercase tracking-[0.04em] text-muted";

function Chip({ value, pct, label }: { value: number; pct: number | null; label: string }) {
  const tone = value >= 0 ? "bg-gain-subtle text-gain" : "bg-loss-subtle text-loss";
  return (
    <span className={`inline-flex items-baseline gap-1.5 rounded-full px-2.5 py-1 text-[12.5px] font-medium tabular-nums ${tone}`}>
      {eurSigned.format(value)}
      {pct !== null && <span className="opacity-80">({pctSigned.format(pct)}%)</span>}
      <span className="font-normal opacity-70">{label}</span>
    </span>
  );
}

export default function DashboardPage() {
  const { t, lang } = useTranslation();
  const { history, dividends, transactions } = useAppData();
  const { positions, revolut, stocks, valueBySymbol, posBySymbol, revolutPosBySymbol } = useHoldings();
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  // The snapshot under the cursor in the net-worth chart; null at rest, when
  // the hero shows today's total.
  const [hoveredDay, setHoveredDay] = useState<HistoryDay | null>(null);
  const locale = LOCALE[lang];

  const totals = useMemo(
    () => portfolioTotals(positions?.summary ?? EMPTY_SUMMARY, revolutInputs(revolut)),
    [positions, revolut],
  );
  const hasPortfolio = positions !== null || revolut !== null;

  // Top holdings by market value, across both brokers.
  const topHoldings = useMemo(() => {
    const rows = stocks
      .map((s) => {
        const value = valueBySymbol.get(s.symbol) ?? 0;
        const p = posBySymbol.get(s.symbol);
        const rp = revolutPosBySymbol.get(s.symbol);
        const pl = (p?.plEur ?? 0) + (rp?.plEur ?? 0);
        const cost = (p?.costEur ?? 0) + (rp?.costEur ?? 0);
        const hasPl = p !== undefined || rp?.plEur != null;
        return { stock: s, value, plPct: hasPl && cost > 0 ? (pl / cost) * 100 : null };
      })
      .filter((r) => r.value > 0)
      .sort((a, b) => b.value - a.value);
    return rows.slice(0, TOP_HOLDINGS);
  }, [stocks, valueBySymbol, posBySymbol, revolutPosBySymbol]);

  const month = monthKey();
  const thisMonth = transactions?.months.find((m) => m.month === month) ?? null;
  const spent = thisMonth?.expenses ?? 0;
  // Refunds that pushed a category below zero; the total is net of them.
  const refunded = Object.values(thisMonth?.byCategory ?? {}).reduce((s, v) => (v < 0 ? s - v : s), 0);
  const categories = useMemo(() => {
    // Categories whose refunds outweigh purchases net out below zero; a donut
    // can't show that, and "-131 €" reads as a bug, so leave them out.
    const entries = Object.entries(thisMonth?.byCategory ?? {}).filter(([, v]) => v > 0);
    const total = entries.reduce((s, [, v]) => s + v, 0);
    const rows = entries
      .map(([category, amount]) => ({ category, amount, count: 0, fraction: total ? amount / total : 0 }))
      .sort((a, b) => b.amount - a.amount);
    return topCategories(rows, DONUT_CATEGORIES);
  }, [thisMonth]);
  // Largest first: colors are dealt out by rank so big slices get related tones.
  const categoryRanking = useMemo(() => categories.map((c) => c.category), [categories]);

  const interestMonth = interestInMonth(transactions, month);
  const interestPoints = useMemo(() => interestSeries(transactions, 30).map((d) => d.amount), [transactions]);
  const savings = transactions?.accounts.savings;
  const boosted = revolut?.cash.find((c) => c.kind === "savings");
  const aer = savings?.aer ?? boosted?.aer;

  // Every hero figure that has history scrubs with the chart. The two chips
  // (today's P/L, open P/L) have none — they recede instead.
  const heroInvestments = hoveredDay ? hoveredDay.portfolioValueEur : totals.portfolioValue;
  const heroCash = hoveredDay ? hoveredDay.cashEur : totals.cash;
  const heroBoosted = hoveredDay ? (hoveredDay.revolut?.cashSavingsEur ?? null) : (boosted?.amountEur ?? null);

  const recent = useMemo(
    () => (transactions?.transactions ?? []).filter((tx) => tx.kind !== "transfer" && tx.kind !== "interest").slice(0, RECENT_ROWS),
    [transactions],
  );
  const upcoming = (dividends?.upcoming ?? []).filter((u) => u.payDate);

  const updatedAt = positions?.updatedAt ?? revolut?.updatedAt ?? transactions?.updatedAt;

  const sourceLabel = (s: CashSource) =>
    s.key === "revolut" ? t.kpis.revolut : s.key === "boosted" ? t.kpis.boosted : s.currency === "EUR" ? t.kpis.degiro : `${t.kpis.degiro} · ${s.currency}`;

  return (
    <>
      <header className="mb-5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h1 className="font-display text-[26px] font-semibold tracking-tight">{t.dashboard.title}</h1>
      </header>

      <div className="panel-grid grid grid-cols-12">
        {/* Net worth hero */}
        <Card className="col-span-12 lg:col-span-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className={LABEL}>{t.dashboard.netWorth}</div>
              <div className="mt-1 font-display text-[40px] font-semibold leading-none tracking-tight tabular-nums">
                {hoveredDay ? eur.format(hoveredDay.totalEur) : hasPortfolio ? eur.format(totals.totalValue) : "—"}
              </div>
              {/* Scrubbing the chart moves this headline: that, and not a second
                  readout under the plot, is what ties the line to the number. */}
              <div className="mt-1 text-xs text-muted">
                {hoveredDay
                  ? capFirst(new Date(hoveredDay.date).toLocaleDateString(locale, { day: "numeric", month: "long", year: "numeric" }))
                  : t.dashboard.positionsPlusCash}
              </div>
            </div>
            {/* Both chips are about *now*, so they recede while the headline is
                reading a past snapshot. */}
            {hasPortfolio && (
              <div className={`flex flex-wrap gap-2 transition-opacity ${hoveredDay ? "opacity-30" : ""}`}>
                <Chip value={totals.todayPl} pct={totals.todayPct} label={t.dashboard.today} />
                <Chip value={totals.openPl} pct={totals.openPct} label={t.dashboard.openPl} />
              </div>
            )}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div>
              <div className={LABEL}>{t.dashboard.investments}</div>
              <div className="text-lg font-semibold tabular-nums">{eur.format(heroInvestments)}</div>
            </div>
            <div>
              <div className={LABEL}>{t.dashboard.cash}</div>
              <div className="text-lg font-semibold tabular-nums">{eur.format(heroCash)}</div>
            </div>
            {boosted && (
              <div>
                <div className={LABEL}>{t.kpis.boosted}</div>
                <div className="text-lg font-semibold tabular-nums">
                  {heroBoosted === null ? "—" : eur.format(heroBoosted)}
                </div>
              </div>
            )}
          </div>
          {/* The chart's own card chrome is stripped here: nested inside the
              hero it read as a separate widget instead of the line under the
              number. Target `figure` — that's what PortfolioChart renders. */}
          <div className="mt-4 min-h-[200px] flex-1 [&>figure]:h-full [&>figure]:border-0 [&>figure]:bg-transparent [&>figure]:p-0 [&>figure]:shadow-none">
            <PortfolioChart history={history} quietAtRest onHover={setHoveredDay} />
          </div>
        </Card>

        {/* Cash accounts */}
        <Card className="col-span-12 md:col-span-6 lg:col-span-4" title={t.dashboard.cashAccounts}>
          {totals.cashSources.length === 0 ? (
            <p className="text-sm text-secondary">—</p>
          ) : (
            <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
              {totals.cashSources.map((s) => (
                <li key={`${s.key}-${s.currency}`} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-primary">{sourceLabel(s)}</div>
                    {s.key === "boosted" && (
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-secondary">
                        {aer !== undefined && (
                          <span className="rounded-full bg-gain-subtle px-1.5 py-0.5 font-medium text-gain">
                            {t.dashboard.aer(aer.toLocaleString("es-ES", { maximumFractionDigits: 2 }))}
                          </span>
                        )}
                      </div>
                    )}
                    {s.key === "degiro" && s.currency !== "EUR" && (
                      <div className="mt-0.5 text-xs text-secondary">
                        {new Intl.NumberFormat("es-ES", { style: "currency", currency: s.currency }).format(s.amount)}
                      </div>
                    )}
                  </div>
                  <div className="text-sm font-semibold tabular-nums">{eur.format(s.amountEur)}</div>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-auto flex items-baseline justify-between border-t border-hairline pt-3 text-sm">
            <span className="text-secondary">{t.kpis.totalCash}</span>
            <span className="font-semibold tabular-nums">{eur.format(totals.cash)}</span>
          </div>
        </Card>

        {/* Top holdings */}
        <Card
          className="col-span-12 md:col-span-6 lg:col-span-4"
          title={t.dashboard.holdings}
          action={{ to: "/holdings", label: t.dashboard.allHoldings }}
        >
          {topHoldings.length === 0 ? (
            <p className="text-sm text-secondary">{t.dashboard.noHoldings}</p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
              {topHoldings.map(({ stock, value, plPct }) => (
                <li key={stock.symbol} className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <TypeIcon symbol={stock.symbol} symbolType={stock.symbolType} />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{stock.compactName || stock.name}</span>
                    <span className="text-sm font-semibold tabular-nums">{eur0.format(value)}</span>
                    {plPct !== null && (
                      <span className={`w-14 text-right text-xs tabular-nums ${gainLoss(plPct)}`}>
                        {pctSigned.format(plPct)}%
                      </span>
                    )}
                  </div>
                  <HBar fraction={totals.portfolioValue > 0 ? value / totals.portfolioValue : 0} color="var(--chart-1)" />
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Spending this month */}
        <Card
          className="col-span-12 md:col-span-6 lg:col-span-4"
          title={t.dashboard.spending}
          action={{ to: "/transactions", label: t.dashboard.transactions }}
        >
          {!transactions ? (
            <NoTransactions />
          ) : (
            <>
              <div className="flex items-baseline gap-3">
                <span className="text-2xl font-semibold tabular-nums">{eur.format(spent)}</span>
              </div>
              {refunded > 0 && (
                <p className="mt-1 text-xs text-secondary">{t.dashboard.includesRefunds(eur0.format(refunded))}</p>
              )}
              {categories.length === 0 ? (
                <p className="mt-2 text-sm text-secondary">{t.dashboard.noSpending}</p>
              ) : (
                <div className="mt-3 flex items-center gap-4">
                  <Donut
                    slices={categories.map((c) => ({
                      key: c.category,
                      label: categoryLabel(t, c.category),
                      value: c.amount,
                      color: categoryColorVar(c.category, categoryRanking),
                    }))}
                    size={120}
                    thickness={18}
                    active={activeCategory}
                    onActiveChange={setActiveCategory}
                    ariaLabel={t.transactions.donutAria(monthLabel(month, locale))}
                  />
                  <ul className="m-0 flex min-w-0 flex-1 list-none flex-col gap-1 p-0">
                    {categories.map((c) => (
                      <li
                        key={c.category}
                        className={`flex items-center gap-2 text-xs transition-opacity ${activeCategory && activeCategory !== c.category ? "opacity-40" : ""}`}
                        onMouseEnter={() => setActiveCategory(c.category)}
                        onMouseLeave={() => setActiveCategory(null)}
                      >
                        <span className="size-2.5 flex-none rounded-[3px]" style={{ background: categoryColorVar(c.category, categoryRanking) }} />
                        <span className="min-w-0 flex-1 truncate text-primary">{categoryLabel(t, c.category)}</span>
                        <span className="tabular-nums text-secondary">{eur0.format(c.amount)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </Card>

        {/* Interest */}
        <Card className="col-span-12 md:col-span-6 lg:col-span-4" title={t.dashboard.interestEarned}>
          {!transactions ? (
            <NoTransactions />
          ) : (
            <>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-2xl font-semibold tabular-nums text-gain">{eur.format(interestMonth)}</span>
                <span className="text-xs text-secondary">{t.dashboard.thisMonth}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-secondary">
                <span>
                  <span className="font-medium tabular-nums text-primary">{eur.format(transactions.interest.total)}</span>{" "}
                  {t.dashboard.totalEarned}
                </span>
                {aer !== undefined && (
                  <span className="font-medium text-gain">
                    {t.dashboard.aer(aer.toLocaleString("es-ES", { maximumFractionDigits: 2 }))}
                  </span>
                )}
              </div>
              <div className="mt-3">
                <Sparkline points={interestPoints} bars stroke="var(--gain)" height={48} ariaLabel={t.dashboard.last30Days} />
                <div className="mt-1 text-[11px] text-muted">{t.dashboard.last30Days}</div>
              </div>
            </>
          )}
        </Card>

        {/* Recent transactions */}
        <Card
          className={`col-span-12 ${upcoming.length > 0 ? "lg:col-span-7" : ""}`}
          title={t.dashboard.recentTransactions}
          action={{ to: "/transactions", label: t.dashboard.seeAll }}
        >
          {!transactions ? (
            <NoTransactions />
          ) : (
            <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
              {recent.map((tx) => (
                <TransactionRow key={tx.legId} tx={tx} locale={locale} ranking={categoryRanking} />
              ))}
            </ul>
          )}
        </Card>

        {upcoming.length > 0 && (
          <Card className="col-span-12 lg:col-span-5" title={t.dashboard.upcomingDividends} action={{ to: "/dividends", label: t.dashboard.seeAll }}>
            <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
              {upcoming.slice(0, RECENT_ROWS).map((u, i) => (
                <li key={`${u.name}-${u.payDate}-${i}`} className="flex items-center justify-between gap-3 py-2 text-sm first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{u.name}</div>
                    <div className="text-xs text-secondary">{u.payDate ? shortDate(u.payDate, locale) : "—"}</div>
                  </div>
                  <div className="font-semibold tabular-nums text-gain">
                    {u.amountEur !== null ? eur.format(u.amountEur) : "—"}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      {updatedAt && (
        <p className="mt-6 text-xs tabular-nums text-muted">
          {t.dashboard.updated(
            `${shortDate(updatedAt, locale)} · ${new Date(updatedAt).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`,
          )}
        </p>
      )}
    </>
  );
}

function NoTransactions() {
  const { t } = useTranslation();
  return (
    <p className="text-sm text-secondary">
      {t.dashboard.noTransactionsIntro}{" "}
      <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">.venv/bin/plout revolut</code>{" "}
      {t.dashboard.noTransactionsOutro}{" "}
      <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">data/revolut_transactions.json</code>.{" "}
      <Link to="/transactions" className="text-accent hover:underline">
        {t.dashboard.transactions} →
      </Link>
    </p>
  );
}

/** One compact statement row, shared by the dashboard list. */
export function TransactionRow({ tx, locale, ranking }: { tx: RevolutTransaction; locale: string; ranking: string[] }) {
  const { t } = useTranslation();
  const isIn = tx.amountEur > 0;
  const title = tx.merchant ?? tx.description ?? categoryLabel(t, tx.category);
  return (
    <li className={`flex items-center gap-3 py-2 first:pt-0 last:pb-0 ${tx.kind === "transfer" ? "opacity-60" : ""}`}>
      <span className="size-2.5 flex-none rounded-[3px]" style={{ background: categoryColorVar(tx.category, ranking) }} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-primary">{title}</div>
        <div className="truncate text-xs text-secondary">
          {categoryLabel(t, tx.category)} · {shortDate(tx.date, locale)}
          {tx.kind !== "expense" && tx.kind !== "income" ? ` · ${t.transactions.kinds[tx.kind]}` : ""}
        </div>
      </div>
      <div className={`text-sm font-semibold tabular-nums ${isIn ? "text-gain" : "text-primary"}`}>
        {isIn ? eurSigned.format(tx.amountEur) : eur.format(tx.amountEur)}
      </div>
    </li>
  );
}
