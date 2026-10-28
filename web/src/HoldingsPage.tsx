import { useMemo, useState } from "react";
import { useAppData } from "./data";
import { useTranslation } from "./i18n/LanguageContext";
import PortfolioChart from "./PortfolioChart";
import PortfolioKpis from "./PortfolioKpis";
import SectorDonut from "./SectorDonut";
import StocksTable from "./StocksTable";
import { sectorBreakdown, sectorVar, type Sector } from "./sectors";
import { useHoldings } from "./useHoldings";
import { eurSigned, gainLoss, shortDate } from "./format";
import { depositsInBenchmark } from "./performance";
import type { BenchmarkSeries, DepositsFile, PositionsFile } from "./types";

export default function HoldingsPage() {
  const { history, prices, deposits } = useAppData();
  const benchmark = prices?.benchmark ?? null;
  const {
    positions,
    revolut,
    stocks,
    showPositions,
    posBySymbol,
    revolutPosBySymbol,
    valueBySymbol,
    revolutValueEur,
    revolutPl,
    externalRealized,
    totalCashEur,
  } = useHoldings();
  const [activeSector, setActiveSector] = useState<Sector | null>(null);
  const { t } = useTranslation();
  // Same slices the donut renders, so a highlighted row takes its slice's color.
  const sectorSlices = useMemo(
    () =>
      sectorBreakdown(
        stocks,
        showPositions ? valueBySymbol : undefined,
        showPositions && totalCashEur > 0 ? totalCashEur : undefined,
      ),
    [stocks, showPositions, valueBySymbol, totalCashEur],
  );

  return (
    <>
      <h1 className="mb-5 font-display text-[26px] font-semibold tracking-tight">{t.nav.holdings}</h1>
      {showPositions && positions && (
        <PortfolioKpis
          summary={positions.summary}
          revolutCash={revolut ? revolut.cash : undefined}
          revolutValueEur={revolut ? revolutValueEur : undefined}
          revolutPlEur={revolutPl?.plEur}
          revolutCostEur={revolutPl?.costEur}
          revolutTodayPlEur={revolut?.totalTodayPlEur}
          externalRealizedByPlatform={externalRealized}
        />
      )}
      <div className="mb-7 flex flex-wrap items-stretch gap-x-4 gap-y-6">
        <div>
          <h2 className="mb-3 text-base font-semibold">{t.holdings.sectorDistribution}</h2>
          <SectorDonut
            stocks={stocks}
            active={activeSector}
            onActiveChange={setActiveSector}
            valueBySymbol={showPositions ? valueBySymbol : undefined}
            cashEur={showPositions && totalCashEur > 0 ? totalCashEur : undefined}
            sourceLabel={revolut ? t.holdings.sourceDegiroRevolut : t.holdings.sourceDegiro}
          />
        </div>
        <div className="flex min-w-[320px] flex-1 flex-col">
          <h2 className="mb-3 text-base font-semibold">{t.holdings.performance}</h2>
          <div className="flex-1">
            {/* Return, not value: the Dashboard already charts net worth, and
                here salary moving into savings would read as profit. */}
            <PortfolioChart history={history} mode="performance" benchmark={benchmark} />
          </div>
          {positions && deposits && benchmark && (
            <SinceOpeningNote positions={positions} deposits={deposits} benchmark={benchmark} />
          )}
        </div>
      </div>
      <StocksTable
        stocks={stocks}
        activeSector={activeSector}
        sectorColor={(sector) => {
          const slice = sectorSlices.find((s) => s.sector === sector);
          return sectorVar(slice ? slice.colorSlot : 0);
        }}
        positionsData={showPositions ? { posBySymbol, revolutPosBySymbol } : undefined}
      />
      {showPositions && positions && <UpdatedNote updatedAt={positions.updatedAt} />}
    </>
  );
}

/** DEGIRO since the first deposit against the same deposits in the index —
 * the long view the snapshot-based chart can't give, since snapshots only
 * started recently while deposits.json goes back to the account's opening. */
function SinceOpeningNote({
  positions,
  deposits,
  benchmark,
}: {
  positions: PositionsFile;
  deposits: DepositsFile;
  benchmark: BenchmarkSeries;
}) {
  const { t, lang } = useTranslation();
  const locale = { en: "en-GB", ca: "ca-ES", es: "es-ES" }[lang];
  const actual = positions.summary.accountPlEur;
  const first = deposits.movements[0];
  const index = depositsInBenchmark(deposits.movements, benchmark, positions.updatedAt);
  if (actual == null || !first || !index) return null;
  return (
    <p className="mt-2 text-xs tabular-nums text-muted">
      {t.holdings.sinceOpening(new Date(first.date).toLocaleDateString(locale, { month: "long", year: "numeric" }))}{" "}
      <span className={`font-medium ${gainLoss(actual)}`}>{eurSigned.format(actual)}</span>
      {" · "}
      {t.holdings.sameDepositsIn(benchmark.symbol)}{" "}
      <span className={`font-medium ${gainLoss(index.plEur)}`}>{eurSigned.format(index.plEur)}</span>
    </p>
  );
}

function UpdatedNote({ updatedAt }: { updatedAt: string }) {
  const { t } = useTranslation();
  const updated = new Date(updatedAt);
  return (
    <p className="mt-6 text-xs tabular-nums text-muted">
      {t.common.updated(
        shortDate(updatedAt),
        updated.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }),
      )}
    </p>
  );
}
