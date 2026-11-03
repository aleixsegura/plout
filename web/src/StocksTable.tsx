import { useMemo, useState, type CSSProperties } from "react";
import type { Position, RevolutPosition, Stock } from "./types";
import TypeIcon from "./TypeIcon";
import { useTranslation } from "./i18n/LanguageContext";
import { sectorFor, type Sector } from "./sectors";

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

type SortKey = "type" | "marketCap" | "value" | "pl" | "plPct";
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
  };
}

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
}: Props) {
  const { t } = useTranslation();
  const showPositions = positionsData !== undefined;
  // `chosenSort` is what the user clicked; until then the table shows the
  // heaviest position first. Derived rather than seeded into state because the
  // positions load after the first render, when there's no value to sort by.
  const [chosenSort, setChosenSort] = useState<SortState | null>(null);
  const sort = chosenSort ?? (showPositions ? DEFAULT_SORT : null);

  const toggleSort = (key: SortKey) => {
    setChosenSort(
      sort !== null && sort.key === key
        ? { key, dir: sort.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" },
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
            return (
              <tr
                key={s.symbol}
                className={`transition-[background-color,box-shadow] duration-150 hover:bg-hover ${
                  isActive ? "sector-active" : ""
                }`}
                style={isActive ? ({ "--row-tint": tint } as CSSProperties) : undefined}
              >
                <td className={`${TD} whitespace-normal`}>
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
                    <td className={`${TD} ${NUM} ${plClass}`}>
                      {plEur !== null ? eurSignedFormat.format(plEur) : "—"}
                    </td>
                    <td className={`${TD} ${NUM} ${plClass}`}>
                      {plPct !== null ? `${pctSignedFormat.format(plPct)}%` : "—"}
                    </td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
