/** One Holdings row, built from a DEGIRO or Revolut position (useHoldings). */
export interface Stock {
  symbol: string;
  name: string;
  compactName: string;
  exchange: string;
  symbolType: string;
  currency: string;
  lastPrice: number | null;
  marketCap: number | null;
}

// Mirror of data/marketcaps.json written by export_marketcaps.py. Keyed
// by every symbol spelling of a company ("VWCE.DE" on Yahoo, "VWCE" at
// DEGIRO), so a row looks its market cap up by whatever symbol it carries. `at` is per entry: a symbol no source answered for on the
// last run keeps the value (and timestamp) of the run that did resolve it.
export interface MarketCapEntry {
  marketCap: number;
  /** Currency the figure is expressed in; null when the source didn't say. */
  currency: string | null;
  name?: string | null;
  /** "yahoo", "nasdaq" or "coingecko". */
  source: string;
  at: string;
}

export interface MarketCapsFile {
  updatedAt: string;
  source: string;
  /** Symbols no source answered for, kept so a `--max-age-hours` run can tell
   * them apart from symbols it has never seen. Absent in older exports. */
  unresolved?: string[];
  entries: Record<string, MarketCapEntry>;
}

// Mirror of data/prices.json written by export_prices.py: ~3 years of daily
// closes per holding, converted to EUR, keyed by the symbol the app uses.
// Dates are ascending and unique; weekends only appear for crypto.
export interface PriceSeries {
  /** "onvista" or "coinbase". */
  source: string;
  isin?: string;
  /** Venue the closes come from ("Xetra", "NYSE", "Coinbase", …). */
  market?: string;
  /** Quote currency before the EUR conversion. */
  currency: string;
  at: string;
  dates: string[];
  closesEur: number[];
}

export interface PricesFile {
  updatedAt: string;
  source: string;
  baseCurrency: string;
  /** Holdings no source had prices for. */
  unresolved: string[];
  series: Record<string, PriceSeries>;
  /** The Holdings return chart's yardstick: an accumulating world ETF's EUR
   * closes, a proxy's chain-linked before `splicedAt`. Absent in older
   * exports, null when it couldn't be fetched. */
  benchmark?: BenchmarkSeries | null;
}

export interface BenchmarkSeries {
  symbol: string;
  name: string;
  isin: string;
  proxy: { symbol: string; name: string; isin: string };
  /** First date priced off the benchmark itself rather than the proxy. */
  splicedAt: string;
  dates: string[];
  closesEur: number[];
}

// Mirror of data/deposits.json written by export_deposits.py: every transfer
// into (positive) or out of (negative) the DEGIRO account, oldest first.
export interface DepositMovement {
  /** ISO timestamp of the booking. */
  date: string;
  kind: "deposit" | "withdrawal";
  description: string;
  currency: string;
  amount: number;
  amountEur: number;
}

export interface DepositsFile {
  updatedAt: string;
  totalEur: number;
  movements: DepositMovement[];
}

// Mirror of data/positions.json written by export_degiro.py. Monetary
// value/cost/P&L fields are in the DEGIRO account base currency (EUR);
// avgPrice/lastPrice are in the product's own currency (`currency`).
export interface Position {
  id: string;
  symbol: string;
  name: string;
  /** Absent in files from exports before the Correlation page. */
  isin?: string | null;
  /** DEGIRO's product classification ("STOCK", "ETF", "FUND", …), mapped to a
   * `symbolType` for the row's type icon. Absent in older exports. */
  productType?: string | null;
  quantity: number;
  avgPrice: number;
  currency: string;
  lastPrice: number | null;
  valueEur: number;
  /** Cost basis of the shares still held (quantity × avgPrice, in EUR). */
  costEur: number;
  /** Unrealized P/L (valueEur − costEur) — DEGIRO's "potential G/L". */
  plEur: number;
  todayPlEur: number;
  /** Result already banked by earlier sells of this product, plus buy fees
   * DEGIRO excludes from avgPrice; absent in files from older exports. */
  realizedPlEur?: number;
  averageFxRate: number | null;
  exchange: string | null;
}

/** One currency's cash at DEGIRO (DEGIRO + flatex accounts merged): `amount`
 * is in `currency`, `amountEur` is DEGIRO's EUR countervalue. */
export interface DegiroCashBalance {
  currency: string;
  amount: number;
  amountEur: number;
}

export interface PositionsSummary {
  totalValueEur: number;
  totalPlEur: number;
  totalTodayPlEur: number;
  /** All DEGIRO cash in EUR, foreign-currency accounts included. */
  cashEur: number | null;
  /** Per-currency split of cashEur; absent in files from older exports. */
  cashBreakdown?: DegiroCashBalance[];
  netLiquidationEur: number | null;
  /** Net deposits since account opening; absent in files from older exports. */
  netDepositsEur?: number | null;
  /** DEGIRO account-level Total P/L (realized + unrealized + dividends + fees). */
  accountPlEur?: number | null;
}

export interface PositionsFile {
  updatedAt: string;
  source: string;
  baseCurrency: string;
  summary: PositionsSummary;
  positions: Position[];
}

// Mirror of data/revolut.json written by export_revolut.py. All EUR
// fields are converted by the exporter; `amount` is in the pocket's own
// currency.
export interface RevolutCash {
  id: string;
  name: string;
  kind: "current" | "savings";
  currency: string;
  amount: number;
  amountEur: number;
  /** Savings only — gross nominal rate (%), from the money-box deposit block.
   * Absent in files from exports before the transactions feature. */
  interestRate?: number;
  /** Savings only — annual effective rate (%). */
  aer?: number;
  /** Savings only — interest paid since the account was opened, in EUR. */
  earnedInTotalEur?: number;
}

export interface RevolutPosition {
  /** Revolut-native symbol ("BTC"). */
  symbol: string;
  name: string;
  quantity: number;
  currency: string;
  /** Live spot price in `priceCurrency` (USD) — crypto's conventional quote
   * currency, as price sites show it. Absent in files from
   * older exports, which only carried the EUR price. */
  lastPrice?: number | null;
  /** Currency of `lastPrice`; absent in files from older exports. */
  priceCurrency?: string;
  lastPriceEur: number | null;
  /** null when no EUR price was available at export time. */
  valueEur: number | null;
  // Cost basis — present only when a manual average buy price is configured in
  // crypto_cost_basis.json (Revolut exposes none). See export_revolut.py.
  /** Average buy price per unit, in `avgPriceCurrency` (e.g. USD). */
  avgPrice?: number;
  /** Currency of `avgPrice` (e.g. "USD"); may differ from the EUR value base. */
  avgPriceCurrency?: string;
  /** quantity × avgPrice converted to EUR (avg-cost basis of the holding). */
  costEur?: number;
  /** valueEur − costEur; null when no EUR value was available. */
  plEur?: number | null;
  /** Change since the previous UTC day's Coinbase price; null when that price
   * was unavailable, absent in files from older exports. */
  todayPlEur?: number | null;
}

export interface RevolutFile {
  updatedAt: string;
  source: string;
  baseCurrency: string;
  totalCashEur: number;
  /** Sum of positions' todayPlEur; absent in files from older exports. */
  totalTodayPlEur?: number;
  cash: RevolutCash[];
  positions: RevolutPosition[];
}

// Mirror of data/history.json written by history.py: one snapshot per
// local date, appended each time an exporter runs. Dates are ascending and
// unique (lightweight-charts requires both).
export interface HistoryDay {
  /** "YYYY-MM-DD" local date of the snapshot. */
  date: string;
  /** null when positions.json didn't exist at snapshot time. */
  degiro: {
    updatedAt: string;
    valueEur: number;
    cashEur: number | null;
    /** Net EUR deposited into DEGIRO so far. Absent or null on days recorded
     * before it existed and not yet backfilled by export_deposits.py. */
    netDepositsEur?: number | null;
  } | null;
  /** null when revolut.json didn't exist at snapshot time. */
  revolut: {
    updatedAt: string;
    cryptoValueEur: number;
    cashCurrentEur: number;
    cashSavingsEur: number;
    /** Running total of EUR put into Revolut positions (buys − sells + coins
     * sent in). Only differences between days mean anything. */
    netInvestedEur?: number | null;
    /** Boosted interest credited so far (running total). */
    interestEur?: number | null;
  } | null;
  portfolioValueEur: number;
  cashEur: number;
  /** portfolioValueEur + cashEur — matches the "Total value" KPI. */
  totalEur: number;
}

export interface HistoryFile {
  baseCurrency: string;
  days: HistoryDay[];
}

// Mirror of data/dividends.json written by export_dividends.py. One entry
// per (product, settlement day); gross/tax/net are in the payment's own
// `currency` (USD for US stocks, EUR for Xetra ETFs) — no FX conversion, so the
// page totals per currency. `tax` is the withholding, booked negative.
export interface DividendPayment {
  /** "YYYY-MM-DD" settlement date. */
  date: string;
  productId: number | string;
  symbol: string;
  name: string;
  /** Native payment currency (USD for US stocks, EUR for Xetra ETFs). */
  currency: string;
  gross: number;
  tax: number;
  net: number;
  /** Native amounts converted to EUR at the payment date (Coinbase USD-EUR
   * spot). Absent in files from exports before the EUR-conversion feature. */
  grossEur?: number;
  taxEur?: number;
  netEur?: number;
}

// Announced-but-unpaid dividend/coupon from DEGIRO's get_upcoming_payments.
// `amount` is in `currency`; `amountEur` is DEGIRO's base-currency estimate.
export interface UpcomingDividend {
  payDate: string | null;
  name: string;
  description: string;
  currency: string | null;
  amount: number | null;
  amountEur: number | null;
}

export interface DividendsFile {
  updatedAt: string;
  source: string;
  fromDate: string;
  toDate: string;
  payments: DividendPayment[];
  /** Absent in files from exports before the upcoming-payments feature. */
  upcoming?: UpcomingDividend[];
}

// Mirror of data/taxes.json written by export_taxes.py. Everything is in
// EUR: DEGIRO already books trades in the account's base currency, and crypto
// lots are converted at the ECB reference rate of their trade date.
//
// One `TaxParcel` is one slice of a sale matched against one purchase by FIFO,
// which is the granularity the IRPF asks for: a single acquisition date and a
// single disposal date per declared line.
export interface TaxParcel {
  quantity: number;
  /** null when no lot covered this slice — declared at cost 0, see `warnings`. */
  acquisitionDate: string | null;
  acquisitionValueEur: number;
  /** Purchase commissions, already inside `acquisitionValueEur`. */
  buyFeesEur: number;
  disposalValueEur: number;
  /** This parcel's share of the sale's commission, pro rata by quantity and
   * already out of `disposalValueEur`. The parcels of one sale add back up to
   * its `sellFeesEur`. */
  sellFeesEur: number;
  resultEur: number;
  /** "degiro" | "bybit" | "staking" | "manual" | "uncovered". */
  source: string;
  note: string | null;
  fxRate: number | null;
  fxSource: string | null;
}

/** One earlier loss, or a slice of it, handed back by a later sale. */
export interface TaxReintegration {
  /** The date of the sale whose loss was blocked. */
  date: string;
  amountEur: number;
  /** How many of the blocking shares this sale transmitted. */
  quantity: number;
}

export interface TaxDisposal {
  date: string;
  quantity: number;
  platform: string;
  ref: string | null;
  note: string | null;
  acquisitionValueEur: number;
  buyFeesEur: number;
  disposalValueEur: number;
  sellFeesEur: number;
  resultEur: number;
  /** Loss blocked by the two-month repurchase rule (listed shares/ETFs).
   * A repurchase covering only part of what was sold blocks only that share
   * of the loss, so these are amounts, not a yes/no. */
  deferred: boolean;
  deferredBecause?: string[];
  /** Units of this sale whose loss the repurchase blocks. */
  deferredQuantity: number;
  /** The blocked slice of `resultEur`; 0 when nothing is deferred. */
  deferredLossEur: number;
  /** `resultEur` minus `deferredLossEur` — what this sale adds to the year. */
  computableResultEur: number;
  /** A loss an earlier year had to leave out, released because this sale is
   * the one closing the repurchase that blocked it. Deliberately *not* inside
   * `computableResultEur`: it is a separate pérdida patrimonial that happens
   * to be triggered here, and folding it in would corrupt the per-line figures
   * the return asks for. */
  reintegratedLossEur: number;
  reintegratedFrom: TaxReintegration[];
  /** Crypto loss with a nearby repurchase — flagged, not applied. */
  review?: string;
  parcels: TaxParcel[];
}

export interface TaxAsset {
  key: string;
  symbol: string;
  name: string;
  assetType: "stock" | "etf" | "crypto";
  platform: string;
  isin: string | null;
  currency: string;
  quantity: number;
  remainingQuantity: number;
  fullExit: boolean;
  /** A single ISO date, or "varias" when the FIFO matched several lots. */
  acquisitionDate: string;
  acquisitionDates: string[];
  acquisitionValueEur: number;
  disposalDate: string;
  disposalDates: string[];
  disposalValueEur: number;
  buyFeesEur: number;
  sellFeesEur: number;
  resultEur: number;
  /** `resultEur` minus anything deferred by the two-month rule. */
  computableResultEur: number;
  /** Earlier deferred losses this year's sales of the asset released. */
  reintegratedLossEur: number;
  /** How much of `resultEur` the two-month rule blocks this year. */
  deferredLossEur: number;
  deferred: boolean;
  review: boolean;
  disposals: TaxDisposal[];
}

export interface TaxDividendPayment {
  date: string;
  symbol: string | null;
  name: string | null;
  currency: string;
  grossEur: number;
  /** Withholding suffered abroad — the base of the double-taxation deduction. */
  withholdingEur: number;
  netEur: number;
}

export interface TaxStakingReward {
  date: string;
  platform: string;
  symbol: string;
  quantity: number;
  /** Market value in EUR the day it was credited: taxed as investment income
   * now, and the acquisition value of those coins when they're sold. */
  valueEur: number;
  approximate: boolean;
  priceSource: string;
}

// Warnings and checks come structured so the page can render them in the
// reader's language; `message` is the exporter's Spanish rendering, used by the
// filing pack and kept here as the fallback for an id the app doesn't know.
export type TaxNoticeId =
  | "modelo-721"
  | "comisiones-no-deducibles"
  | "lote-descubierto"
  | "traspaso-degiro"
  | "staking-sin-precio"
  | "regla-dos-meses"
  | "regla-dos-meses-cripto";

export interface TaxNotice {
  id: TaxNoticeId | string;
  level: "info" | "warn";
  params: Record<string, string | number | boolean | string[]>;
  message: string;
}

export interface TaxYear {
  year: string;
  /** The year the return is filed in — 2026 is filed in 2027. */
  filedIn: string;
  /** True while the year is still running, so the figures can still change. */
  open: boolean;
  capitalGains: {
    totalGainEur: number;
    totalLossEur: number;
    /** Gains + computable losses + `reintegratedLossEur`. */
    netEur: number;
    deferredLossEur: number;
    /** Losses deferred in an earlier year that this one gets back, because the
     * repurchase blocking them was sold. Part of `netEur`, but of no single
     * asset row's `computableResultEur`. */
    reintegratedLossEur: number;
    /** Savings-base brackets applied in isolation — an estimate, not the bill. */
    estimatedTaxEur: number;
    byAsset: TaxAsset[];
  };
  investmentIncome: {
    dividends: {
      grossEur: number;
      withholdingEur: number;
      netEur: number;
      payments: TaxDividendPayment[];
    };
    staking: {
      grossEur: number;
      approximate: boolean;
      rewards: TaxStakingReward[];
    };
  };
  checks: TaxNotice[];
  warnings: TaxNotice[];
}

export interface TaxTrade {
  id: number | string | null;
  platform: string;
  date: string;
  side: "buy" | "sell";
  symbol: string;
  name: string;
  assetType: string;
  currency: string;
  quantity: number;
  price: number;
  totalEur: number;
  totalPlusFeesEur: number;
  feesEur: number;
}

export interface TaxesFile {
  updatedAt: string;
  source: string;
  baseCurrency: string;
  fromDate: string;
  toDate: string;
  years: TaxYear[];
  /** The full normalized ledger, kept so any figure can be audited. */
  trades: TaxTrade[];
}

// Mirror of data/revolut_transactions.json written by export_revolut.py
// (write_transactions): the EUR current account's statement plus the Boosted
// account's interest credits, one row per leg, newest first, with the monthly
// aggregates precomputed so the pages only render.
export type TransactionKind =
  | "expense"
  | "refund"
  | "income"
  | "transfer"
  | "interest"
  | "fee"
  | "other";

export interface RevolutTransaction {
  id: string;
  legId: string;
  /** Millisecond epoch of `date`; what the exporter merges on. */
  ts: number;
  /** Local-time ISO timestamp. */
  date: string;
  /** Revolut's own type ("CARD_PAYMENT", "TRANSFER", "TOPUP", …). */
  type: string | null;
  kind: TransactionKind;
  /** Revolut's category slug ("restaurants", "groceries", …); "interest" for
   * interest rows, "fees" for the synthetic fee rows. */
  category: string;
  merchant: string | null;
  description: string | null;
  /** Signed, in `currency`; negative is money out. */
  amount: number;
  amountEur: number;
  fee: number;
  currency: string;
  account: "current" | "savings";
  cardLastFour?: string;
  city?: string;
  country?: string;
  counterpart?: { amount: number; currency: string };
  /** Pocket balance after the row, when Revolut reported one. */
  balance?: number;
}

export interface TransactionMonth {
  /** "YYYY-MM" */
  month: string;
  /** Positive: card payments and outgoing transfers, net of refunds. */
  expenses: number;
  income: number;
  interest: number;
  fees: number;
  /** income + interest − expenses − fees */
  net: number;
  /** Expense, refund and income rows in the month. */
  count: number;
  /** Expenses per category slug, descending. */
  byCategory: Record<string, number>;
}

export interface InterestDay {
  /** "YYYY-MM-DD" */
  date: string;
  amount: number;
  /** Savings balance after the day's credit, when reported. */
  balanceEur?: number;
}

export interface InterestSummary {
  daily: InterestDay[];
  byMonth: { month: string; amount: number }[];
  total: number;
}

export interface RevolutTransactionsFile {
  updatedAt: string;
  source: string;
  currency: string;
  fromDate: string | null;
  toDate: string | null;
  /** Query parameter that paged the history; null when only one page came back. */
  pageParam: string | null;
  accounts: {
    current: { id: string; currency: string };
    savings?: {
      id: string | null;
      name: string;
      since: string | null;
      balanceEur: number;
      interestRate?: number;
      aer?: number;
      earnedInTotalEur?: number;
    };
  };
  transactions: RevolutTransaction[];
  /** Newest month first. */
  months: TransactionMonth[];
  interest: InterestSummary;
  ignoredTypes: Record<string, number>;
}
