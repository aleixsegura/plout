import { Fragment, useMemo, useState, type CSSProperties } from "react";
import type { Position, RevolutPosition, Stock } from "./types";
import type { LotBreakdown } from "./lots";
import TypeIcon from "./TypeIcon";
import { useTranslation } from "./i18n/LanguageContext";
import { sectorFor, type Sector } from "./sectors";
import { gainLoss } from "./format";
import { holdingsTotals, rowFigures } from "./holdingsTotals";

const numberFormat = new Intl.NumberFormat("es-ES", {
  maximumFractionDigits: 2,
  useGrouping: true,
});

const eurFormat = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
  useGrouping: true,
});

const eurSignedFormat = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
  useGrouping: true,
  signDisplay: "exceptZero",
});

const weightFormat = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

const pctSignedFormat = new Intl.NumberFormat("es-ES", {
  maximumFractionDigits: 2,
  useGrouping: true,
  signDisplay: "exceptZero",
});

function formatNumber(value: number | null): string {
  return value === null ? "—" : numberFormat.format(value);
}

const MARKET_CAP_UNITS: [number, string][] = [
  [1e12, "T"],
  [1e9, "B"],
  [1e6, "M"],
  [1e3, "K"],
];

// Compact market cap, e.g. 1,283,428,319,232 -> "1.28T".
function formatMarketCap(value: number | null): string {
  if (value === null) return "—";
  const abs = Math.abs(value);
  for (const [threshold, suffix] of MARKET_CAP_UNITS) {
    if (abs >= threshold) {
      const scaled = value / threshold;
      const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
      return `${scaled.toFixed(digits)}${suffix}`;
    }
  }
  return numberFormat.format(value);
}

function formatInCurrency(value: number, currency: string): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
    maximumFractionDigits: 2,
    useGrouping: true,
  }).format(value);
}

const TH =
  "border-b border-hairline-strong px-2.5 py-2.5 text-left text-[11.5px] font-semibold uppercase tracking-[0.04em] text-muted whitespace-nowrap";
const TD = "border-b border-hairline px-2.5 py-2 whitespace-nowrap";
const NUM = "text-right tabular-nums";

type SortKey = "type" | "marketCap" | "value" | "pl" | "plPct" | "today";
type SortDir = "asc" | "desc";

interface SortState {
  key: SortKey;
  dir: SortDir;
}

const DEFAULT_SORT: SortState = { key: "value", dir: "desc" };

interface Props {
  stocks: Stock[];
  /** Highlighted sector (holdings only); rows outside it get de-emphasized. */
  activeSector?: Sector | null;
  /** Color of a sector's donut slice, for the active-row tint. */
  sectorColor?: (sector: Sector) => string;
  /** Position joins (holdings only); absent ⇒ hide Qty/Avg/Value/P&L columns. */
  positionsData?: {
    posBySymbol: Map<string, Position>;
    revolutPosBySymbol: Map<string, RevolutPosition>;
    /** Open purchase lots per symbol; a row with lots expands on click. */
    lotsBySymbol?: Map<string, LotBreakdown>;
  };
  /** Per-asset analysis, from the row's sparkle button (and from a click on a
   * row that has no lots to expand). Absent ⇒ no analysis. */
  onAnalyse?: (stock: Stock) => void;
}

const chevronIcon = (
  <svg viewBox="0 0 24 24" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M9 6l6 6-6 6" />
  </svg>
);

const analyseIcon = (
  <svg viewBox="0 0 24 24" width={15} height={15} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 3.5l1.6 4.2 4.4 1.6-4.4 1.6L12 15.1l-1.6-4.2L6 9.3l4.4-1.6L12 3.5Z" />
    <path d="M18.5 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2Z" />
  </svg>
);

function SortableTh({
  label,
  sortKey,
  sort,
  onSort,
  numeric = false,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState | null;
  onSort: (key: SortKey) => void;
  numeric?: boolean;
}) {
  const active = sort !== null && sort.key === sortKey;
  return (
    <th
      className={numeric ? `${TH} ${NUM}` : TH}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className="inline-flex cursor-pointer items-center gap-1 uppercase transition-colors hover:text-primary"
      >
        {label}
        {active && (
          <span aria-hidden="true" className="text-[9px]">
            {sort.dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

export default function StocksTable({
  stocks,
  activeSector = null,
  sectorColor = () => "var(--sector-other)",
  positionsData,
  onAnalyse,
}: Props) {
  const { t, lang } = useTranslation();
  const showPositions = positionsData !== undefined;
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const toggleExpanded = (symbol: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  const locale = { en: "en-GB", ca: "ca-ES", es: "es-ES" }[lang];
  const lotDate = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
  const columnCount = 4 + (showPositions ? 7 : 0) + (onAnalyse ? 1 : 0);
  // `chosenSort` is what the user clicked; until then the table shows the
  // heaviest position first. Derived rather than seeded into state because the
  // positions load after the first render, when there's no value to sort by.
  const [chosenSort, setChosenSort] = useState<SortState | null>(null);
  const sort = chosenSort ?? (showPositions ? DEFAULT_SORT : null);

  const toggleSort = (key: SortKey) => {
    setChosenSort(
      sort !== null && sort.key === key
        ? { key, dir: sort.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "type" ? "asc" : "desc" },
    );
  };

  const sortedStocks = useMemo(() => {
    if (sort === null) return stocks;
    const sortValue = (s: Stock): string | number | null => {
      const p = positionsData?.posBySymbol.get(s.symbol);
      const rp = !p ? positionsData?.revolutPosBySymbol.get(s.symbol) : undefined;
      switch (sort.key) {
        case "type":
          return s.symbolType;
        case "marketCap":
          return s.marketCap;
        case "value":
          return p?.valueEur ?? rp?.valueEur ?? null;
        case "pl":
          return p?.plEur ?? rp?.plEur ?? null;
        case "today":
          return p?.todayPlEur ?? rp?.todayPlEur ?? null;
        case "plPct": {
          const pl = p?.plEur ?? rp?.plEur ?? null;
          const cost = p?.costEur ?? rp?.costEur;
          return pl !== null && cost && cost > 0 ? pl / cost : null;
        }
      }
    };
    const mul = sort.dir === "asc" ? 1 : -1;
    return [...stocks].sort((a, b) => {
      const va = sortValue(a);
      const vb = sortValue(b);
      // Missing values sink to the bottom regardless of direction.
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      if (typeof va === "string" && typeof vb === "string") return va.localeCompare(vb) * mul;
      return ((va as number) - (vb as number)) * mul;
    });
  }, [stocks, sort, positionsData]);

  // Weight is the share of the listed positions' value (cash excluded, as the
  // table lists no cash row).
  const { figuresBySymbol, totals } = useMemo(() => {
    const map = new Map<string, ReturnType<typeof rowFigures>>();
    if (positionsData)
      for (const s of stocks) {
        const p = positionsData.posBySymbol.get(s.symbol);
        map.set(s.symbol, rowFigures(p, p ? undefined : positionsData.revolutPosBySymbol.get(s.symbol)));
      }
    return { figuresBySymbol: map, totals: holdingsTotals([...map.values()]) };
  }, [stocks, positionsData]);

  return (
    // Every data cell is nowrap, so the table can't shrink below its min-content
    // width — scroll it inside the card instead of letting row borders spill past
    // the rounded edge.
    <div className="overflow-x-auto card">
      <table className="w-full border-collapse text-[13.5px] [&_tbody_tr:last-child>td]:border-b-0 [&_tr>*:first-child]:pl-4 [&_tr>*:last-child]:pr-4">
        <thead>
          <tr>
            <th className={TH}>{t.stocksTable.name}</th>
            <SortableTh label={t.stocksTable.type} sortKey="type" sort={sort} onSort={toggleSort} />
            <th className={`${TH} ${NUM}`}>{t.stocksTable.lastPrice}</th>
            <SortableTh
              label={t.stocksTable.marketCap}
              sortKey="marketCap"
              sort={sort}
              onSort={toggleSort}
              numeric
            />
            {showPositions && (
              <>
                <th className={`${TH} ${NUM}`}>{t.stocksTable.qty}</th>
                <th className={`${TH} ${NUM}`}>{t.stocksTable.avgPrice}</th>
                <SortableTh
                  label={t.stocksTable.valueEur}
                  sortKey="value"
                  sort={sort}
                  onSort={toggleSort}
                  numeric
                />
                <th className={`${TH} ${NUM}`}>{t.stocksTable.weight}</th>
                <SortableTh
                  label={t.stocksTable.todayEur}
                  sortKey="today"
                  sort={sort}
                  onSort={toggleSort}
                  numeric
                />
                <SortableTh
                  label={t.stocksTable.plEur}
                  sortKey="pl"
                  sort={sort}
                  onSort={toggleSort}
                  numeric
                />
                <SortableTh
                  label={t.stocksTable.plPct}
                  sortKey="plPct"
                  sort={sort}
                  onSort={toggleSort}
                  numeric
                />
              </>
            )}
            {onAnalyse && (
              <th className={TH}>
                <span className="sr-only">{t.analysis.title}</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {sortedStocks.map((s) => {
            const rowSector = sectorFor(s);
            const isActive = activeSector !== null && rowSector === activeSector;
            const tint = sectorColor(rowSector);
            const p = showPositions ? positionsData.posBySymbol.get(s.symbol) : undefined;
            // Revolut positions (crypto) have no cost basis: Qty/Value only.
            const rp =
              showPositions && !p ? positionsData.revolutPosBySymbol.get(s.symbol) : undefined;
            // P&L comes from DEGIRO (p) or, for crypto, a manual Revolut cost
            // basis (rp); the latter is absent until an avg price is configured.
            const plEur = p ? p.plEur : rp?.plEur ?? null;
            const costEur = p ? p.costEur : rp?.costEur;
            const plPct =
              plEur !== null && costEur && costEur > 0 ? (plEur / costEur) * 100 : null;
            const plClass = plEur === null ? "" : plEur >= 0 ? "text-gain" : "text-loss";
            const fig = figuresBySymbol.get(s.symbol) ?? rowFigures(undefined, undefined);
            const lots = p ? positionsData?.lotsBySymbol?.get(s.symbol) : undefined;
            const isOpen = lots !== undefined && expanded.has(s.symbol);
            const onRowClick = lots ? () => toggleExpanded(s.symbol) : onAnalyse ? () => onAnalyse(s) : undefined;
            return (
              <Fragment key={s.symbol}>
              <tr
                className={`group transition-[background-color,box-shadow] duration-150 hover:bg-hover ${
                  isActive ? "sector-active" : ""
                } ${onRowClick ? "cursor-pointer" : ""} ${isOpen ? "bg-hover" : ""}`}
                style={isActive ? ({ "--row-tint": tint } as CSSProperties) : undefined}
                onClick={
                  onRowClick
                    ? (e) => {
                        // Selecting a figure to copy it is not a click on the
                        // row, and nested controls keep their own click.
                        if (window.getSelection()?.toString()) return;
                        if ((e.target as HTMLElement).closest("a, button, input")) return;
                        onRowClick();
                      }
                    : undefined
                }
              >
                <td className={`${TD} whitespace-normal`}>
                  {lots && (
                    <button
                      type="button"
                      onClick={() => toggleExpanded(s.symbol)}
                      aria-expanded={isOpen}
                      aria-label={t.stocksTable.lotsToggle(s.name)}
                      title={t.stocksTable.lotsToggle(s.name)}
                      className={`-ml-1 mr-1 inline-flex align-[-2px] rounded p-0.5 text-muted transition-transform duration-150 hover:text-primary focus-visible:outline-2 focus-visible:outline-focus ${
                        isOpen ? "rotate-90" : ""
                      }`}
                    >
                      {chevronIcon}
                    </button>
                  )}
                  <span className="font-semibold text-primary">{s.name}</span>{" "}
                  <span className="text-xs text-muted">({s.symbol})</span>
                </td>
                <td className={TD}>
                  <TypeIcon symbol={s.symbol} symbolType={s.symbolType} />
                </td>
                <td className={`${TD} ${NUM}`}>
                  {(() => {
                    if (p && p.lastPrice !== null) return formatInCurrency(p.lastPrice, p.currency);
                    // Revolut crypto: the exporter's live Coinbase spot.
                    if (rp?.lastPrice != null)
                      return formatInCurrency(rp.lastPrice, rp.priceCurrency ?? "USD");
                    if (rp?.lastPriceEur != null) return eurFormat.format(rp.lastPriceEur);
                    if (s.lastPrice === null) return "—";
                    return s.currency ? formatInCurrency(s.lastPrice, s.currency) : formatNumber(s.lastPrice);
                  })()}
                </td>
                <td
                  className={`${TD} ${NUM}`}
                  title={s.marketCap === null ? undefined : numberFormat.format(s.marketCap)}
                >
                  {formatMarketCap(s.marketCap)}
                </td>
                {showPositions && (
                  <>
                    <td className={`${TD} ${NUM}`}>
                      {p ? numberFormat.format(p.quantity) : rp ? numberFormat.format(rp.quantity) : "—"}
                    </td>
                    <td className={`${TD} ${NUM}`}>
                      {p
                        ? formatInCurrency(p.avgPrice, p.currency)
                        : rp?.avgPrice != null
                          ? formatInCurrency(rp.avgPrice, rp.avgPriceCurrency ?? "EUR")
                          : "—"}
                    </td>
                    <td className={`${TD} ${NUM}`}>
                      {p
                        ? eurFormat.format(p.valueEur)
                        : rp && rp.valueEur !== null
                          ? eurFormat.format(rp.valueEur)
                          : "—"}
                    </td>
                    <td className={`${TD} ${NUM} text-secondary`}>
                      {fig.value !== null && totals.value > 0
                        ? `${weightFormat.format((fig.value / totals.value) * 100)}%`
                        : "—"}
                    </td>
                    <td className={`${TD} ${NUM} ${fig.today === null ? "" : gainLoss(fig.today)}`}>
                      {fig.today !== null ? eurSignedFormat.format(fig.today) : "—"}
                    </td>
                    <td className={`${TD} ${NUM} ${plClass}`}>
                      {plEur !== null ? eurSignedFormat.format(plEur) : "—"}
                    </td>
                    <td className={`${TD} ${NUM} ${plClass}`}>
                      {plPct !== null ? `${pctSignedFormat.format(plPct)}%` : "—"}
                    </td>
                  </>
                )}
                {onAnalyse && (
                  <td className={`${TD} w-px text-right`}>
                    <button
                      type="button"
                      onClick={() => onAnalyse(s)}
                      aria-label={t.analysis.analyseRow(s.name)}
                      title={t.analysis.analyseRow(s.name)}
                      className="rounded-md p-1 text-muted opacity-0 transition-opacity group-hover:opacity-100 hover:text-accent focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-focus"
                    >
                      {analyseIcon}
                    </button>
                  </td>
                )}
              </tr>
              {isOpen && p && (
                <LotRows
                  breakdown={lots}
                  position={p}
                  showAnalyse={onAnalyse !== undefined}
                  columnCount={columnCount}
                  lotDate={lotDate}
                />
              )}
              </Fragment>
            );
          })}
        </tbody>
        {showPositions && (
          <tfoot>
            <tr className="font-semibold">
              <td className={`${TD} border-t border-hairline-strong`} colSpan={6}>
                {t.stocksTable.total}
              </td>
              <td className={`${TD} ${NUM} border-t border-hairline-strong`}>{eurFormat.format(totals.value)}</td>
              <td className={`${TD} border-t border-hairline-strong`} />
              <td className={`${TD} ${NUM} border-t border-hairline-strong ${gainLoss(totals.today)}`}>
                {eurSignedFormat.format(totals.today)}
              </td>
              <td className={`${TD} ${NUM} border-t border-hairline-strong ${gainLoss(totals.pl)}`}>
                {eurSignedFormat.format(totals.pl)}
              </td>
              <td className={`${TD} ${NUM} border-t border-hairline-strong ${gainLoss(totals.pl)}`}>
                {totals.plPct !== null ? `${pctSignedFormat.format(totals.plPct)}%` : "—"}
              </td>
              {onAnalyse && <td className={TD} />}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

const LOT_CELL = "border-b border-hairline bg-elevated/60 px-2.5 py-1.5 text-[12.5px]";
const LOT_TD = `${LOT_CELL} whitespace-nowrap`;

/** One sub-row per open purchase lot, aligned under the position's columns,
 * plus a note when the ledger and DEGIRO don't account for the same shares. */
function LotRows({
  breakdown,
  position,
  showAnalyse,
  columnCount,
  lotDate,
}: {
  breakdown: LotBreakdown;
  position: Position;
  showAnalyse: boolean;
  columnCount: number;
  lotDate: (iso: string) => string;
}) {
  const { t } = useTranslation();
  const notes = [
    breakdown.unmatchedQuantity > 0 && t.stocksTable.lotsUnmatched(numberFormat.format(breakdown.unmatchedQuantity)),
    breakdown.soldSinceLedger > 0 && t.stocksTable.lotsSoldSince(numberFormat.format(breakdown.soldSinceLedger)),
    breakdown.hadSells && Math.abs(breakdown.lots.reduce((sum, l) => sum + l.plEur, 0) - position.plEur) > 1 &&
      t.stocksTable.lotsFifo,
  ].filter((n): n is string => typeof n === "string");
  return (
    <>
      {breakdown.lots.map((lot, i) => {
        const cls = lot.plEur >= 0 ? "text-gain" : "text-loss";
        return (
          <tr key={`${lot.date}-${i}`} className="text-secondary">
            <td className={`${LOT_TD} pl-10!`} colSpan={4}>
              <span className="text-muted">{t.stocksTable.lotBought}</span>{" "}
              <span className="tabular-nums text-primary">{lotDate(lot.date)}</span>
              {lot.feesEur > 0 && (
                <span className="ml-2 text-xs text-muted">· {t.stocksTable.lotFees(eurFormat.format(lot.feesEur))}</span>
              )}
            </td>
            <td className={`${LOT_TD} ${NUM}`}>{numberFormat.format(lot.quantity)}</td>
            <td className={`${LOT_TD} ${NUM}`}>{formatInCurrency(lot.price, lot.currency)}</td>
            <td className={`${LOT_TD} ${NUM}`}>{eurFormat.format(lot.valueEur)}</td>
            <td className={LOT_TD} colSpan={2} />
            <td className={`${LOT_TD} ${NUM} ${cls}`}>{eurSignedFormat.format(lot.plEur)}</td>
            <td className={`${LOT_TD} ${NUM} ${cls}`}>
              {lot.plPct !== null ? `${pctSignedFormat.format(lot.plPct * 100)}%` : "—"}
            </td>
            {showAnalyse && <td className={LOT_TD} />}
          </tr>
        );
      })}
      {notes.length > 0 && (
        <tr>
          <td className={`${LOT_CELL} pl-10! text-xs text-muted`} colSpan={columnCount}>
            {notes.map((n) => (
              <p key={n}>{n}</p>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
