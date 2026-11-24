import { useEffect, useMemo, useState } from "react";
import Card from "./Card";
import Bars, { type Bar } from "./charts/Bars";
import Donut from "./charts/Donut";
import HBar from "./charts/HBar";
import StepArea from "./charts/StepArea";
import useContainerHeight from "./charts/useContainerHeight";
import { useAppData } from "./data";
import { capFirst, eur, eur0, eurSigned, monthLabel, monthTick, pct1, pctSigned } from "./format";
import { useTranslation } from "./i18n/LanguageContext";
import KpiCard, { SUB_DENSE } from "./KpiCard";
import {
  CATEGORY_ORDER,
  categoryColorVar,
  categoryLabel,
  daysElapsed,
  daysSinceFirst,
  deltaPct,
  expensesByCategory,
  inMonth,
  interestInMonth,
  interestSeries,
  localDay,
  matchesQuery,
  netOfRows,
  monthKey,
  monthsBack,
  previousMonth,
} from "./transactions";
import type { RevolutTransaction, TransactionMonth } from "./types";

const LOCALE = { en: "en-GB", ca: "ca-ES", es: "es-ES" } as const;
const ALL = "all";
const BAR_MONTHS = 12;
/** Fewest months the bar chart shows, even when the history is shorter. */
const MIN_BAR_MONTHS = 6;
/** Monthly bars: the open month in full colour, the rest in a pale tint. */
const BAR_ON = "var(--chart-2)";
const BAR_OFF = "var(--chart-2-soft)";
const INTEREST_DAYS = 90;
/** Rows rendered before "show more": a long month or all time stays snappy. */
const ROW_PAGE = 150;
/** Daily-interest chart height when it can't borrow the table's (stacked layout). */
const INTEREST_CHART_MIN = 180;

type KindFilter = "all" | "expense" | "income" | "internal";

const TH =
  "border-b border-hairline-strong px-2.5 py-2.5 text-left text-[11.5px] font-semibold uppercase tracking-[0.04em] text-muted whitespace-nowrap";
const TD = "border-b border-hairline px-2.5 py-2";
const NUM = "text-right tabular-nums";
/** The transaction list scrolls inside a box this tall (about 12 rows) so a
 * long month never stretches the page; the header row stays pinned. */
const LIST_WRAP = "max-h-[34rem] overflow-auto card";
const TH_STICKY = "sticky top-0 z-[1] bg-surface";
const TABLE = "w-full border-collapse text-[13.5px] [&_tr>*:first-child]:pl-4 [&_tr>*:last-child]:pr-4";

const chipClass = (active: boolean) =>
  `rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-focus ${
    active ? "border-accent bg-accent-subtle text-accent" : "border-hairline bg-surface text-secondary hover:border-hairline-strong hover:text-primary"
  }`;

const navButton =
  "flex size-8 items-center justify-center rounded-md border border-hairline bg-surface text-secondary hover:border-hairline-strong hover:text-primary disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-focus";

function emptyMonth(month: string): TransactionMonth {
  return { month, expenses: 0, income: 0, interest: 0, fees: 0, net: 0, count: 0, byCategory: {} };
}

export default function TransactionsPage() {
  const { transactions: file } = useAppData();
  const { t, lang } = useTranslation();
  const locale = LOCALE[lang];

  const months = useMemo(() => {
    const have = new Set((file?.months ?? []).map((m) => m.month));
    const keys = [...have];
    const current = monthKey();
    if (!have.has(current)) keys.push(current);
    return keys.sort().reverse();
  }, [file]);

  const [period, setPeriod] = useState<string>(() => monthKey());
  const [category, setCategory] = useState<string | null>(null);
  const [kind, setKind] = useState<KindFilter>("all");
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [limit, setLimit] = useState(ROW_PAGE);
  useEffect(() => setLimit(ROW_PAGE), [period, category, kind, query]);
  const [interestChartRef, interestChartHeight] = useContainerHeight(INTEREST_CHART_MIN);

  const isAll = period === ALL;
  const monthIndex = months.indexOf(period);
  const summary: TransactionMonth = useMemo(() => {
    if (!file) return emptyMonth(period);
    if (!isAll) return file.months.find((m) => m.month === period) ?? emptyMonth(period);
    const total = emptyMonth(ALL);
    for (const m of file.months) {
      total.expenses += m.expenses;
      total.income += m.income;
      total.interest += m.interest;
      total.fees += m.fees;
      total.count += m.count;
      for (const [c, v] of Object.entries(m.byCategory)) total.byCategory[c] = (total.byCategory[c] ?? 0) + v;
    }
    total.net = total.income + total.interest - total.expenses - total.fees;
    return total;
  }, [file, period, isAll]);
  const previous = !isAll ? file?.months.find((m) => m.month === previousMonth(period)) : undefined;
  const spentDelta = deltaPct(summary.expenses, previous?.expenses);
  const days = isAll ? daysSinceFirst(file?.transactions ?? []) : Math.max(daysElapsed(period), 1);

  // Rows of the period, then the table's own filters on top.
  const periodRows = useMemo(() => {
    const all = file?.transactions ?? [];
    return isAll ? all : inMonth(all, period);
  }, [file, period, isAll]);

  const categoryRows = useMemo(() => expensesByCategory(periodRows), [periodRows]);
  const extraCategories = useMemo(() => {
    const known = new Set<string>(CATEGORY_ORDER);
    const out: string[] = [];
    for (const m of file?.months ?? []) for (const c of Object.keys(m.byCategory)) if (!known.has(c) && !out.includes(c)) out.push(c);
    return out;
  }, [file]);

  // Months on the bar chart: the last twelve, minus the empty ones before the
  // history starts, so a short history doesn't leave half the plot blank.
  const barMonths = useMemo(() => {
    const keys = monthsBack(BAR_MONTHS, isAll ? monthKey() : period > monthKey() ? period : monthKey());
    const spent = (k: string) => (file?.months.find((m) => m.month === k)?.expenses ?? 0) > 0;
    const first = keys.findIndex(spent);
    return keys.slice(Math.min(first < 0 ? keys.length : first, keys.length - MIN_BAR_MONTHS));
  }, [file, period, isAll]);

  // Colors are dealt out by spend over the chart's months (largest first), so a
  // category keeps its color when another month is opened; categories with no
  // spend in that window follow.
  const ranking = useMemo(() => {
    const totals = new Map<string, number>();
    for (const key of barMonths) {
      const m = file?.months.find((x) => x.month === key);
      for (const [c, v] of Object.entries(m?.byCategory ?? {})) if (v > 0) totals.set(c, (totals.get(c) ?? 0) + v);
    }
    const spent = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
    return [...spent, ...[...CATEGORY_ORDER, ...extraCategories].filter((c) => !spent.includes(c))];
  }, [file, barMonths, extraCategories]);
  const color = (c: string) => categoryColorVar(c, ranking);

  const legendCategories = useMemo(() => {
    const seen = new Set<string>();
    for (const m of file?.months ?? []) for (const c of Object.keys(m.byCategory)) seen.add(c);
    return ranking.filter((c) => seen.has(c));
  }, [file, ranking]);
  // One bar per month (the per-category split lives in the donut and the
  // breakdown below, and in each bar's tooltip). A category chip narrows the
  // bars to that category.
  const bars: Bar[] = useMemo(() => {
    return barMonths.map((key, i) => {
      const m = file?.months.find((x) => x.month === key);
      const label = monthTick(key, locale, i === 0);
      const on = !isAll && key === period;
      const fill = on ? BAR_ON : BAR_OFF;
      if (category) return { key, label, value: m?.byCategory[category] ?? 0, color: fill };
      const breakdown = Object.entries(m?.byCategory ?? {})
        .filter(([, v]) => v > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([c, v]) => ({ key: c, value: v, color: categoryColorVar(c, ranking), label: categoryLabel(t, c) }));
      return { key, label, value: m?.expenses ?? 0, color: fill, breakdown };
    });
  }, [file, barMonths, category, period, isAll, ranking, locale, t]);

  // What "x of y" counts against: interest rows only show under Income.
  const listable = useMemo(() => (kind === "all" ? periodRows.filter((tx) => tx.kind !== "interest") : periodRows), [periodRows, kind]);

  const tableRows = useMemo(() => {
    return periodRows.filter((tx) => {
      if (kind === "expense" && tx.kind !== "expense" && tx.kind !== "refund" && tx.kind !== "fee") return false;
      if (kind === "income" && tx.kind !== "income" && tx.kind !== "interest") return false;
      if (kind === "internal" && tx.kind !== "transfer") return false;
      if (kind === "all" && tx.kind === "interest") return false;
      if (category && tx.category !== category) return false;
      return matchesQuery(tx, query);
    });
  }, [periodRows, kind, category, query]);

  // Group the visible rows by day with a day subtotal of what was spent.
  const groups = useMemo(() => {
    const out: { day: string; rows: RevolutTransaction[]; spent: number }[] = [];
    for (const tx of tableRows.slice(0, limit)) {
      const day = tx.date.slice(0, 10);
      let g = out[out.length - 1];
      if (!g || g.day !== day) {
        g = { day, rows: [], spent: 0 };
        out.push(g);
      }
      g.rows.push(tx);
      if (tx.kind === "expense" || tx.kind === "refund" || tx.kind === "fee") g.spent -= tx.amountEur;
    }
    return out;
  }, [tableRows, limit]);
  const filtered = query.trim() !== "" || category !== null || kind !== "all";
  const filteredNet = useMemo(() => netOfRows(tableRows, kind === "internal"), [tableRows, kind]);

  // Interest section.
  const interest = file?.interest;
  const savings = file?.accounts.savings;
  const interestDaily = useMemo(() => interestSeries(file, INTEREST_DAYS), [file]);
  const last30 = useMemo(() => interestSeries(file, 30).reduce((s, d) => s + d.amount, 0), [file]);
  const interestByMonth = useMemo(() => {
    const daily = interest?.daily ?? [];
    return (interest?.byMonth ?? [])
      .slice()
      .reverse()
      .map((m) => {
        const inMonthDays = daily.filter((d) => d.date.startsWith(m.month));
        const balances = inMonthDays.map((d) => d.balanceEur).filter((b): b is number => typeof b === "number");
        const avgBalance = balances.length ? balances.reduce((s, b) => s + b, 0) / balances.length : null;
        const rate = avgBalance && inMonthDays.length ? (m.amount / avgBalance) * (365 / inMonthDays.length) * 100 : null;
        return { ...m, days: inMonthDays.length, avgBalance, rate };
      });
  }, [interest]);

  if (file === null) {
    return (
      <div>
        <h1 className="mb-2 font-display text-[26px] font-semibold tracking-tight">{t.transactions.title}</h1>
        <p className="max-w-prose text-secondary">
          {t.transactions.noDataIntro}{" "}
          <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">.venv/bin/plout revolut</code>{" "}
          {t.transactions.noDataOutro}{" "}
          <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">data/revolut_transactions.json</code>.
        </p>
      </div>
    );
  }

  const periodLabel = isAll ? t.transactions.allTime : monthLabel(period, locale);
  const donutSlices = categoryRows.map((c) => ({ key: c.category, label: categoryLabel(t, c.category), value: c.amount, color: color(c.category) }));

  return (
    <>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="font-display text-[26px] font-semibold tracking-tight">{t.transactions.title}</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={navButton}
            aria-label={t.transactions.prevMonth}
            disabled={isAll || monthIndex === -1 || monthIndex >= months.length - 1}
            onClick={() => setPeriod(months[monthIndex + 1])}
          >
            ‹
          </button>
          <span className="min-w-[10rem] text-center font-display text-[17px] font-semibold">{periodLabel}</span>
          <button
            type="button"
            className={navButton}
            aria-label={t.transactions.nextMonth}
            disabled={isAll || monthIndex <= 0}
            onClick={() => setPeriod(months[monthIndex - 1])}
          >
            ›
          </button>
          <button type="button" className={`ml-2 ${chipClass(isAll)}`} onClick={() => setPeriod(isAll ? monthKey() : ALL)}>
            {t.transactions.allTime}
          </button>
        </div>
      </header>

      <section className="panel-grid mb-5 grid [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]" aria-label={t.transactions.title}>
        <KpiCard
          dense
          label={t.transactions.spent}
          value={eur.format(summary.expenses)}
          sub={
            spentDelta !== null ? (
              <span className={`${SUB_DENSE} ${spentDelta > 0 ? "text-loss" : "text-gain"}`}>
                {t.transactions.vsPrev(`${pctSigned.format(spentDelta)}%`)}
              </span>
            ) : undefined
          }
        />
        <KpiCard dense label={t.transactions.income} value={eur.format(summary.income)} valueClass="text-gain" />
        <KpiCard dense label={t.transactions.net} value={eurSigned.format(summary.net)} valueClass={summary.net >= 0 ? "text-gain" : "text-loss"} />
        <KpiCard dense label={t.transactions.count} value={String(summary.count)} />
        <KpiCard dense label={t.transactions.avgPerDay} value={eur.format(summary.expenses / days)} />
      </section>

      <div className="panel-grid grid grid-cols-12">
        <Card className="col-span-12" title={t.transactions.monthlyExpenses}>
          <Bars
            bars={bars}
            height={240}
            showValues
            highlightKey={isAll ? null : period}
            highlightColor={BAR_ON}
            onSelect={(key) => setPeriod(key)}
            formatValue={(v) => eur0.format(v)}
            tooltipTitle={(b) => monthLabel(b.key, locale)}
            ariaLabel={t.transactions.barsAria}
          />
          <div className="mt-3 flex flex-wrap gap-1.5">
            {legendCategories.map((c) => (
              <button
                key={c}
                type="button"
                className={`${chipClass(category === c)} inline-flex items-center gap-1.5`}
                onClick={() => setCategory(category === c ? null : c)}
                aria-pressed={category === c}
              >
                <span
                  className="size-2 rounded-[2px]"
                  style={{ background: color(c) }}
                  aria-hidden
                />
                {categoryLabel(t, c)}
              </button>
            ))}
          </div>
        </Card>

        <Card className="col-span-12 lg:col-span-5" title={t.transactions.byCategory}>
          {categoryRows.length === 0 ? (
            <p className="text-sm text-secondary">{t.transactions.noRows}</p>
          ) : (
            <div className="flex items-center justify-center">
              <Donut
                slices={donutSlices}
                size={200}
                thickness={28}
                active={activeCategory}
                onActiveChange={setActiveCategory}
                center={{
                  value: eur0.format(activeCategory ? categoryRows.find((c) => c.category === activeCategory)?.amount ?? 0 : summary.expenses),
                  label: activeCategory ? categoryLabel(t, activeCategory) : periodLabel,
                }}
                ariaLabel={t.transactions.donutAria(periodLabel)}
              />
            </div>
          )}
        </Card>

        <Card className="col-span-12 lg:col-span-7" title={t.transactions.breakdown}>
          {categoryRows.length === 0 ? (
            <p className="text-sm text-secondary">{t.transactions.noRows}</p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
              {categoryRows.map((c) => (
                <li
                  key={c.category}
                  className={`flex cursor-pointer flex-col gap-1 transition-opacity ${activeCategory && activeCategory !== c.category ? "opacity-40" : ""}`}
                  onMouseEnter={() => setActiveCategory(c.category)}
                  onMouseLeave={() => setActiveCategory(null)}
                  onClick={() => setCategory(category === c.category ? null : c.category)}
                  onFocus={() => setActiveCategory(c.category)}
                  onBlur={() => setActiveCategory(null)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setCategory(category === c.category ? null : c.category);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-pressed={category === c.category}
                >
                  <div className="flex items-center gap-2 text-sm">
                    <span className="size-2.5 flex-none rounded-[3px]" style={{ background: color(c.category) }} aria-hidden />
                    <span className="min-w-0 flex-1 truncate font-medium">{categoryLabel(t, c.category)}</span>
                    <span className="text-xs text-muted">{c.count}</span>
                    <span className="w-12 text-right text-xs tabular-nums text-secondary">{pct1.format(c.fraction * 100)}%</span>
                    <span className="w-24 text-right font-semibold tabular-nums">{eur.format(c.amount)}</span>
                  </div>
                  <HBar fraction={c.fraction} color={color(c.category)} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* The table and the interest card stand on their own below the panel:
          inside panel-grid they'd need to be cards themselves, or the grid's
          rules double up on the table's own and never close around the rest. */}
      <section className="mt-6" aria-label={t.transactions.title}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
            }}
            placeholder={t.transactions.searchPlaceholder}
            aria-label={t.transactions.search}
            className="h-9 min-w-[16rem] flex-1 rounded-md border border-hairline bg-surface px-3 text-sm text-primary placeholder:text-muted focus:border-hairline-strong focus:outline-none focus-visible:outline-2 focus-visible:outline-focus sm:max-w-sm"
          />
          <div className="flex flex-wrap gap-1.5">
            {(["all", "expense", "income", "internal"] as KindFilter[]).map((k) => (
              <button key={k} type="button" className={chipClass(kind === k)} onClick={() => setKind(k)} aria-pressed={kind === k}>
                {k === "all" ? t.transactions.all : k === "expense" ? t.transactions.expensesOnly : k === "income" ? t.transactions.incomeOnly : t.transactions.internal}
              </button>
            ))}
            {category && (
              <button type="button" className={`${chipClass(true)} inline-flex items-center gap-1.5`} onClick={() => setCategory(null)}>
                <span className="size-2 rounded-[2px]" style={{ background: color(category) }} aria-hidden />
                {categoryLabel(t, category)} ×
              </button>
            )}
          </div>
          <span className="ml-auto text-xs tabular-nums text-muted">
            {filtered && tableRows.length > 0 && (
              <span className={`mr-3 font-medium ${filteredNet >= 0 ? "text-gain" : "text-loss"}`}>{eurSigned.format(filteredNet)}</span>
            )}
            {t.transactions.rowsShown(tableRows.length, listable.length)}
          </span>
        </div>

        <div className={LIST_WRAP}>
          <table className={TABLE}>
            <thead>
              <tr>
                <th className={`${TH} ${TH_STICKY}`}>{t.transactions.date}</th>
                <th className={`${TH} ${TH_STICKY}`}>{t.transactions.merchant}</th>
                <th className={`${TH} ${TH_STICKY}`}>{t.transactions.category}</th>
                <th className={`${TH} ${TH_STICKY} ${NUM}`}>{t.transactions.amount}</th>
              </tr>
            </thead>
            <tbody>
              {groups.length === 0 && (
                <tr>
                  <td className={`${TD} text-secondary`} colSpan={4}>
                    {t.transactions.noRows}
                  </td>
                </tr>
              )}
              {groups.map((g) => (
                <GroupRows key={g.day} group={g} locale={locale} color={color} />
              ))}
            </tbody>
          </table>
        </div>
        {tableRows.length > limit && (
          <button type="button" className={`mt-3 ${chipClass(false)}`} onClick={() => setLimit((l) => l + ROW_PAGE)}>
            {t.transactions.showMore(Math.min(ROW_PAGE, tableRows.length - limit))}
          </button>
        )}
      </section>

      <Card className="mt-6" title={t.transactions.interest.title}>
        {!interest || interest.daily.length === 0 ? (
          <p className="text-sm text-secondary">{t.transactions.interest.noInterest}</p>
        ) : (
          <>
            <div className="panel-grid mb-4 grid [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
              <KpiCard dense label={t.transactions.interest.thisMonth} value={eur.format(interestInMonth(file, monthKey()))} valueClass="text-gain" />
              <KpiCard dense label={t.transactions.interest.last30} value={eur.format(last30)} valueClass="text-gain" />
              <KpiCard dense label={t.transactions.interest.total} value={eur.format(interest.total)} valueClass="text-gain" />
              {savings?.aer !== undefined && (
                <KpiCard
                  dense
                  label={t.transactions.interest.rate}
                  value={`${savings.aer.toLocaleString("es-ES", { maximumFractionDigits: 2 })}% AER`}
                  sub={
                    savings.interestRate !== undefined ? (
                      <span className={`${SUB_DENSE} text-secondary`}>{savings.interestRate.toLocaleString("es-ES", { maximumFractionDigits: 2 })}% NIR</span>
                    ) : undefined
                  }
                />
              )}
              {savings && <KpiCard dense label={t.transactions.interest.balance} value={eur.format(savings.balanceEur)} />}
            </div>
            <div className="grid gap-x-10 gap-y-8 lg:grid-cols-12">
              <div className="flex min-w-0 flex-col lg:col-span-7">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-muted">{t.transactions.interest.daily}</h3>
                {/* The chart fills whatever height the month table beside it
                    takes, so both start and end on the same line. It is drawn
                    absolutely so it never props the row open itself. */}
                <div ref={interestChartRef} className="relative flex-1" style={{ minHeight: INTEREST_CHART_MIN }}>
                  <div className="absolute inset-0">
                    <StepArea
                      points={interestDaily.map((d, i) => ({
                        key: d.date,
                        label: (i + 1) % 15 === 0 ? localDay(d.date).toLocaleDateString(locale, { day: "numeric", month: "short" }) : "",
                        value: d.amount,
                      }))}
                      height={interestChartHeight}
                      formatValue={(v) => eur.format(v)}
                      tooltipTitle={(p) => localDay(p.key).toLocaleDateString(locale, { day: "numeric", month: "long" })}
                      ariaLabel={t.transactions.interest.dailyAria}
                    />
                  </div>
                </div>
              </div>
              <div className="min-w-0 lg:col-span-5">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-muted">{t.transactions.interest.byMonth}</h3>
                <div className="overflow-x-auto">
                  {/* outer cells flush with the heading, like the chart beside it */}
                  <table className="w-full border-collapse text-[13px] [&_tr>*:first-child]:pl-0 [&_tr>*:last-child]:pr-0">
                    <thead>
                      <tr>
                        <th className={TH}>{t.transactions.interest.month}</th>
                        <th className={`${TH} ${NUM}`}>{t.transactions.interest.days}</th>
                        <th className={`${TH} ${NUM}`}>{t.transactions.interest.amount}</th>
                        <th className={`${TH} ${NUM}`}>{t.transactions.interest.effectiveRate}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {interestByMonth.map((m) => (
                        <tr key={m.month}>
                          <td className={TD}>{monthLabel(m.month, locale)}</td>
                          <td className={`${TD} ${NUM} text-secondary`}>{m.days}</td>
                          <td className={`${TD} ${NUM} font-medium text-gain`}>{eur.format(m.amount)}</td>
                          <td className={`${TD} ${NUM} text-secondary`}>{m.rate !== null ? `${pct1.format(m.rate)}%` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}

function GroupRows({
  group,
  locale,
  color,
}: {
  group: { day: string; rows: RevolutTransaction[]; spent: number };
  locale: string;
  color: (c: string) => string;
}) {
  const { t } = useTranslation();
  const dayLabel = capFirst(localDay(group.day).toLocaleDateString(locale, { weekday: "short", day: "numeric", month: "short" }));
  return (
    <>
      <tr className="bg-elevated">
        <td className={`${TD} text-xs font-semibold text-secondary`} colSpan={3}>
          {dayLabel}
        </td>
        <td className={`${TD} ${NUM} text-xs text-muted`}>
          {group.spent > 0 ? `${eur.format(group.spent)} ${t.transactions.dayTotal}` : ""}
        </td>
      </tr>
      {group.rows.map((tx) => {
        const muted = tx.kind === "transfer";
        const isIn = tx.amountEur > 0;
        const title = tx.merchant ?? tx.description ?? categoryLabel(t, tx.category);
        const sub = tx.merchant && tx.description && tx.description !== tx.merchant ? tx.description : null;
        return (
          <tr key={tx.legId} className={`hover:bg-hover ${muted ? "opacity-60" : ""}`}>
            <td className={`${TD} whitespace-nowrap tabular-nums text-secondary`}>
              {new Date(tx.date).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}
            </td>
            <td className={`${TD} max-w-[28rem]`}>
              <div className="truncate font-medium text-primary">{title}</div>
              <div className="truncate text-xs text-secondary">
                {[sub, tx.currency !== "EUR" ? `${Math.abs(tx.amount).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${tx.currency}` : null, tx.cardLastFour ? `•••• ${tx.cardLastFour}` : null, tx.kind !== "expense" && tx.kind !== "income" ? t.transactions.kinds[tx.kind] : null]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </td>
            <td className={`${TD} whitespace-nowrap`}>
              <span className="inline-flex items-center gap-1.5 text-xs text-secondary">
                <span className="size-2 rounded-[2px]" style={{ background: color(tx.category) }} aria-hidden />
                {categoryLabel(t, tx.category)}
              </span>
            </td>
            <td className={`${TD} ${NUM} whitespace-nowrap font-semibold ${isIn ? "text-gain" : "text-primary"}`}>
              {isIn ? eurSigned.format(tx.amountEur) : eur.format(tx.amountEur)}
            </td>
          </tr>
        );
      })}
    </>
  );
}
