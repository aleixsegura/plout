import { useMemo } from "react";
import { useAppData } from "./data";
import { useTranslation } from "./i18n/LanguageContext";
import type { DividendPayment } from "./types";

const CARD = "flex flex-col gap-1 card px-4 py-3.5";
const LABEL = "text-xs font-semibold uppercase tracking-[0.03em] text-muted";
const VALUE = "text-[1.35rem] font-semibold leading-tight tabular-nums";
const SUB = "text-[0.85rem] tabular-nums text-secondary";

const TH =
  "border-b border-hairline-strong px-2.5 py-2.5 text-left text-[11.5px] font-semibold uppercase tracking-[0.04em] text-muted whitespace-nowrap";
const TD = "border-b border-hairline px-2.5 py-2 whitespace-nowrap";
const NUM = "text-right tabular-nums";

const eur = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
  useGrouping: true,
});

function money(value: number, currency: string, signed = false): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
    maximumFractionDigits: 2,
    useGrouping: true,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(value);
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

// EUR figures fall back to native amounts for files from exports that predate
// the EUR-conversion feature (those are only exact when the payment was in EUR).
const grossEur = (p: DividendPayment) => p.grossEur ?? p.gross;
const taxEur = (p: DividendPayment) => p.taxEur ?? p.tax;
const netEur = (p: DividendPayment) => p.netEur ?? p.net;

interface Totals {
  gross: number;
  tax: number;
  net: number;
  count: number;
}

function emptyTotals(): Totals {
  return { gross: 0, tax: 0, net: 0, count: 0 };
}

function addPayment(t: Totals, p: DividendPayment): void {
  t.gross += grossEur(p);
  t.tax += taxEur(p);
  t.net += netEur(p);
  t.count += 1;
}

export default function DividendsPage() {
  const { dividends } = useAppData();
  const { t } = useTranslation();
  const payments = dividends?.payments ?? [];
  const upcoming = (dividends?.upcoming ?? []).filter((u) => u.payDate);
  const nextPayment = upcoming[0];

  const { total, thisYearNet, byYear, payerCount } = useMemo(() => {
    const thisYear = String(new Date().getFullYear());
    const total = emptyTotals();
    const byYearMap = new Map<string, Totals>();
    const payers = new Set<string>();
    let thisYearNet = 0;

    for (const p of payments) {
      payers.add(p.symbol);
      addPayment(total, p);

      const year = p.date.slice(0, 4);
      const yt = byYearMap.get(year) ?? emptyTotals();
      addPayment(yt, p);
      byYearMap.set(year, yt);

      if (year === thisYear) thisYearNet += netEur(p);
    }

    return {
      total,
      thisYearNet,
      byYear: [...byYearMap.entries()].sort((a, b) => b[0].localeCompare(a[0])),
      payerCount: payers.size,
    };
  }, [payments]);

  // Not exported yet — mirror the silent-degrade guidance elsewhere.
  if (dividends === null) {
    return (
      <div>
        <h1 className="mb-2 text-xl font-semibold">{t.dividends.title}</h1>
        <p className="max-w-prose text-secondary">
          {t.dividends.noDataIntro}{" "}
          <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">
            .venv/bin/plout dividends
          </code>{" "}
          {t.dividends.noDataOutro}{" "}
          <code className="rounded bg-elevated px-1.5 py-0.5 text-[0.85em]">
            data/dividends.json
          </code>
          .
        </p>
      </div>
    );
  }

  if (payments.length === 0 && upcoming.length === 0) {
    return (
      <div>
        <h1 className="mb-2 text-xl font-semibold">{t.dividends.title}</h1>
        <p className="text-secondary">
          {t.dividends.noPaymentsFound(dividends.fromDate, dividends.toDate)}
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">{t.dividends.title}</h1>
      <p className="mb-5 text-sm text-secondary">
        {t.dividends.description}{" "}
        <span className="text-muted">{t.dividends.tax}</span> {t.dividends.taxIsWithholding}
      </p>

      <section
        className="panel-grid mb-6 grid [grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]"
        aria-label={t.dividends.totalsAria}
      >
        <div className={CARD}>
          <span className={LABEL}>{t.dividends.netReceived}</span>
          <span className={`${VALUE} text-gain`}>{eur.format(total.net)}</span>
          <span className={SUB}>{t.dividends.grossOf(eur.format(total.gross))}</span>
        </div>
        <div className={CARD}>
          <span className={LABEL}>{t.dividends.thisYear}</span>
          <span className={`${VALUE} text-gain`}>{eur.format(thisYearNet)}</span>
          <span className={SUB}>{t.dividends.netYear(new Date().getFullYear())}</span>
        </div>
        <div className={CARD}>
          <span className={LABEL}>{t.dividends.payments}</span>
          <span className={VALUE}>{payments.length}</span>
          <span className={SUB}>
            {payerCount} {payerCount === 1 ? t.dividends.payer : t.dividends.payers}
          </span>
        </div>
        {nextPayment && (
          <div className={CARD}>
            <span className={LABEL}>{t.dividends.nextPayment}</span>
            <span className={VALUE}>{formatDate(nextPayment.payDate!)}</span>
            <span className={`${SUB} truncate`} title={nextPayment.name}>
              {nextPayment.name}
            </span>
          </div>
        )}
      </section>

      <h2 className="mb-1 text-base font-semibold">{t.dividends.upcoming}</h2>
      {upcoming.length > 0 ? (
        <>
          <p className="mb-3 text-sm text-secondary">{t.dividends.upcomingDescription}</p>
          <div className="mb-7 overflow-x-auto card">
            <table className="w-full border-collapse text-[13.5px] [&_tbody_tr:last-child>td]:border-b-0 [&_tr>*:first-child]:pl-4 [&_tr>*:last-child]:pr-4">
              <thead>
                <tr>
                  <th className={TH}>{t.dividends.payDate}</th>
                  <th className={TH}>{t.dividends.stock}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.amount}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.eur}</th>
                </tr>
              </thead>
              <tbody>
                {upcoming.map((u, i) => (
                  <tr key={`${u.name}-${u.payDate}-${i}`} className="hover:bg-hover">
                    <td className={`${TD} tabular-nums text-secondary`}>
                      {u.payDate ? formatDate(u.payDate) : "—"}
                    </td>
                    <td className={`${TD} font-semibold text-primary`}>{u.name}</td>
                    <td className={`${TD} ${NUM} text-secondary`}>
                      {u.amount !== null && u.currency ? money(u.amount, u.currency) : "—"}
                    </td>
                    <td className={`${TD} ${NUM}`}>
                      {u.amountEur !== null ? eur.format(u.amountEur) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="mb-7 text-sm text-muted">{t.dividends.noUpcoming}</p>
      )}

      {payments.length > 0 && (
        <>
          <h2 className="mb-3 text-base font-semibold">{t.dividends.byYear}</h2>
          <div className="mb-7 overflow-x-auto card">
            <table className="w-full border-collapse text-[13.5px] [&_tbody_tr:last-child>td]:border-b-0 [&_tr>*:first-child]:pl-4 [&_tr>*:last-child]:pr-4">
              <thead>
                <tr>
                  <th className={TH}>{t.dividends.year}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.gross}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.tax}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.net}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.payments}</th>
                </tr>
              </thead>
              <tbody>
                {byYear.map(([year, totals]) => (
                  <tr key={year} className="hover:bg-hover">
                    <td className={`${TD} font-semibold text-primary`}>{year}</td>
                    <td className={`${TD} ${NUM}`}>{eur.format(totals.gross)}</td>
                    <td className={`${TD} ${NUM} ${totals.tax !== 0 ? "text-loss" : "text-muted"}`}>
                      {totals.tax !== 0 ? eur.format(totals.tax) : "—"}
                    </td>
                    <td className={`${TD} ${NUM} text-gain`}>{eur.format(totals.net)}</td>
                    <td className={`${TD} ${NUM} text-secondary`}>{totals.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2 className="mb-3 text-base font-semibold">{t.dividends.allPayments}</h2>
          <div className="overflow-x-auto card">
            <table className="w-full border-collapse text-[13.5px] [&_tbody_tr:last-child>td]:border-b-0 [&_tr>*:first-child]:pl-4 [&_tr>*:last-child]:pr-4">
              <thead>
                <tr>
                  <th className={TH}>{t.dividends.date}</th>
                  <th className={TH}>{t.dividends.stock}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.gross}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.tax}</th>
                  <th className={`${TH} ${NUM}`}>{t.dividends.net}</th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p, i) => (
                  <tr key={`${p.productId}-${p.date}-${i}`} className="hover:bg-hover">
                    <td className={`${TD} tabular-nums text-secondary`}>{formatDate(p.date)}</td>
                    <td className={TD}>
                      <span className="font-semibold text-primary">{p.name || p.symbol}</span>{" "}
                      <span className="text-xs text-muted">({p.symbol})</span>
                    </td>
                    <td className={`${TD} ${NUM}`}>{eur.format(grossEur(p))}</td>
                    <td className={`${TD} ${NUM} ${taxEur(p) !== 0 ? "text-loss" : "text-muted"}`}>
                      {taxEur(p) !== 0 ? eur.format(taxEur(p)) : "—"}
                    </td>
                    <td className={`${TD} ${NUM}`}>
                      <span className="font-medium text-gain">{eur.format(netEur(p))}</span>
                      {p.currency !== "EUR" && (
                        <span className="ml-1.5 text-xs text-muted">{money(p.net, p.currency)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}


      <UpdatedNote updatedAt={dividends.updatedAt} />
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
