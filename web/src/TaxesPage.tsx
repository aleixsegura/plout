import { useRef, useState, type KeyboardEvent } from "react";
import { useAppData } from "./data";
import { useTranslation } from "./i18n/LanguageContext";
import type { TaxAsset, TaxNotice, TaxStakingReward, TaxYear } from "./types";

/** Which fiscal year was last open, so the page comes back where you left it. */
const YEAR_KEY = "taxesYear";

/** Staking rewards shown before the list has to be expanded. */
const REWARDS_PREVIEW = 5;

const CARD = "flex flex-col gap-1 card px-4 py-3.5";
const LABEL = "text-xs font-semibold uppercase tracking-[0.03em] text-muted";
const VALUE = "text-[1.35rem] font-semibold leading-tight tabular-nums";
const SUB = "text-[0.85rem] tabular-nums text-secondary";

const TH =
  "border-b border-hairline-strong px-2.5 py-2.5 text-left text-[11.5px] font-semibold uppercase tracking-[0.04em] text-muted whitespace-nowrap";
// Split so a cell that needs to wrap can leave `whitespace-nowrap` off rather
// than try to override it: two Tailwind utilities for the same property have
// the same specificity, so appending `whitespace-normal` loses to whichever
// the generated stylesheet happens to emit last.
const TD_BASE = "border-b border-hairline px-2.5 py-2";
const TD = `${TD_BASE} whitespace-nowrap`;
const NUM = "text-right tabular-nums";

const TABLE_WRAP = "overflow-x-auto card";
const TABLE =
  "w-full border-collapse text-[13.5px] [&_tr>*:first-child]:pl-4 [&_tr>*:last-child]:pr-4";

const eur = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
  useGrouping: true,
});

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** A gain is green, a loss red, and an exact zero neither. */
function resultClass(value: number): string {
  if (value > 0) return "text-gain";
  if (value < 0) return "text-loss";
  return "text-muted";
}

/** Quantities are shares (usually whole) or crypto (up to 8 decimals), and a
 * fixed precision would either truncate the latter or pad the former. */
function quantity(value: number): string {
  return value.toLocaleString("es-ES", { maximumFractionDigits: 8 });
}

export default function TaxesPage() {
  const { taxes } = useAppData();
  const { t } = useTranslation();
  const [year, setYear] = useState<string | null>(() => {
    try {
      return localStorage.getItem(YEAR_KEY);
    } catch {
      return null;
    }
  });

  // Not exported yet — mirror the silent-degrade guidance on the other pages.
  if (taxes === null) {
    return (
      <div>
        <h1 className="mb-2 font-display text-[26px] font-semibold tracking-tight">{t.taxes.title}</h1>
        <p className="max-w-prose text-secondary">
          {t.taxes.noDataIntro}{" "}
          <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">
            .venv/bin/plout taxes
          </code>{" "}
          {t.taxes.noDataOutro}{" "}
          <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">
            data/taxes.json
          </code>
          .
        </p>
      </div>
    );
  }

  if (taxes.years.length === 0) {
    return (
      <div>
        <h1 className="mb-2 font-display text-[26px] font-semibold tracking-tight">{t.taxes.title}</h1>
        <p className="text-secondary">{t.taxes.noYears}</p>
      </div>
    );
  }

  // A remembered year disappears as soon as the data behind it does.
  const active = taxes.years.find((y) => y.year === year) ?? taxes.years[0];
  const selectYear = (next: string) => {
    setYear(next);
    try {
      localStorage.setItem(YEAR_KEY, next);
    } catch {
      /* private mode: the choice still applies this session */
    }
  };

  return (
    <div>
      <h1 className="mb-1 font-display text-[26px] font-semibold tracking-tight">{t.taxes.title}</h1>
      <p className="mb-5 max-w-prose text-sm text-secondary">{t.taxes.description}</p>

      <YearTabs
        years={taxes.years}
        active={active.year}
        onChange={selectYear}
        label={t.taxes.yearsAria}
      />

      <div
        role="tabpanel"
        id={`taxes-panel-${active.year}`}
        aria-labelledby={`taxes-tab-${active.year}`}
      >
        <YearPanel year={active} />
      </div>

      <UpdatedNote updatedAt={taxes.updatedAt} />
    </div>
  );
}

/** Staking credits arrive every few days, so a year's worth buries the rest of
 * the page: show a handful and let the whole list out on request. */
function StakingRewards({ rewards }: { rewards: TaxStakingReward[] }) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? rewards : rewards.slice(0, REWARDS_PREVIEW);

  return (
    <>
      <h3 className="mb-2 text-[13.5px] font-semibold text-secondary">{t.taxes.stakingTitle}</h3>
      <div className={`mb-3 ${TABLE_WRAP}`}>
        <table className={`${TABLE} [&_tbody_tr:last-child>td]:border-b-0`}>
          <thead>
            <tr>
              <th className={TH}>{t.taxes.date}</th>
              <th className={TH}>{t.taxes.asset}</th>
              <th className={`${TH} ${NUM}`}>{t.taxes.quantity}</th>
              <th className={`${TH} ${NUM}`}>{t.taxes.valueAtReceipt}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r, i) => (
              <tr key={`${r.date}-${r.symbol}-${i}`} className="hover:bg-hover">
                <td className={`${TD} tabular-nums text-secondary`}>
                  {formatDate(r.date)}
                  {r.approximate && (
                    <span className="ml-1.5 text-xs text-muted">{t.taxes.approximate}</span>
                  )}
                </td>
                <td className={TD}>{r.symbol}</td>
                <td className={`${TD} ${NUM} text-secondary`}>{quantity(r.quantity)}</td>
                <td className={`${TD} ${NUM}`}>{eur.format(r.valueEur)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rewards.length > REWARDS_PREVIEW && (
        <button
          type="button"
          aria-expanded={showAll}
          className="mb-3 text-sm font-medium text-accent hover:underline focus-visible:outline-2 focus-visible:outline-focus"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? t.taxes.showFewer : t.taxes.showAllRewards(rewards.length)}
        </button>
      )}
      <p className="mb-6 max-w-prose text-[13px] text-muted">{t.taxes.stakingNote}</p>
    </>
  );
}

const tabClass = (active: boolean) =>
  [
    "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13.5px] font-medium",
    "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus",
    active
      ? "border-primary text-primary"
      : "border-transparent text-secondary hover:border-hairline-strong hover:text-primary",
  ].join(" ");

function YearTabs({
  years,
  active,
  onChange,
  label,
}: {
  years: TaxYear[];
  active: string;
  onChange: (year: string) => void;
  label: string;
}) {
  const { t } = useTranslation();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  // Roving-tabindex arrow navigation, the WAI-ARIA tabs pattern.
  const onKeyDown = (e: KeyboardEvent) => {
    const i = years.findIndex((y) => y.year === active);
    const next =
      e.key === "ArrowRight"
        ? (i + 1) % years.length
        : e.key === "ArrowLeft"
          ? (i - 1 + years.length) % years.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? years.length - 1
              : -1;
    if (next < 0) return;
    e.preventDefault();
    onChange(years[next].year);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      // overflow-y-hidden on purpose: overflow-x-auto alone computes the other
      // axis to auto, and the tab strip is barely taller than its content, so a
      // stray vertical scrollbar shows up pinned to the right of the tabs.
      className="mb-5 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-hairline"
    >
      {years.map((y, i) => (
        <button
          key={y.year}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          id={`taxes-tab-${y.year}`}
          aria-selected={y.year === active}
          aria-controls={`taxes-panel-${y.year}`}
          tabIndex={y.year === active ? 0 : -1}
          onClick={() => onChange(y.year)}
          className={tabClass(y.year === active)}
        >
          <span className="whitespace-nowrap">{t.taxes.yearTab(y.year, y.filedIn)}</span>
        </button>
      ))}
    </div>
  );
}

function YearPanel({ year }: { year: TaxYear }) {
  const { t } = useTranslation();
  const gains = year.capitalGains;
  const { dividends, staking } = year.investmentIncome;
  const hasIncome = dividends.payments.length > 0 || staking.rewards.length > 0;

  return (
    <>
      {year.open && (
        <p className="mb-5 rounded-xl border border-hairline bg-elevated px-4 py-3 text-[13.5px] text-secondary">
          {t.taxes.openYear(year.year, year.filedIn)}
        </p>
      )}

      <section
        className="panel-grid mb-6 grid [grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]"
        aria-label={t.taxes.totalsAria}
      >
        <div className={CARD}>
          <span className={LABEL}>{t.taxes.netResult}</span>
          <span className={`${VALUE} ${resultClass(gains.netEur)}`}>
            {eur.format(gains.netEur)}
          </span>
          <span className={SUB}>{t.taxes.assetsSold(gains.byAsset.length)}</span>
        </div>
        <div className={CARD}>
          <span className={LABEL}>{t.taxes.gains}</span>
          <span className={`${VALUE} ${resultClass(gains.totalGainEur)}`}>
            {eur.format(gains.totalGainEur)}
          </span>
          <span className={SUB}>{t.taxes.losses}: {eur.format(gains.totalLossEur)}</span>
        </div>
        <div className={CARD}>
          <span className={LABEL}>{t.taxes.estimatedTax}</span>
          <span className={VALUE}>{eur.format(gains.estimatedTaxEur)}</span>
          <span className={SUB}>{t.taxes.estimatedTaxNote}</span>
        </div>
        {gains.deferredLossEur !== 0 && (
          <div className={CARD}>
            <span className={LABEL}>{t.taxes.deferred}</span>
            <span className={`${VALUE} text-muted`}>{eur.format(gains.deferredLossEur)}</span>
            <span className={SUB}>{t.taxes.deferredNote}</span>
          </div>
        )}
        {gains.reintegratedLossEur !== 0 && (
          <div className={CARD}>
            <span className={LABEL}>{t.taxes.reintegrated}</span>
            <span className={`${VALUE} ${resultClass(gains.reintegratedLossEur)}`}>
              {eur.format(gains.reintegratedLossEur)}
            </span>
            <span className={SUB}>{t.taxes.reintegratedNote}</span>
          </div>
        )}
      </section>

      {(year.warnings.length > 0 || year.checks.length > 0) && (
        <section className="mb-7" aria-label={t.taxes.noticesAria}>
          <ul className="flex flex-col gap-2">
            {[...year.warnings, ...year.checks].map((n, i) => (
              <Notice key={`${n.id}-${i}`} notice={n} />
            ))}
          </ul>
        </section>
      )}

      <h2 className="mb-3 text-base font-semibold">{t.taxes.disposalsTitle}</h2>
      {gains.byAsset.length === 0 ? (
        <p className="mb-7 text-secondary">{t.taxes.noDisposals(year.year)}</p>
      ) : (
        <AssetTable assets={gains.byAsset} />
      )}

      {hasIncome && (
        <>
          <h2 className="mt-7 mb-1 text-base font-semibold">{t.taxes.incomeTitle}</h2>
          <p className="mb-3 max-w-prose text-sm text-secondary">{t.taxes.incomeDescription}</p>

          {dividends.payments.length > 0 && (
            <>
              <h3 className="mb-2 text-[13.5px] font-semibold text-secondary">
                {t.taxes.dividendsTitle}
              </h3>
              <div className={`mb-5 ${TABLE_WRAP}`}>
                <table className={TABLE}>
                  <thead>
                    <tr>
                      <th className={TH}>{t.taxes.date}</th>
                      <th className={TH}>{t.taxes.payer}</th>
                      <th className={`${TH} ${NUM}`}>{t.taxes.gross}</th>
                      <th className={`${TH} ${NUM}`}>{t.taxes.withholding}</th>
                      <th className={`${TH} ${NUM}`}>{t.taxes.net}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dividends.payments.map((p, i) => (
                      <tr key={`${p.date}-${p.symbol}-${i}`} className="hover:bg-hover">
                        <td className={`${TD} tabular-nums text-secondary`}>{formatDate(p.date)}</td>
                        <td className={TD}>
                          <span className="text-primary">{p.name}</span>
                          {p.symbol && <span className="ml-1.5 text-xs text-muted">({p.symbol})</span>}
                        </td>
                        <td className={`${TD} ${NUM}`}>{eur.format(p.grossEur)}</td>
                        <td className={`${TD} ${NUM} text-loss`}>{eur.format(p.withholdingEur)}</td>
                        <td className={`${TD} ${NUM}`}>{eur.format(p.netEur)}</td>
                      </tr>
                    ))}
                    <tr className="bg-elevated">
                      <td className={`${TD} border-b-0 font-semibold text-primary`} colSpan={2}>
                        {t.taxes.total}
                      </td>
                      <td className={`${TD} ${NUM} border-b-0 font-semibold`}>
                        {eur.format(dividends.grossEur)}
                      </td>
                      <td className={`${TD} ${NUM} border-b-0 font-semibold text-loss`}>
                        {eur.format(dividends.withholdingEur)}
                      </td>
                      <td className={`${TD} ${NUM} border-b-0 font-semibold`}>
                        {eur.format(dividends.netEur)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="mb-6 max-w-prose text-[13px] text-muted">{t.taxes.dividendsNote}</p>
            </>
          )}

          {staking.rewards.length > 0 && (
            <StakingRewards key={year.year} rewards={staking.rewards} />
          )}
        </>
      )}

      <h2 className="mt-7 mb-1 text-base font-semibold">{t.taxes.packTitle}</h2>
      <p className="mb-3 max-w-prose text-sm text-secondary">{t.taxes.packDescription}</p>
      <div className="flex flex-wrap gap-2">
        <PackLink year={year.year} extension="md" />
        <PackLink year={year.year} extension="json" />
      </div>

      <p className="mt-7 max-w-prose text-[13px] text-muted">{t.taxes.feesNote}</p>
      <p className="mt-2 max-w-prose text-[13px] text-muted">{t.taxes.disclaimer}</p>
    </>
  );
}

/** The pack is written next to the JSON the page already reads, so Vite serves
 * it from the same folder — no copy, no build step. */
function PackLink({ year, extension }: { year: string; extension: "md" | "json" }) {
  return (
    <a
      href={`/renta/renta-${year}.${extension}`}
      target="_blank"
      rel="noreferrer"
      className="rounded-lg border border-hairline bg-surface px-3 py-1.5 text-[13px] font-medium text-secondary transition-colors hover:border-hairline-strong hover:text-primary focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus"
    >
      renta-{year}.{extension}
    </a>
  );
}

/** The exporter ships each notice as an id plus its numbers, so the app can say
 * it in the reader's language. `message` is its own Spanish rendering, kept as
 * the fallback for an id this build doesn't know about yet. */
function Notice({ notice }: { notice: TaxNotice }) {
  const { t } = useTranslation();
  const render = t.taxes.notice[notice.id];
  const text = render ? render(notice.params) : notice.message;
  return (
    <li
      className={`rounded-xl border px-4 py-2.5 text-[13.5px] ${
        notice.level === "warn"
          ? "border-loss/40 bg-loss/5 text-primary"
          : "border-hairline bg-surface text-secondary"
      }`}
    >
      <span aria-hidden="true" className="mr-2">
        {notice.level === "warn" ? "⚠" : "ℹ"}
      </span>
      {text}
    </li>
  );
}

function AssetTable({ assets }: { assets: TaxAsset[] }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<string | null>(null);

  // What the year actually nets out to, which is the point of the table: the
  // computable half of each sale, plus any loss an earlier year deferred and
  // this one gets back. The released losses belong to no row, so they get a
  // line of their own rather than being smuggled into one.
  const computable = assets.reduce((sum, a) => sum + a.computableResultEur, 0);
  const released = assets.reduce((sum, a) => sum + a.reintegratedLossEur, 0);
  const total = computable + released;

  return (
    <div className={TABLE_WRAP}>
      <table className={TABLE}>
        <thead>
          <tr>
            <th className={TH}>{t.taxes.asset}</th>
            <th className={`${TH} ${NUM}`}>{t.taxes.quantity}</th>
            <th className={TH}>{t.taxes.acquisitionDate}</th>
            <th className={`${TH} ${NUM}`}>{t.taxes.acquisitionValue}</th>
            <th className={TH}>{t.taxes.disposalDate}</th>
            <th className={`${TH} ${NUM}`}>{t.taxes.disposalValue}</th>
            <th className={`${TH} ${NUM}`}>{t.taxes.fees}</th>
            <th className={`${TH} ${NUM}`}>{t.taxes.computableResult}</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => {
            const open = expanded === asset.key;
            return [
              <tr
                key={asset.key}
                onClick={() => setExpanded(open ? null : asset.key)}
                aria-expanded={open}
                className="cursor-pointer hover:bg-hover"
              >
                {/* The one column allowed to wrap. Every other cell is nowrap,
                    so without this a single long fund name sets the table's
                    width and pushes the computable figure — the column the
                    whole page exists for — off the right edge. */}
                <td className={`${TD_BASE} max-w-[280px]`}>
                  <span className="text-primary">{asset.name}</span>
                  <span className="ml-1.5 text-xs text-muted">({asset.symbol})</span>
                  {asset.deferred && (
                    <span className="ml-1.5 text-xs text-muted">
                      {/* A repurchase covering only part of the sale leaves the
                          rest computable — saying just "deferred" would read as
                          "none of this counts this year", which is wrong. */}
                      {asset.deferredLossEur === asset.resultEur
                        ? t.taxes.deferredBadge
                        : t.taxes.partlyDeferredBadge}
                    </span>
                  )}
                  {asset.review && (
                    <span className="ml-1.5 text-xs text-muted">{t.taxes.reviewBadge}</span>
                  )}
                </td>
                <td className={`${TD} ${NUM} text-secondary`}>
                  {quantity(asset.quantity)}
                  {!asset.fullExit && (
                    <span className="ml-1.5 text-xs text-muted">{t.taxes.partial}</span>
                  )}
                </td>
                <td className={`${TD} text-secondary`}>
                  {asset.acquisitionDate === "varias"
                    ? t.taxes.several
                    : formatDate(asset.acquisitionDate)}
                </td>
                <td className={`${TD} ${NUM}`}>{eur.format(asset.acquisitionValueEur)}</td>
                <td className={`${TD} text-secondary`}>
                  {asset.disposalDate === "varias"
                    ? t.taxes.several
                    : formatDate(asset.disposalDate)}
                </td>
                <td className={`${TD} ${NUM}`}>{eur.format(asset.disposalValueEur)}</td>
                <td className={`${TD} ${NUM} text-secondary`}>
                  {eur.format(asset.buyFeesEur + asset.sellFeesEur)}
                </td>
                {/* The computable figure, not the raw result: this column has
                    to add up to the total underneath it, and the total is what
                    goes in the return. The whole loss is still one glance away
                    so the deferral stays auditable. */}
                <td className={`${TD} ${NUM} font-medium ${resultClass(asset.computableResultEur)}`}>
                  {eur.format(asset.computableResultEur)}
                  {asset.deferredLossEur !== 0 && (
                    <>
                      {/* Two short lines rather than one long one: every cell in
                          this table is nowrap, so a single wide note would drag
                          the whole column off the right edge. */}
                      <span className="block text-xs font-normal text-muted">
                        {t.taxes.resultTotalNote(eur.format(asset.resultEur))}
                      </span>
                      <span className="block text-xs font-normal text-muted">
                        {t.taxes.resultDeferredNote(eur.format(asset.deferredLossEur))}
                      </span>
                    </>
                  )}
                </td>
              </tr>,
              open ? (
                <tr key={`${asset.key}-detail`} className="bg-elevated">
                  <td className={TD_BASE} colSpan={8}>
                    <LotDetail asset={asset} />
                  </td>
                </tr>
              ) : null,
            ];
          })}
          {released !== 0 && (
            <tr>
              <td className={`${TD} text-secondary`} colSpan={7}>
                {t.taxes.reintegratedRow}
              </td>
              <td className={`${TD} ${NUM} font-medium ${resultClass(released)}`}>
                {eur.format(released)}
              </td>
            </tr>
          )}
          <tr className="bg-elevated">
            <td className={`${TD} border-b-0 font-semibold text-primary`} colSpan={7}>
              {t.taxes.computableTotal}
            </td>
            <td className={`${TD} ${NUM} border-b-0 font-semibold ${resultClass(total)}`}>
              {eur.format(total)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/** The FIFO working: which purchase funded which slice of which sale. This is
 * the granularity the return itself asks for, so it doubles as the checking
 * view for the filing pack. */
function LotDetail({ asset }: { asset: TaxAsset }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4 py-1">
      {asset.disposals.map((disposal) => (
        <div key={`${disposal.date}-${disposal.ref ?? ""}`}>
          <p className="mb-1.5 text-[13px] text-secondary">
            {t.taxes.saleOn(formatDate(disposal.date), quantity(disposal.quantity), disposal.platform)}
            {disposal.deferred && (
              <span className="ml-2 text-muted">
                {t.taxes.deferredBecause(
                  (disposal.deferredBecause ?? []).map(formatDate).join(", "),
                  eur.format(disposal.deferredLossEur),
                )}
              </span>
            )}
          </p>
          {disposal.reintegratedFrom.map((origin) => (
            <p
              key={`${origin.date}-${origin.amountEur}`}
              className="mb-1.5 text-[13px] text-secondary"
            >
              {eur.format(origin.amountEur)}{" "}
              <span className="text-muted">
                {t.taxes.reintegratedFrom(formatDate(origin.date), quantity(origin.quantity))}
              </span>
            </p>
          ))}
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr>
                <th className={`${TH} !py-1.5`}>{t.taxes.acquisitionDate}</th>
                <th className={`${TH} ${NUM} !py-1.5`}>{t.taxes.quantity}</th>
                <th className={`${TH} ${NUM} !py-1.5`}>{t.taxes.acquisitionValue}</th>
                <th className={`${TH} ${NUM} !py-1.5`}>{t.taxes.disposalValue}</th>
                <th className={`${TH} ${NUM} !py-1.5`}>{t.taxes.result}</th>
                <th className={`${TH} !py-1.5`}>{t.taxes.source}</th>
              </tr>
            </thead>
            <tbody>
              {disposal.parcels.map((parcel, i) => (
                <tr key={`${parcel.acquisitionDate ?? "none"}-${i}`}>
                  <td className={`${TD} !py-1.5 text-secondary`}>
                    {parcel.acquisitionDate ? formatDate(parcel.acquisitionDate) : t.taxes.unknown}
                  </td>
                  <td className={`${TD} ${NUM} !py-1.5 text-secondary`}>
                    {quantity(parcel.quantity)}
                  </td>
                  <td className={`${TD} ${NUM} !py-1.5`}>
                    {eur.format(parcel.acquisitionValueEur)}
                  </td>
                  <td className={`${TD} ${NUM} !py-1.5`}>{eur.format(parcel.disposalValueEur)}</td>
                  <td className={`${TD} ${NUM} !py-1.5 ${resultClass(parcel.resultEur)}`}>
                    {eur.format(parcel.resultEur)}
                  </td>
                  <td className={`${TD} !py-1.5 text-muted`}>
                    {t.taxes.sourceLabel[parcel.source] ?? parcel.source}
                    {parcel.fxRate !== null && (
                      <span className="ml-1.5">
                        {t.taxes.fxAt(parcel.fxRate, parcel.fxSource ?? "")}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function UpdatedNote({ updatedAt }: { updatedAt: string }) {
  const { t } = useTranslation();
  const updated = new Date(updatedAt);
  return (
    <p className="mt-6 text-xs tabular-nums text-muted">
      {t.common.updated(
        updated.toLocaleDateString("en-GB", { day: "numeric", month: "short" }),
        updated.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }),
      )}
    </p>
  );
}
