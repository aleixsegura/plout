// Number formatting shared by every page. Money stays es-ES whatever the
// interface language (same rule as the i18n helpers).

export const eur = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
  useGrouping: true,
});

/** Whole euros, for chart labels and compact tiles. */
export const eur0 = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
  useGrouping: true,
});

export const eurSigned = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
  useGrouping: true,
  signDisplay: "exceptZero",
});

export const pctSigned = new Intl.NumberFormat("es-ES", {
  maximumFractionDigits: 2,
  useGrouping: true,
  signDisplay: "exceptZero",
});

export const pct1 = new Intl.NumberFormat("es-ES", {
  maximumFractionDigits: 1,
  useGrouping: true,
});

/** A correlation coefficient: two decimals and a true minus sign ("−0,04"). */
export function rho(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return value
    .toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    .replace("-", "−");
}

/** Money in an arbitrary currency with a narrow symbol ("US$", "£"). */
export function money(value: number, currency: string, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
    maximumFractionDigits,
    useGrouping: true,
  }).format(value);
}

export function gainLoss(value: number): string {
  return value >= 0 ? "text-gain" : "text-loss";
}

/** "13 Sep" — day and short month, the app-wide short date. */
export function shortDate(iso: string, lang = "en-GB"): string {
  return new Date(iso).toLocaleDateString(lang, { day: "numeric", month: "short" });
}

/** "September 2026" from a "YYYY-MM" key, first letter capitalised (Catalan
 * and Spanish month names are lower-case). */
export function monthLabel(month: string, lang = "en-GB"): string {
  const [y, m] = month.split("-").map(Number);
  return capFirst(new Date(y, m - 1, 1).toLocaleDateString(lang, { month: "long", year: "numeric" }));
}

/** Axis label for a month: "Sep", with the year added in January and on the
 * first bar ("Sep '25") so a 12-month axis stays short but dated. */
export function monthTick(month: string, lang = "en-GB", withYear = false): string {
  const [y, m] = month.split("-").map(Number);
  const name = capFirst(new Date(y, m - 1, 1).toLocaleDateString(lang, { month: "short" }).replace(/\.$/, ""));
  return withYear || m === 1 ? `${name} '${String(y).slice(2)}` : name;
}

export function capFirst(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
