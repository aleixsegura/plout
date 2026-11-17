"""
Export what has to be declared in the Spanish IRPF to data/taxes.json.

This is the only exporter that needs a *ledger* rather than a snapshot:
positions.json shows what you still hold, so a position sold in full vanishes
from it entirely, and DEGIRO's own `realizedPlEur` mixes realised gains with
buy fees. So this script rebuilds the history from trades and matches every
sale against its purchases with FIFO, which is what article 37.2 LIRPF
requires for "valores homogéneos".

Sources, in the order they are merged:

  DEGIRO        api.get_transactions_history — every buy/sell since the account
                opened, already denominated in EUR by DEGIRO itself
                (`totalInBaseCurrency`), which is what their statement shows.
  bybit.csv     the crypto bought on Bybit and later moved to Revolut. Moving
                coins between your own wallets is not a disposal, so these stay
                the valid acquisition lots. Quoted in USDC, converted to EUR at
                the ECB reference rate of each trade date.
  revolut_trades.json
                Revolut's own crypto ledger, captured by revolut.py from
                the per-pocket transaction history: Revolut X trades with their
                gross amount and fee, wallet deposits, and every staking reward
                with its real date. Authoritative for the period it covers.
  manual_trades.json
                what no API gives back: disposals and rewards from before the
                ledger's window or from other platforms, and any acquisition
                missing from the files above. Entries the ledger already covers
                are skipped rather than counted twice.

Everything is grouped by fiscal year — sales made in 2026 are declared in the
2026 return, filed in 2027 — and written to data/taxes.json for the
Renta page, plus a filing pack under data/renta/ meant to be read by an
agent driving Renta Web.

None of this is tax advice: every figure carries the inputs it came from so
you (or your advisor) can check it.

Usage:
  plout taxes [--from YYYY-MM-DD] [--year 2026]
              [--bybit bybit.csv] [--output-dir DIR] [--debug]
"""

import argparse
import calendar
import csv
import json
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from degiro_connector.trading.models.transaction import HistoryRequest

from plout.config import PROJECT_ROOT
from plout.exporters.degiro import connect, fetch_product_meta
from plout.exporters.ecb import EcbHistory
from plout.exporters.renta_casillas import annotate_pack, load_dictionary
from plout.exporters.revolut import crypto_price_eur, crypto_price_eur_at
from plout.exporters.tax_rules import (
    MODELO_720_THRESHOLDS,
    MODELO_721_THRESHOLDS,
    TREATY_RATES,
    TREATY_SOURCE,
    ZERO,
    Pending,
    average_rate_pct,
    cents,
    creditable_tax,
    dec,
    dividend_country,
    double_tax_deduction,
    integrate,
    modelo_threshold,
    money,
    savings_tax,
)

# Amounts and quantities are Decimal inside this module and float only in the
# JSON it writes: every float that comes in goes through `dec`, so there is no
# float dust to absorb and no epsilon to tune. Money is rounded to the cent
# once, on the figures that are declared — never lot by lot before adding up.

MANUAL_FILE = PROJECT_ROOT / "manual_trades.json"
BYBIT_FILE = PROJECT_ROOT / "bybit.csv"


def qty(value) -> float:
    """A quantity for JSON: exact, up to the 10 decimals any source carries."""
    return float(dec(value).quantize(Decimal("1e-10")))


def total(values) -> float:
    """Cent-rounded sum of already-declared figures."""
    return money(sum((dec(v) for v in values), ZERO))


# ── FX ─────────────────────────────────────────────────────────────────────────

_fx_cache: dict[tuple[str, str], tuple[float, str, str] | None] = {}

# The ECB reference rate is the one the AEAT's own guidance points at, and
# frankfurter republishes that series verbatim with no key. Weekends and
# holidays answer with the previous publication day, which is the correct
# behaviour: it's the rate that was actually published for that date.
FX_ENDPOINTS = (
    "https://api.frankfurter.dev/v1/{day}?base={base}&symbols=EUR",
    "https://api.frankfurter.app/{day}?base={base}&symbols=EUR",
)

# When frankfurter is down, the same series straight from the ECB, kept on
# disk. Never a market price: a conversion with no official rate is left
# incomplete instead — see `fx_to_eur`.
ECB_HISTORY = EcbHistory(PROJECT_ROOT / "data" / "ecb" / "eurofxref-hist.csv")


def _fx_base(currency: str) -> str:
    # USDC and USDT are dollar-pegged and have no ECB series of their own; the
    # Bybit statement quotes them one-for-one with the dollar.
    return "USD" if currency.upper() in ("USD", "USDC", "USDT", "BUSD") else currency.upper()


def fx_to_eur(currency: str, day: str, prefer_local: bool = False) -> tuple[float, str, str] | None:
    """ECB rate to convert `currency` into EUR on `day`, with its provenance.

    Returns (rate, source, rate_date), or None when no official rate exists
    for that currency and day. There is deliberately no market-price fallback:
    a tax figure converted at an unofficial rate looks exactly as definitive
    as a correct one, so the caller marks the operation incomplete instead.

    `prefer_local` reads the ECB file first — for bulk checks where a hundred
    HTTP calls would buy nothing the file doesn't already have.
    """
    base = _fx_base(currency)
    if base == "EUR":
        return 1.0, "identity", day

    key = (base, day)
    if key in _fx_cache:
        return _fx_cache[key]

    def local():
        found = ECB_HISTORY.rate(base, day)
        return (found[0], "ecb-file", found[1]) if found else None

    if prefer_local:
        result = local()
        if result:
            _fx_cache[key] = result
            return result

    for template in FX_ENDPOINTS:
        url = template.format(day=day, base=base)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "stocks-exporter"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            rate = float(payload["rates"]["EUR"])
            result = (rate, "ecb", str(payload.get("date", day)))
            _fx_cache[key] = result
            return result
        except Exception:
            continue

    result = local()
    if result is None:
        print(f"  warning: no ECB rate for {base} on {day}; the operation is left incomplete.",
              file=sys.stderr)
    _fx_cache[key] = result
    return result


# ── Notices ────────────────────────────────────────────────────────────────────

# Warnings and checks are emitted structured rather than as prose: the web app
# renders them in the reader's language, while `message` keeps a Spanish
# rendering for the CLI and the filing pack, which are Spanish either way.


def _es(value: float) -> str:
    """Spanish thousands/decimal separators, for text the page shows verbatim."""
    return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


def _es_qty(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".").replace(".", ",")


def _window(p: dict) -> str:
    return "regla de los dos meses" if p.get("months", 2) == 2 else "regla del año, valores no cotizados"


NOTICE_TEXT = {
    "modelo-721": lambda p: (
        f"Cripto en plataforma extranjera valorada en {_es(p['valueEur'])} € a {p['asOf']}"
        + (" (el ejercicio sigue abierto)" if p["open"] else "")
        + ". "
        + (
            f"Supera los {_es(p['threshold'])} € del Modelo 721: comprueba si te toca "
            f"presentarlo (declaración informativa, hasta el 31 de marzo)."
            if p["over"]
            else f"Por debajo de los {_es(p['threshold'])} € del Modelo 721; vuelve a mirarlo "
            f"con el saldo real a 31/12."
        )
    ),
    "comisiones-no-deducibles": lambda p: (
        f"{_es(p['amountEur'])} € de comisiones de conectividad y custodia en {p['year']}. No "
        f"son deducibles en el IRPF: solo entran las comisiones de compra y de venta, y esas ya "
        f"están dentro de los valores de adquisición y transmisión."
    ),
    "lote-descubierto": lambda p: (
        f"{p['symbol']}: {_es_qty(p['quantity'])} unidades vendidas el {p['date']} sin compra "
        f"conocida. La venta queda incompleta y su apartado bloqueado: no lo rellenes hasta "
        f"añadir la compra a manual_trades.json (con coste 0 pagarías de más)."
    ),
    "sin-tipo-oficial": lambda p: (
        f"{p['symbol']}: no hay tipo de cambio del BCE para {p['currency']} el {p['date']}. "
        f"La operación queda incompleta: no se convierte con un tipo no oficial."
    ),
    "tipo-cambio-desviado": lambda p: (
        f"{p['symbol']}: el cambio implícito de la operación del {p['date']} "
        f"({p['brokerRate']:.6f} EUR/{p['currency']}) se desvía un {_es(p['deviationPct'])} % "
        f"del BCE ({p['ecbRate']:.6f}). Probablemente el dato está mal leído: se usa el BCE."
    ),
    "dividendo-pais-desconocido": lambda p: (
        (
            f"{p['symbol']}: no se sabe de qué país es el dividendo del {p['date']}"
            if not p.get("country")
            else f"{p['symbol']}: no hay tipo de convenio registrado para {p['country']} "
            f"(dividendo del {p['date']})"
        )
        + ", así que no se puede aplicar el tope del convenio y se deduce todo lo retenido. "
        "Indica el país en `dividendCountries` de manual_trades.json o revisa el convenio."
    ),
    "dividendo-pais-dudoso": lambda p: (
        f"{p['symbol']}: parece un certificado de depósito (ADR/GDR). El país que cuenta es el "
        f"de la sociedad, no el del ISIN ({p['country'] or '—'}); confírmalo en "
        f"`dividendCountries` de manual_trades.json."
    ),
    "exceso-retencion": lambda p: (
        f"{p['country']}: se retuvieron {_es(p['amountEur'])} € por encima del "
        f"{_es(p['ratePct'])} % del convenio en {p['year']}. Eso no se deduce en España: "
        f"se reclama a la administración de {p['country']}."
    ),
    "sin-reglas": lambda p: (
        f"No hay reglas de {p['what']} para {p['year']}: no se estima esa cifra en vez de "
        f"dar una equivocada."
    ),
    "perdidas-caducadas": lambda p: (
        f"Caducan {_es(p['amountEur'])} € de saldo negativo de "
        f"{'ganancias y pérdidas' if p['block'] == 'gp' else 'rendimientos del capital mobiliario'}"
        f" de {p['origin']}: en {p['year']} se cumplen los 4 años para compensarlo."
    ),
    "traspaso-degiro": lambda p: (
        f"{p['symbol']}: la entrada del {p['date']} está marcada como traspaso en DEGIRO; "
        f"comprueba que el valor de adquisición es el original y no el del traspaso."
    ),
    "staking-sin-precio": lambda p: (
        f"{p['symbol']}: no se pudo valorar la recompensa de staking del {p['date']}; queda a "
        f"0 € hasta que haya precio."
    ),
    "regla-dos-meses": lambda p: (
        (
            f"{p['symbol']}: la pérdida de {_es(p['lossEur'])} € del {p['date']} no es computable "
            f"este ejercicio — recompra de valores homogéneos el {', '.join(p['repurchases'])} "
            f"({_window(p)}). Se integrará cuando vendas esos valores."
        )
        if p["deferredEur"] == p["lossEur"]
        else (
            f"{p['symbol']}: de la pérdida de {_es(p['lossEur'])} € del {p['date']}, "
            f"{_es(p['deferredEur'])} € no son computables este ejercicio — de los "
            f"{_es_qty(p['soldQuantity'])} títulos vendidos recompraste "
            f"{_es_qty(p['quantity'])} el {', '.join(p['repurchases'])} ({_window(p)}). "
            f"Los {_es(p['computableEur'])} € restantes sí se computan; la parte "
            f"diferida se integrará cuando vendas esos valores."
        )
    ),
    "modelo-720": lambda p: (
        f"Fuera de España a {p['asOf']}"
        + (" (el ejercicio sigue abierto)" if p["open"] else "")
        + f": {_es(p['accountsEur'])} € en cuentas y {_es(p['securitiesEur'])} € en valores. "
        + (
            f"{p['overLabel']} supera los {_es(p['threshold'])} € del Modelo 720: comprueba si "
            f"te toca presentarlo (declaración informativa, hasta el 31 de marzo). Si ya lo "
            f"presentaste otro año, solo hay que repetirlo si un bloque sube más de 20.000 € "
            f"sobre lo último declarado."
            if p["over"]
            else f"Cada bloque se mira por separado contra los {_es(p['threshold'])} € del "
            f"Modelo 720 y los dos están por debajo; vuelve a mirarlo con los saldos reales "
            f"a 31/12."
        )
        + " La cripto no va aquí, va en el Modelo 721. Y lo que cuenta es dónde está la "
        "entidad depositaria, no desde qué app la uses: confírmalo antes de dar por hecho "
        "que estos saldos son extranjeros."
    ),
    "regla-dos-meses-reintegro": lambda p: (
        f"{p['symbol']}: se integran {_es(p['amountEur'])} € de pérdida que quedaron "
        f"bloqueados en la venta del {p['lossDate']} — el {p['date']} vendiste "
        f"{_es_qty(p['quantity'])} de los títulos recomprados, así que esa parte ya es "
        f"computable. Va aparte del resultado de esta venta."
    ),
    "regla-dos-meses-cripto": lambda p: (
        f"{p['symbol']}: pérdida de {_es(p['lossEur'])} € el {p['date']} con compras el "
        f"{', '.join(p['repurchases'])}. Revisa si aplica la regla de recompra a monedas "
        f"virtuales antes de computarla."
    ),
}


def notice(notice_id: str, level: str, **params) -> dict:
    return {
        "id": notice_id,
        "level": level,
        "params": params,
        "message": NOTICE_TEXT[notice_id](params),
    }


# ── Lots and disposals ─────────────────────────────────────────────────────────


@dataclass
class Lot:
    """One acquisition. `cost_eur` is the full valor de adquisición of
    `quantity`: what was paid plus the fees inherent to the purchase."""

    day: str
    quantity: Decimal
    cost_eur: Decimal
    source: str
    # Part of cost_eur, kept apart only so the page can show what the purchase
    # commissions were; they are already inside the valor de adquisición.
    fee_eur: Decimal = ZERO
    note: str | None = None
    fx_rate: float | None = None
    fx_source: str | None = None
    platform: str | None = None
    # What the annex shows: price per unit in the trade's own currency.
    price: Decimal | None = None
    currency: str | None = None
    # True when the cost could not be established (no official FX rate): any
    # sale this lot funds is then incomplete too.
    incomplete: bool = False
    remaining: Decimal = field(init=False)
    # Quantity of this lot that funded each disposal, by its index in the
    # disposals list. The two-month rule needs it to tell a genuine repurchase
    # apart from the very shares being sold — see flag_two_month_rule.
    used: dict[int, Decimal] = field(init=False)

    def __post_init__(self) -> None:
        self.quantity = dec(self.quantity)
        self.cost_eur = dec(self.cost_eur)
        self.fee_eur = dec(self.fee_eur)
        if self.price is not None:
            self.price = dec(self.price)
        self.remaining = self.quantity
        self.used = {}

    @property
    def unit_cost(self) -> Decimal:
        return self.cost_eur / self.quantity if self.quantity else ZERO


@dataclass
class Sale:
    """One disposal. `proceeds_eur` is the valor de transmisión: what came in
    minus the fees inherent to the sale."""

    day: str
    quantity: Decimal
    proceeds_eur: Decimal
    fee_eur: Decimal
    platform: str
    ref: str | None = None
    note: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    fx_rate: float | None = None
    fx_source: str | None = None
    fx_date: str | None = None
    incomplete: bool = False

    def __post_init__(self) -> None:
        self.quantity = dec(self.quantity)
        self.proceeds_eur = dec(self.proceeds_eur)
        self.fee_eur = dec(self.fee_eur)
        if self.price is not None:
            self.price = dec(self.price)


@dataclass
class Asset:
    """A line of homogeneous securities: one FIFO queue of its own."""

    key: str
    symbol: str
    name: str
    asset_type: str          # "stock" | "etf" | "crypto"
    platform: str
    currency: str
    isin: str | None = None
    lots: list[Lot] = field(default_factory=list)
    sales: list[Sale] = field(default_factory=list)
    # Admitted to trading on a regulated market. Decides the repurchase window
    # of art. 33.5: two months if listed, one year if not.
    listed: bool = True


def match_fifo(asset: Asset) -> tuple[list[dict], list[tuple[str, dict]]]:
    """Match every sale against the oldest available lots.

    Returns (disposals, warnings). A sale that no lot covers is neither
    dropped nor given an invented cost: the uncovered slice gets a zero
    acquisition value so the arithmetic still runs, but the disposal is marked
    `incomplete` and the filing pack refuses to present it as final — a zero
    cost maximises the gain, and copying it would mean paying too much.

    Each parcel keeps its unrounded euros; the sale's own valores are rounded
    once, from those sums. The parcel lines are rounded for display only and
    can sit a cent off the sale's total.
    """
    lots = sorted(asset.lots, key=lambda lot: lot.day)
    warnings: list[tuple[str, dict]] = []
    disposals: list[dict] = []

    for sale in sorted(asset.sales, key=lambda s: s.day):
        if sale.quantity <= 0:
            continue
        remaining = sale.quantity
        parcels: list[dict] = []
        # Unrounded (quantity, acquisition, proceeds) per parcel.
        exact: list[tuple[Decimal, Decimal, Decimal]] = []
        reasons: set[str] = set()
        if sale.incomplete:
            reasons.add("sin-tipo-de-cambio")

        def parcel(take: Decimal, acquisition: Decimal, lot: Lot | None) -> dict:
            proceeds = sale.proceeds_eur * take / sale.quantity
            exact.append((take, acquisition, proceeds))
            return {
                "quantity": qty(take),
                "acquisitionDate": lot.day if lot else None,
                "acquisitionValueEur": money(acquisition),
                "buyFeesEur": money(lot.fee_eur * take / lot.quantity) if lot and lot.quantity else 0.0,
                "disposalValueEur": money(proceeds),
                # Filled in below, once every parcel of this sale is known.
                "sellFeesEur": 0.0,
                "resultEur": float(cents(proceeds) - cents(acquisition)),
                "source": lot.source if lot else "uncovered",
                "platform": lot.platform if lot else None,
                "note": lot.note if lot else "Sin compra conocida",
                "fxRate": lot.fx_rate if lot else None,
                "fxSource": lot.fx_source if lot else None,
                "price": float(lot.price) if lot and lot.price is not None else None,
                "currency": lot.currency if lot else None,
                "lotQuantity": qty(lot.quantity) if lot else None,
                "incomplete": lot is None or lot.incomplete,
            }

        for lot in lots:
            if remaining <= 0:
                break
            # A lot bought after the sale cannot fund it, but stays queued for
            # later sales.
            if lot.remaining <= 0 or lot.day > sale.day:
                continue
            take = min(lot.remaining, remaining)
            lot.remaining -= take
            remaining -= take
            lot.used[len(disposals)] = lot.used.get(len(disposals), ZERO) + take
            parcels.append(parcel(take, lot.cost_eur * take / lot.quantity, lot))
            if lot.incomplete:
                reasons.add("sin-tipo-de-cambio")

        # A remainder whose proceeds round to 0,00 € is not a missing purchase:
        # it is the precision gap between venues (Bybit nets fees to 9
        # decimals, Revolut keeps 8), and no declared figure moves by it. It is
        # kept on the record but neither blocks the sale nor gets a line.
        dust = ZERO
        if remaining > 0 and cents(sale.proceeds_eur * remaining / sale.quantity) == 0:
            dust, remaining = remaining, ZERO
        if remaining > 0:
            parcels.append(parcel(remaining, ZERO, None))
            reasons.add("sin-compra")
            warnings.append((
                sale.day[:4],
                notice(
                    "lote-descubierto",
                    "warn",
                    symbol=asset.symbol,
                    quantity=qty(remaining),
                    date=sale.day,
                ),
            ))

        # The selling commission is charged on the order, not on any one lot, but
        # the return is filled one line per FIFO parcel — and Cartera de Valores
        # asks for "Gastos de la operación" on each one. Share it out pro rata by
        # quantity, with the rounding remainder on the last parcel so the lines add
        # back up to the cent to what the broker actually charged.
        if parcels and sale.fee_eur:
            fee = cents(sale.fee_eur)
            allotted = ZERO
            for line, (take, _acq, _proc) in zip(parcels[:-1], exact[:-1]):
                share = cents(fee * take / sale.quantity)
                line["sellFeesEur"] = float(share)
                allotted += share
            parcels[-1]["sellFeesEur"] = float(fee - allotted)

        acquisition = cents(sum((a for _t, a, _p in exact), ZERO))
        transmission = cents(sale.proceeds_eur)
        result = float(transmission - acquisition)
        disposals.append(
            {
                "date": sale.day,
                "quantity": qty(sale.quantity),
                "platform": sale.platform,
                "ref": sale.ref,
                "note": sale.note,
                "acquisitionValueEur": float(acquisition),
                "buyFeesEur": total(p["buyFeesEur"] for p in parcels),
                "disposalValueEur": float(transmission),
                "sellFeesEur": money(sale.fee_eur),
                "resultEur": result,
                # What the annex needs to show how the transmission was valued.
                "price": float(sale.price) if sale.price is not None else None,
                "currency": sale.currency,
                "fxRate": sale.fx_rate,
                "fxSource": sale.fx_source,
                "fxDate": sale.fx_date,
                # A sale with an unknown cost or an unofficial conversion is
                # computed, but never presented as a figure to file.
                "incomplete": bool(reasons),
                "incompleteReasons": sorted(reasons),
                "missingQuantity": qty(remaining) if remaining > 0 else 0.0,
                "dustQuantity": qty(dust),
                # Every disposal carries the deferral fields, so no consumer
                # has to guess what a missing key means. flag_two_month_rule
                # is the only thing that moves them off these defaults.
                "deferred": False,
                "deferredQuantity": 0.0,
                "deferredLossEur": 0.0,
                "computableResultEur": result,
                # A loss an earlier year could not compute, released by this
                # sale because it is the one closing the repurchased position.
                # Deliberately outside computableResultEur: it is a separate
                # pérdida patrimonial, not part of what this sale made.
                "reintegratedLossEur": 0.0,
                "reintegratedFrom": [],
                "parcels": parcels,
            }
        )

    return disposals, warnings


# ── Regla de los dos meses (art. 33.5 f LIRPF) ─────────────────────────────────


def held_at_year_end(lot: Lot, disposals: list[dict], year: str) -> Decimal:
    """Units of `lot` still in the portfolio at 31 December of `year`.

    Both callers need the position as the year closed rather than as it stands
    today: the two-month rule so a sale in a later year cannot retroactively
    unblock a filed one, and the yearly summary so `remainingQuantity` is what
    was held when that return was due.
    """
    if lot.day[:4] > year:
        return ZERO
    consumed = sum(
        (taken for index, taken in lot.used.items() if disposals[index]["date"][:4] <= year),
        ZERO,
    )
    return lot.quantity - consumed


def _shift_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def flag_two_month_rule(asset: Asset, disposals: list[dict]) -> list[tuple[str, dict]]:
    """Defer losses blocked by art. 33.5 f LIRPF: homogeneous securities
    repurchased within two months either side of the sale.

    Three things a naive reading of the article gets wrong, all of which cost
    real money at filing time:

      * **The shares sold are not their own repurchase.** A lot bought inside
        the window that this very sale consumed is not a repurchase of it —
        buying on Monday and selling on Tuesday closes a position rather than
        keeping it, and the rule exists to catch keeping it. Only the part of
        a lot the sale did *not* consume can restore the position.
      * **A partial repurchase defers a proportional part of the loss.** The
        DGT's reading is that buying back 25 of the 85 shares sold blocks
        25/85 of the loss; the remaining 60/85 is computable straight away.
        Blocking the whole loss silently overpays the year's tax.
      * **A repurchase that is itself sold again stops holding the position.**
        The deferred loss is integrated as the repurchased securities are
        transmitted, so a lot that is gone by 31 December never deferred
        anything *for that year* — buying 120 and selling all 120 the same
        morning is not a repurchase of itself, and neither is buying back in
        March and closing that position again in November. Only what is still
        held at the close of the year the loss was booked in can block it,
        which is also why the cut-off is the year end and not "today": a lot
        sold in a later year must not retroactively unblock a filed year.
        Selling it in a later year instead *releases* the deferred loss into
        that year, which is the second half of what the article says and what
        `reintegratedLossEur` on the releasing disposal carries.

    A repurchase can only restore as many shares as it actually bought, so
    lots are a budget shared across every sale they cover, spent oldest sale
    first — otherwise one purchase would defer the same shares several times.

    The window is two months for securities admitted to trading and one year
    for those that are not (art. 33.5 g), which `asset.listed` decides.

    Shares and ETFs only. For crypto the DGT's position on which window
    applies is not settled, so a loss there is flagged for review rather than
    deferred automatically.
    """
    months = 2 if asset.listed else 12
    notes: list[tuple[str, dict]] = []
    lots = sorted(asset.lots, key=lambda lot: lot.day)
    # Units of each lot still able to act as a repurchase, shared across sales.
    budget: dict[int, Decimal] = {id(lot): lot.quantity for lot in lots}
    # Per lot, the units it spent blocking a loss and the euros they block,
    # oldest loss first. Selling those units later hands the euros back — see
    # the reintegration pass at the end.
    claims: dict[int, list[dict]] = defaultdict(list)

    # Same-day sales of the same security are one disposal event with one
    # window, so the repurchase is spread over them pro rata rather than poured
    # into whichever the broker happened to list first — with different FIFO
    # costs per order, list order would otherwise change the tax owed.
    by_day: dict[str, list[tuple[int, dict]]] = {}
    for index, disposal in enumerate(disposals):
        if disposal["resultEur"] < 0:
            by_day.setdefault(disposal["date"], []).append((index, disposal))

    for day in sorted(by_day):
        group = by_day[day]
        sold_day = date.fromisoformat(day)
        window = (_shift_months(sold_day, -months), _shift_months(sold_day, months))

        candidates: list[tuple[Lot, Decimal]] = []
        for lot in lots:
            if not (window[0] <= date.fromisoformat(lot.day) <= window[1]):
                continue
            # Only the part of the lot still held when the year closes can be
            # a repurchase. That covers both ways a lot stops holding the
            # position: this day's own sales took it out (those are the very
            # shares transmitted, not a repurchase of them), or a later sale
            # in the same year did (the deferral would reintegrate that same
            # year, netting to nothing). Sales in *later* years are deliberately
            # ignored so regenerating the file cannot rewrite a filed year.
            spare = min(budget[id(lot)], held_at_year_end(lot, disposals, day[:4]))
            if spare > 0:
                candidates.append((lot, spare))
        available = sum((spare for _lot, spare in candidates), ZERO)
        if available <= 0:
            continue

        repurchases = sorted({lot.day for lot, _spare in candidates})

        if asset.asset_type == "crypto":
            for _index, disposal in group:
                disposal["review"] = "two-month-rule"
            notes.append((
                day[:4],
                notice(
                    "regla-dos-meses-cripto",
                    "warn",
                    symbol=asset.symbol,
                    lossEur=total(d["resultEur"] for _i, d in group),
                    date=day,
                    repurchases=repurchases,
                ),
            ))
            continue

        sold_total = sum((dec(disposal["quantity"]) for _index, disposal in group), ZERO)
        deferred_total = min(sold_total, available)
        # Spend the budget so a later sale cannot defer the same shares again.
        pending = deferred_total
        spent: list[tuple[Lot, Decimal]] = []
        for lot, spare in candidates:
            if pending <= 0:
                break
            take = min(spare, pending)
            budget[id(lot)] -= take
            pending -= take
            spent.append((lot, take))

        fraction = deferred_total / sold_total if sold_total else ZERO
        for _index, disposal in group:
            deferred_loss = cents(dec(disposal["resultEur"]) * fraction)
            disposal["deferred"] = True
            disposal["deferredQuantity"] = qty(dec(disposal["quantity"]) * fraction)
            disposal["deferredLossEur"] = float(deferred_loss)
            disposal["computableResultEur"] = float(dec(disposal["resultEur"]) - deferred_loss)
            disposal["deferredBecause"] = repurchases
            disposal["deferralWindowMonths"] = months

        # One warning per security and day, not per sell order: the reader
        # cares that 25 of 85 shares came back, not about a pro-rata slice of
        # 0,588 titles landing on the smallest of three orders.
        loss_total = sum((dec(d["resultEur"]) for _i, d in group), ZERO)
        deferred_loss_total = sum((dec(d["deferredLossEur"]) for _i, d in group), ZERO)

        # Note the euros against the very units that blocked them, so selling
        # part of the repurchase later releases the matching part.
        blocked_left = deferred_loss_total
        for position, (lot, take) in enumerate(spent):
            share = (
                blocked_left
                if position == len(spent) - 1
                else cents(deferred_loss_total * take / deferred_total)
            )
            blocked_left -= share
            claims[id(lot)].append(
                {"year": day[:4], "day": day, "units": take, "eur": share}
            )
        notes.append((
            day[:4],
            notice(
                "regla-dos-meses",
                "warn",
                symbol=asset.symbol,
                lossEur=float(loss_total),
                deferredEur=float(deferred_loss_total),
                computableEur=float(loss_total - deferred_loss_total),
                quantity=qty(deferred_total),
                soldQuantity=qty(sold_total),
                date=day,
                repurchases=repurchases,
                months=months,
            ),
        ))

    notes += _release_deferrals(asset, disposals, claims)
    return notes


def _release_deferrals(
    asset: Asset, disposals: list[dict], claims: dict[int, list[dict]]
) -> list[tuple[str, dict]]:
    """Hand a deferred loss back in the year the blocking shares are sold.

    Art. 33.5 f only postpones the loss: it is integrated "a medida que se
    transmitan los valores que permanezcan en el patrimonio". So the year that
    closes the repurchased position gets the euros the earlier year could not
    compute — proportionally, since selling 10 of the 25 shares that blocked a
    loss releases 10/25 of it.

    Only sales in a *later* year release anything. A sale in the same year as
    the loss never claimed the units in the first place: `flag_two_month_rule`
    already capped the repurchase at what was still held on 31 December, so a
    buy-back closed before year end simply never deferred.
    """
    notes: list[tuple[str, dict]] = []
    for lot in sorted(asset.lots, key=lambda lot: lot.day):
        pending = claims.get(id(lot))
        if not pending:
            continue
        for index in sorted(lot.used, key=lambda i: (disposals[i]["date"], i)):
            disposal = disposals[index]
            taken = lot.used[index]
            while taken > 0 and pending:
                claim = pending[0]
                # Claims are in loss-date order, so once the oldest is not
                # older than this sale, none of the rest is either.
                if disposal["date"][:4] <= claim["year"]:
                    break
                units = min(taken, claim["units"])
                # Exhausting a claim hands back what is left of it rather than
                # a fresh rounding, so the pieces add up to the euros deferred.
                amount = (
                    claim["eur"]
                    if units >= claim["units"]
                    else cents(claim["eur"] * units / claim["units"])
                )
                claim["units"] -= units
                claim["eur"] -= amount
                taken -= units
                if claim["units"] <= 0:
                    pending.pop(0)
                if amount == 0:
                    continue
                disposal["reintegratedLossEur"] = float(
                    dec(disposal["reintegratedLossEur"]) + amount
                )
                disposal["reintegratedFrom"].append(
                    {"date": claim["day"], "amountEur": float(amount), "quantity": qty(units)}
                )
                notes.append((
                    disposal["date"][:4],
                    notice(
                        "regla-dos-meses-reintegro",
                        "info",
                        symbol=asset.symbol,
                        amountEur=float(amount),
                        quantity=qty(units),
                        date=disposal["date"],
                        lossDate=claim["day"],
                    ),
                ))
    return notes


# ── DEGIRO ─────────────────────────────────────────────────────────────────────

# DEGIRO's product classification → how the IRPF treats the line. ETFs and
# funds follow the same capital-gains rules as shares here; the distinction is
# kept only so the filing pack can pick the right section of the form.
PRODUCT_TYPES = {"ETF": "etf", "FUND": "etf", "STOCK": "stock"}


def fetch_transactions(api, from_date: date, to_date: date) -> list[dict]:
    """Every trade over the range, raw.

    raw=True on purpose: the pydantic model declares `quantity: int` and would
    silently truncate fractional shares.
    """
    raw = api.get_transactions_history(
        transaction_request=HistoryRequest(
            from_date=from_date,
            to_date=to_date,
            group_transactions_by_order=False,
        ),
        raw=True,
    )
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    return [t for t in (data or []) if isinstance(t, dict)]


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_degiro(transactions: list[dict], meta: dict[str, dict]) -> list[dict]:
    """Flatten DEGIRO's trade rows into the ledger shape used everywhere below.

    Fees are derived as `totalPlusAllFees − total` rather than read from
    `totalFeesInBaseCurrency`, because that keeps buy cost and sell proceeds
    internally consistent whatever DEGIRO folds into which field.

    That figure is deliberately larger than what the Fees page shows for the
    same trades. On a USD order it works out to 0.25–0.38% of the notional:
    the flat handling fee *plus* the 0.25% AutoFX charge, which DEGIRO takes
    inside the exchange rate rather than as a separate cash movement — so
    fees.py, reading the cash statement, never sees it. For tax it
    belongs here: it is a cost inherent to the trade, and it is part of the
    euros that actually left or entered the account.
    """
    trades = []
    for t in transactions:
        product_id = str(t.get("productId") or "")
        product = meta.get(product_id, {})
        quantity = _num(t.get("quantity"))
        side = (t.get("buysell") or "").upper() or ("B" if quantity > 0 else "S")
        total = _num(t.get("totalInBaseCurrency"))
        total_all = _num(t.get("totalPlusAllFeesInBaseCurrency")) or total
        product_type = str(product.get("productTypeId") or product.get("productType") or "")

        trades.append(
            {
                "id": t.get("id"),
                "platform": "DEGIRO",
                "date": str(t.get("date") or "")[:10],
                "side": "buy" if side == "B" else "sell",
                "productId": product_id,
                "symbol": product.get("symbol") or product_id,
                "name": product.get("name") or product.get("symbol") or product_id,
                "isin": product.get("isin"),
                "assetType": PRODUCT_TYPES.get(product_type.upper(), "stock"),
                "currency": product.get("currency") or "EUR",
                "quantity": abs(quantity),
                "price": _num(t.get("price")),
                "fxRate": t.get("fxRate"),
                # Signed as DEGIRO books them: negative is money out.
                "totalEur": round(total, 2),
                "totalPlusFeesEur": round(total_all, 2),
                "feesEur": round(abs(total_all - total), 2),
                "transfered": bool(t.get("transfered")),
            }
        )
    return trades


# How far a broker's own conversion may sit from the ECB before it is taken
# for a misread figure. DEGIRO's AutoFX margin is 0.25 %, so anything near this
# is not a margin, it is a wrong number.
FX_DEVIATION_LIMIT = Decimal("0.05")


def check_degiro_fx(trades: list[dict], fx=None) -> list[tuple[str, dict]]:
    """Compare each foreign-currency DEGIRO trade against the ECB of its day.

    DEGIRO's euros are what actually left or entered the account — the
    "importe real" art. 35 asks for — so they are kept as they are. The check
    only catches a figure that cannot be a conversion at all: more than 5 %
    away from the ECB means a misread field, and then the ECB is used instead
    (the fees DEGIRO charged on top are kept).
    """
    fx = fx or (lambda currency, day: fx_to_eur(currency, day, prefer_local=True))
    warnings: list[tuple[str, dict]] = []
    for trade in trades:
        currency = (trade.get("currency") or "EUR").upper()
        native = abs(dec(trade.get("price")) * dec(trade.get("quantity")))
        if currency == "EUR" or not native or not trade.get("totalEur"):
            continue
        found = fx(currency, trade["date"])
        if not found:
            continue
        ecb = dec(found[0])
        implied = abs(dec(trade["totalEur"])) / native
        deviation = abs(implied - ecb) / ecb
        trade["impliedFxRate"] = float(implied.quantize(Decimal("1e-8")))
        trade["ecbFxRate"] = found[0]
        if deviation <= FX_DEVIATION_LIMIT:
            continue
        sign = -1 if dec(trade["totalEur"]) < 0 else 1
        fees = dec(trade["totalPlusFeesEur"]) - dec(trade["totalEur"])
        corrected = cents(native * ecb) * sign
        trade["totalEur"] = float(corrected)
        trade["totalPlusFeesEur"] = float(corrected + fees)
        trade["fxCorrected"] = True
        warnings.append((
            trade["date"][:4],
            notice(
                "tipo-cambio-desviado",
                "warn",
                symbol=trade["symbol"],
                date=trade["date"],
                currency=currency,
                brokerRate=float(implied),
                ecbRate=found[0],
                deviationPct=money(deviation * 100),
            ),
        ))
    return warnings


# ── Bybit ──────────────────────────────────────────────────────────────────────

# Quote currencies Bybit pairs against, longest first so "ETHUSDC" splits into
# ("ETH", "USDC") and not ("ETHUSD", "C").
BYBIT_QUOTES = ("USDC", "USDT", "USDE", "EUR", "USD", "BTC", "ETH")


def _split_pair(pair: str) -> tuple[str, str] | None:
    for quote in BYBIT_QUOTES:
        if pair.endswith(quote) and len(pair) > len(quote):
            return pair[: -len(quote)], quote
    return None


def parse_bybit(path: Path, debug: bool = False) -> list[dict]:
    """Acquisition lots from a Bybit spot-trade export.

    Two things about that file are easy to get wrong and are handled here:
    the pair is quoted in USDC (there is no EUR figure anywhere, so the rate
    has to come from the ECB), and `Fees` is charged in the coin *bought*, not
    in the quote currency — so the quantity actually acquired is
    `Filled Quantity − Fees`.
    """
    if not path.exists():
        return []

    lines = path.read_text().splitlines()
    # The export starts with an account header line before the real header.
    start = next((i for i, line in enumerate(lines) if line.startswith("Uid,")), 0)
    rows = list(csv.DictReader(lines[start:]))

    trades = []
    for row in rows:
        pair = (row.get("Spot Pairs") or "").strip()
        split = _split_pair(pair)
        if not split:
            if debug:
                print(f"  bybit: skipping unrecognised pair {pair!r}")
            continue
        symbol, quote = split
        direction = (row.get("Direction") or "").strip().upper()
        stamp = (row.get("Timestamp (UTC+0)") or "").strip()
        day = stamp[:10]
        filled = _num(row.get("Filled Quantity"))
        fee = _num(row.get("Fees"))
        value = _num(row.get("Filled Value"))
        if not day or filled <= 0:
            continue

        fx = fx_to_eur(quote, day)
        rate, fx_source, rate_day = fx if fx else (0.0, None, None)
        trades.append(
            {
                "id": row.get("Transaction ID"),
                "platform": "Bybit",
                "date": day,
                "side": "buy" if direction == "BUY" else "sell",
                "symbol": symbol,
                "name": symbol,
                "assetType": "crypto",
                "currency": quote,
                # The fee is taken out of the coin received, so it reduces the
                # quantity acquired instead of adding to the cost.
                "quantity": round(filled - fee if direction == "BUY" else filled, 10),
                "price": _num(row.get("Filled Price")),
                "totalEur": round(value * rate, 2),
                "totalPlusFeesEur": round(value * rate, 2),
                "feesEur": 0.0,
                "fxRate": rate if fx else None,
                "fxSource": fx_source,
                "fxDate": rate_day,
                # No official rate: the EUR figures above are placeholders.
                "incomplete": fx is None,
            }
        )
    return trades


# ── manual_trades.json ─────────────────────────────────────────────────────────


def load_manual(path: Path) -> dict:
    """Disposals, staking rewards and stray lots kept by hand, plus the two
    things only the user knows: which country a dividend really comes from
    (`dividendCountries`) and the loss balances their filed returns left
    pending (`pendingLosses`).

    Read best-effort like crypto_cost_basis.json: a missing or broken file
    means "nothing to add", never a failed export.
    """
    empty = {"disposals": [], "rewards": [], "lots": [], "pendingLosses": [], "dividendCountries": {}}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        print(f"  warning: could not read {path.name} ({exc}); ignoring it.", file=sys.stderr)
        return empty
    return {
        "disposals": data.get("disposals") or [],
        "rewards": data.get("rewards") or [],
        "lots": data.get("lots") or [],
        "pendingLosses": data.get("pendingLosses") or [],
        "dividendCountries": {
            str(k).upper(): str(v).upper()
            for k, v in (data.get("dividendCountries") or {}).items()
            if not str(k).startswith("_")
        },
    }


def load_revolut_ledger(out_dir: Path) -> dict:
    """Revolut's crypto ledger as written by revolut.py.

    Best-effort like load_manual: this file only exists once the Revolut export
    has run with a live session, and its absence just means "nothing to add".
    """
    path = out_dir / "data" / "revolut_trades.json"
    empty = {"trades": [], "deposits": [], "rewards": []}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        print(f"  warning: could not read {path.name} ({exc}); ignoring it.", file=sys.stderr)
        return empty
    return {
        "trades": data.get("trades") or [],
        "deposits": data.get("deposits") or [],
        "rewards": data.get("rewards") or [],
    }


def ledger_coverage(ledger: dict) -> dict[tuple[str, str], tuple[str, str]]:
    """(platform, symbol) → the first and last day the ledger knows about.

    Everything the ledger saw counts towards the window, deposits included: a
    pocket's history is complete for the span it covers, so a hand-written
    entry landing inside it is a duplicate of something already captured, while
    one outside it is still the only record there is.
    """
    window: dict[tuple[str, str], tuple[str, str]] = {}
    for group in ("trades", "deposits", "rewards"):
        for entry in ledger[group]:
            symbol = str(entry.get("symbol") or "").upper()
            platform = entry.get("platform") or "Revolut"
            day = str(entry.get("date") or "")[:10]
            if not symbol or not day:
                continue
            first, last = window.get((platform, symbol), (day, day))
            window[(platform, symbol)] = (min(first, day), max(last, day))
    return window


def superseded_by_ledger(
    coverage: dict[tuple[str, str], tuple[str, str]],
    platform: str,
    symbol: str,
    day: str,
) -> bool:
    """True when the ledger already accounts for this hand-written entry."""
    window = coverage.get((platform, symbol.upper()))
    return bool(window and window[0] <= day <= window[1])


def reward_value_eur(symbol: str, quantity: Decimal, day: str) -> tuple[Decimal, str]:
    """EUR market value of a staking reward on the day it was credited.

    That figure does double duty: it is the rendimiento del capital mobiliario
    taxed in the year it was received, and the acquisition value of those coins
    for the FIFO later. Computing it once keeps the two consistent.
    """
    price = crypto_price_eur_at(symbol, day) or crypto_price_eur(symbol)
    if not price:
        return ZERO, "unavailable"
    return dec(quantity) * dec(price), "coinbase"


# ── Building the per-year view ─────────────────────────────────────────────────


def queue_key(trade: dict) -> str:
    """The FIFO queue a security belongs to.

    Homogeneous securities (art. 37.2 LIRPF) are those of the same issuer with
    the same rights — the same ISIN — wherever they are held: the oldest
    bought go first counting every broker and every exchange together (TEAC,
    19/02/2026, RG 1535/2024). DEGIRO gives AAPL on NASDAQ and on Xetra two
    product ids, so keying on the product would run two separate queues.
    """
    isin = str(trade.get("isin") or "").strip().upper()
    if isin:
        return f"isin:{isin}"
    platform = str(trade.get("platform") or "manual").lower()
    return f"{platform}:{trade.get('productId') or trade['symbol']}"


def build_assets(
    degiro: list[dict], bybit: list[dict], manual: dict, ledger: dict
) -> tuple[dict[str, Asset], list[tuple[str, dict]], list[dict]]:
    """One Asset (one FIFO queue) per line of homogeneous securities."""
    assets: dict[str, Asset] = {}
    warnings: list[tuple[str, dict]] = []
    rewards: list[dict] = []
    # Where the ledger speaks, it overrules what was kept by hand — the hand
    # entries were only ever a stand-in for data the API wouldn't give up.
    coverage = ledger_coverage(ledger)
    superseded: list[str] = []

    def asset_for(key: str, **kwargs) -> Asset:
        if key not in assets:
            assets[key] = Asset(key=key, **kwargs)
        asset = assets[key]
        # The first source to mention a coin is usually the exchange export,
        # which only knows the ticker; let a later one supply the real name.
        name = kwargs.get("name")
        if name and asset.name == asset.symbol and name != asset.symbol:
            asset.name = name
        # One queue can now span several brokers; the row names all of them.
        platform = kwargs.get("platform")
        if platform and platform not in asset.platform.split(" + "):
            asset.platform = " + ".join(sorted(asset.platform.split(" + ") + [platform]))
        if kwargs.get("listed") is False:
            asset.listed = False
        if not asset.isin and kwargs.get("isin"):
            asset.isin = kwargs["isin"]
        return asset

    def fx_note(symbol: str, currency: str, day: str) -> None:
        warnings.append((
            day[:4],
            notice("sin-tipo-oficial", "warn", symbol=symbol, currency=currency, date=day),
        ))

    for trade in degiro:
        if _num(trade["quantity"]) <= 0:
            continue
        quantity = dec(trade["quantity"])
        asset = asset_for(
            queue_key(trade),
            symbol=trade["symbol"],
            name=trade["name"],
            asset_type=trade["assetType"],
            platform="DEGIRO",
            currency=trade["currency"],
            isin=trade.get("isin"),
        )
        note = f"{_num(trade['quantity']):g} @ {_num(trade['price']):g} {trade['currency']}"
        implied = trade.get("impliedFxRate")
        if implied is None and trade["currency"] == "EUR":
            implied = 1.0
        if trade["side"] == "buy":
            asset.lots.append(
                Lot(
                    day=trade["date"],
                    quantity=quantity,
                    cost_eur=abs(dec(trade["totalPlusFeesEur"])),
                    source="degiro",
                    fee_eur=dec(trade["feesEur"]),
                    note=note,
                    fx_rate=implied,
                    fx_source="ecb" if trade.get("fxCorrected") else "degiro",
                    platform="DEGIRO",
                    price=dec(trade["price"]),
                    currency=trade["currency"],
                )
            )
            if trade["transfered"]:
                warnings.append((
                    trade["date"][:4],
                    notice(
                        "traspaso-degiro", "warn", symbol=trade["symbol"], date=trade["date"]
                    ),
                ))
        else:
            asset.sales.append(
                Sale(
                    day=trade["date"],
                    quantity=quantity,
                    proceeds_eur=abs(dec(trade["totalPlusFeesEur"])),
                    fee_eur=dec(trade["feesEur"]),
                    platform="DEGIRO",
                    ref=str(trade["id"]) if trade.get("id") else None,
                    note=note,
                    price=dec(trade["price"]),
                    currency=trade["currency"],
                    fx_rate=implied,
                    fx_source="ecb" if trade.get("fxCorrected") else "degiro",
                )
            )

    # Crypto: homogeneity is per coin, wherever it happens to sit. Moving it
    # between your own wallets is not a disposal, so Bybit lots and Revolut
    # sales share one queue.
    for trade in bybit:
        asset = asset_for(
            f"crypto:{trade['symbol']}",
            symbol=trade["symbol"],
            name=trade["name"],
            asset_type="crypto",
            platform="Revolut",
            currency="EUR",
        )
        incomplete = bool(trade.get("incomplete"))
        if incomplete:
            fx_note(trade["symbol"], trade["currency"], trade["date"])
        if trade["side"] == "buy":
            asset.lots.append(
                Lot(
                    day=trade["date"],
                    quantity=dec(trade["quantity"]),
                    cost_eur=dec(trade["totalEur"]),
                    source="bybit",
                    note=f"{_num(trade['quantity']):.8f} @ {_num(trade['price']):g} {trade['currency']}",
                    fx_rate=trade.get("fxRate"),
                    fx_source=trade.get("fxSource"),
                    platform="Bybit",
                    price=dec(trade["price"]),
                    currency=trade["currency"],
                    incomplete=incomplete,
                )
            )
        else:
            asset.sales.append(
                Sale(
                    day=trade["date"],
                    quantity=dec(trade["quantity"]),
                    proceeds_eur=dec(trade["totalEur"]),
                    fee_eur=ZERO,
                    platform="Bybit",
                    ref=str(trade.get("id") or "") or None,
                    price=dec(trade["price"]),
                    currency=trade["currency"],
                    fx_rate=trade.get("fxRate"),
                    fx_source=trade.get("fxSource"),
                    fx_date=trade.get("fxDate"),
                    incomplete=incomplete,
                )
            )

    def to_eur_official(amounts: tuple, currency: str, day: str, symbol: str):
        """(eur amounts, rate, source, rate day, incomplete) at the ECB rate."""
        if currency == "EUR":
            return tuple(dec(a) for a in amounts), 1.0, "identity", day, False
        fx = fx_to_eur(currency, day)
        if fx is None:
            fx_note(symbol, currency, day)
            return tuple(ZERO for _ in amounts), None, None, None, True
        rate = dec(fx[0])
        return tuple(dec(a) * rate for a in amounts), fx[0], fx[1], fx[2], False

    # Revolut's own ledger. Deposits are deliberately not lots: moving your own
    # coins in from another wallet is not an acquisition, so the cost basis
    # stays with the original purchase (bybit.csv). A deposit the lots don't
    # cover surfaces as an uncovered slice in match_fifo, which is the honest
    # outcome — better a named warning than an invented cost.
    for trade in ledger["trades"]:
        symbol = str(trade.get("symbol") or "").upper()
        quantity = dec(_num(trade.get("quantity")))
        day = str(trade.get("date") or "")[:10]
        if not symbol or quantity <= 0 or not day:
            continue
        currency = (trade.get("currency") or "EUR").upper()
        (gross_eur, fee_eur), rate, source, rate_day, incomplete = to_eur_official(
            (_num(trade.get("grossAmount")), _num(trade.get("feeAmount"))), currency, day, symbol
        )
        platform = trade.get("platform") or "Revolut"
        asset = asset_for(
            f"crypto:{symbol}",
            symbol=symbol,
            name=trade.get("name") or symbol,
            asset_type="crypto",
            platform=platform,
            currency="EUR",
        )
        price = dec(_num(trade.get("grossAmount"))) / quantity
        if trade.get("side") == "buy":
            asset.lots.append(
                Lot(
                    day=day,
                    quantity=quantity,
                    # Valor de adquisición: what was paid plus the buying fee.
                    cost_eur=gross_eur + fee_eur,
                    source="revolut",
                    fee_eur=fee_eur,
                    note=trade.get("description"),
                    fx_rate=rate,
                    fx_source=source,
                    platform=platform,
                    price=price,
                    currency=currency,
                    incomplete=incomplete,
                )
            )
        else:
            asset.sales.append(
                Sale(
                    day=day,
                    quantity=quantity,
                    proceeds_eur=gross_eur - fee_eur,
                    fee_eur=fee_eur,
                    platform=platform,
                    ref=trade.get("id"),
                    note=trade.get("description"),
                    price=price,
                    currency=currency,
                    fx_rate=rate,
                    fx_source=source,
                    fx_date=rate_day,
                    incomplete=incomplete,
                )
            )

    def add_reward(symbol: str, quantity: Decimal, day: str, platform: str, name: str,
                   approximate: bool) -> None:
        value, source = reward_value_eur(symbol, quantity, day)
        asset = asset_for(
            f"crypto:{symbol}",
            symbol=symbol,
            name=name,
            asset_type="crypto",
            platform=platform,
            currency="EUR",
        )
        asset.lots.append(
            Lot(
                day=day,
                quantity=quantity,
                cost_eur=value,
                source="staking",
                note="Recompensa de staking, valorada el día de cobro",
                fx_source=source,
                platform=platform,
            )
        )
        rewards.append(
            {
                "date": day,
                "platform": platform,
                "symbol": symbol,
                "quantity": qty(quantity),
                "valueEur": money(value),
                "approximate": approximate,
                "priceSource": source,
            }
        )
        if source == "unavailable":
            warnings.append((
                day[:4],
                notice("staking-sin-precio", "warn", symbol=symbol, date=day),
            ))

    # Staking rewards straight from the ledger: real dates and amounts, so no
    # `approximate` flag and no estimate to revisit.
    for reward in ledger["rewards"]:
        symbol = str(reward.get("symbol") or "").upper()
        quantity = dec(_num(reward.get("quantity")))
        day = str(reward.get("date") or "")[:10]
        if not symbol or quantity <= 0 or not day:
            continue
        add_reward(symbol, quantity, day, reward.get("platform") or "Revolut", symbol, False)

    # Staking rewards kept by hand: acquisition lots whose cost is the value
    # already taxed as a rendimiento.
    for reward in manual["rewards"]:
        symbol = str(reward.get("symbol") or "").upper()
        quantity = dec(_num(reward.get("quantity")))
        day = str(reward.get("date") or "")[:10]
        if not symbol or quantity <= 0 or not day:
            continue
        platform_name = reward.get("platform") or "Revolut"
        if superseded_by_ledger(coverage, platform_name, symbol, day):
            superseded.append(f"recompensa {symbol} {day} ({platform_name})")
            continue
        add_reward(
            symbol, quantity, day, platform_name, reward.get("name") or symbol,
            bool(reward.get("approximate")),
        )

    def manual_key(entry: dict, symbol: str, asset_type: str) -> str:
        if asset_type == "crypto":
            return f"crypto:{symbol}"
        # Same ISIN, same queue as the DEGIRO lots of that security.
        return queue_key({"isin": entry.get("isin"), "platform": "manual", "symbol": symbol})

    # Extra acquisition lots kept by hand.
    for lot in manual["lots"]:
        symbol = str(lot.get("symbol") or "").upper()
        quantity = dec(_num(lot.get("quantity")))
        day = str(lot.get("date") or "")[:10]
        if not symbol or quantity <= 0 or not day:
            continue
        asset_type = lot.get("assetType") or "crypto"
        platform = lot.get("platform") or "manual"
        asset = asset_for(
            manual_key(lot, symbol, asset_type),
            symbol=symbol,
            name=lot.get("name") or symbol,
            asset_type=asset_type,
            platform=platform,
            currency="EUR",
            isin=lot.get("isin"),
            listed=lot.get("listed", True) is not False,
        )
        asset.lots.append(
            Lot(
                day=day,
                quantity=quantity,
                cost_eur=dec(_num(lot.get("acquisitionValueEur"))),
                source="manual",
                fee_eur=dec(_num(lot.get("buyFeeEur"))),
                note=lot.get("note"),
                platform=platform,
                price=dec(lot["unitPrice"]) if lot.get("unitPrice") is not None else None,
                currency=lot.get("currency"),
            )
        )

    # Disposals made outside DEGIRO.
    for disposal in manual["disposals"]:
        symbol = str(disposal.get("symbol") or "").upper()
        quantity = dec(_num(disposal.get("quantity")))
        day = str(disposal.get("date") or "")[:10]
        if not symbol or quantity <= 0 or not day:
            continue
        platform_name = disposal.get("platform") or "manual"
        if superseded_by_ledger(coverage, platform_name, symbol, day):
            superseded.append(f"venta {symbol} {day} ({platform_name})")
            continue
        asset_type = disposal.get("assetType") or "crypto"
        gross = _num(disposal.get("grossProceeds"))
        if not gross:
            gross = float(quantity * dec(_num(disposal.get("unitPrice"))))
        currency = (disposal.get("currency") or "EUR").upper()
        (gross_eur, fee_eur), rate, source, rate_day, incomplete = to_eur_official(
            (gross, _num(disposal.get("sellFee"))), currency, day, symbol
        )

        asset = asset_for(
            manual_key(disposal, symbol, asset_type),
            symbol=symbol,
            name=disposal.get("name") or symbol,
            asset_type=asset_type,
            platform=platform_name,
            currency="EUR",
            isin=disposal.get("isin"),
            listed=disposal.get("listed", True) is not False,
        )
        asset.sales.append(
            Sale(
                day=day,
                quantity=quantity,
                # Valor de transmisión: what came in, less the fees inherent
                # to the sale.
                proceeds_eur=gross_eur - fee_eur,
                fee_eur=fee_eur,
                platform=platform_name,
                ref=disposal.get("id"),
                note=disposal.get("note"),
                price=dec(gross) / quantity,
                currency=currency,
                fx_rate=rate,
                fx_source=source,
                fx_date=rate_day,
                incomplete=incomplete,
            )
        )

    if superseded:
        print(
            f"  {len(superseded)} entradas de manual_trades.json ya están en el ledger "
            f"de Revolut y se omiten: {', '.join(superseded[:4])}"
            + (" …" if len(superseded) > 4 else "")
        )

    return assets, warnings, rewards


# ── Rendimientos del capital mobiliario ────────────────────────────────────────


def load_dividends(out_dir: Path) -> list[dict]:
    """Payments already exported by dividends.py, if it has ever run."""
    path = out_dir / "data" / "dividends.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("payments") or []
    except Exception as exc:
        print(f"  warning: could not read dividends.json ({exc}); skipping them.", file=sys.stderr)
        return []


def dividends_for_year(
    payments: list[dict],
    year: str,
    isins: dict[str, str] | None = None,
    countries: dict[str, str] | None = None,
    fx=None,
) -> dict:
    """Dividends are rendimientos del capital mobiliario (art. 25.1 LIRPF): the
    same savings base as capital gains, a different section of the form.

    Each payment is converted at the ECB rate of its day from the native
    amounts, and its foreign withholding is split in two (art. 80 LIRPF and
    the conventions): what the treaty lets the source country keep is
    deductible in Spain (`creditableEur`); anything withheld above that is not
    Spain's to give back and has to be reclaimed from that country
    (`excessEur`). A Spanish company's withholding is a Spanish retención and
    goes to its own box instead.

    `isins` maps a DEGIRO productId to its ISIN; `countries` lets the user
    override the country by symbol, ISIN or productId — for depositary
    receipts, whose ISIN is the issuing bank's and not the paying company's.
    """
    fx = fx or fx_to_eur
    isins = isins or {}
    countries = countries or {}
    rows = []
    notes: list[dict] = []
    for payment in payments:
        if str(payment.get("date", ""))[:4] != year:
            continue
        day = str(payment["date"])[:10]
        currency = (payment.get("currency") or "EUR").upper()
        symbol = payment.get("symbol")
        incomplete = False
        fx_source = "identity"
        has_native = payment.get("gross") is not None
        if currency == "EUR":
            gross_eur = dec(payment["gross"] if has_native else payment.get("grossEur"))
            tax_eur = dec(payment["tax"] if has_native else payment.get("taxEur"))
        else:
            found = fx(currency, day) if has_native else None
            if found:
                rate = dec(found[0])
                gross_eur = dec(payment["gross"]) * rate
                tax_eur = dec(payment["tax"]) * rate
                fx_source = found[1]
            else:
                # No official rate: keep the exporter's own conversion so the
                # income is not left out, but never present it as final.
                gross_eur = dec(payment.get("grossEur"))
                tax_eur = dec(payment.get("taxEur"))
                fx_source = "unofficial"
                incomplete = True
                notes.append(
                    notice("sin-tipo-oficial", "warn", symbol=symbol, currency=currency, date=day)
                )
        gross_eur = cents(gross_eur)
        withheld = cents(abs(tax_eur))

        product = str(payment.get("productId") or "")
        isin = payment.get("isin") or isins.get(product)
        override = next(
            (countries[k] for k in (str(symbol or "").upper(), str(isin or "").upper(), product)
             if k and k in countries),
            None,
        )
        country, doubtful = dividend_country(isin, payment.get("name"), override)
        spanish = country == "ES"
        credit = creditable_tax(gross_eur, ZERO if spanish else withheld, country, year)
        if not spanish and withheld and not credit.known and int(year) in TREATY_RATES:
            notes.append(
                notice("dividendo-pais-desconocido", "warn", symbol=symbol, date=day, country=country)
            )
        if doubtful and country:
            notes.append(notice("dividendo-pais-dudoso", "warn", symbol=symbol, country=country))

        rows.append(
            {
                "date": day,
                "symbol": symbol,
                "name": payment.get("name"),
                "currency": currency,
                "isin": isin,
                "country": country,
                "grossEur": float(gross_eur),
                "withholdingEur": float(withheld),
                "netEur": float(gross_eur - withheld),
                "treatyRatePct": float(credit.rate * 100) if credit.rate is not None else None,
                "creditableEur": 0.0 if spanish else float(credit.creditable),
                "excessEur": 0.0 if spanish else float(credit.excess),
                "spanishWithholdingEur": float(withheld) if spanish else 0.0,
                "fxSource": fx_source,
                "incomplete": incomplete,
            }
        )

    excess_by_country: dict[str, Decimal] = defaultdict(lambda: ZERO)
    rate_by_country: dict[str, float] = {}
    for row in rows:
        if row["excessEur"]:
            excess_by_country[row["country"]] += dec(row["excessEur"])
            rate_by_country[row["country"]] = row["treatyRatePct"]
    for country, amount in sorted(excess_by_country.items()):
        notes.append(
            notice(
                "exceso-retencion",
                "warn",
                country=country,
                amountEur=float(amount),
                ratePct=rate_by_country[country],
                year=year,
            )
        )

    rows.sort(key=lambda r: r["date"], reverse=True)
    foreign = [r for r in rows if r["country"] != "ES"]
    # One notice per (id, symbol) is enough; a monthly payer would repeat it.
    seen: set = set()
    unique_notes = []
    for item in notes:
        key = (item["id"], item["params"].get("symbol"), item["params"].get("country"),
               item["params"].get("date") if item["id"] == "sin-tipo-oficial" else None)
        if key not in seen:
            seen.add(key)
            unique_notes.append(item)
    return {
        "grossEur": total(r["grossEur"] for r in rows),
        "withholdingEur": total(r["withholdingEur"] for r in rows),
        "netEur": total(r["netEur"] for r in rows),
        "foreignGrossEur": total(r["grossEur"] for r in foreign),
        "creditableEur": total(r["creditableEur"] for r in rows),
        "excessEur": total(r["excessEur"] for r in rows),
        "spanishWithholdingEur": total(r["spanishWithholdingEur"] for r in rows),
        "excessByCountry": [
            {"country": c, "amountEur": float(a), "treatyRatePct": rate_by_country[c]}
            for c, a in sorted(excess_by_country.items())
        ],
        "incomplete": any(r["incomplete"] for r in rows),
        "payments": rows,
        "notes": unique_notes,
    }


def staking_for_year(rewards: list[dict], year: str) -> dict:
    """Staking rewards paid by a platform are rendimientos del capital
    mobiliario too, but under art. 25.2 (cesión a terceros de capitales
    propios) — a different box from dividends, hence a separate block."""
    rows = [r for r in rewards if r["date"][:4] == year]
    rows.sort(key=lambda r: r["date"], reverse=True)
    return {
        "grossEur": total(r["valueEur"] for r in rows),
        "approximate": any(r["approximate"] for r in rows),
        "rewards": rows,
    }


# ── Checks ─────────────────────────────────────────────────────────────────────


def crypto_value_at_year_end(out_dir: Path, year: str) -> tuple[float, str] | None:
    """Crypto value closest to 31 December of `year`, from history.json.

    For a year still running there is no year end yet, so the latest snapshot
    is returned instead and the caller says which date it actually is.
    """
    path = out_dir / "data" / "history.json"
    if not path.exists():
        return None
    try:
        days = json.loads(path.read_text()).get("days") or []
    except Exception:
        return None
    candidates = [d for d in days if str(d.get("date", ""))[:4] == year and d.get("revolut")]
    if not candidates:
        return None
    last = max(candidates, key=lambda d: d["date"])
    return _num(last["revolut"].get("cryptoValueEur")), last["date"]


def non_deductible_fees(out_dir: Path, year: str) -> float:
    """Connectivity and custody charges: real costs, but not deductible in the
    IRPF. Only the commissions inherent to a purchase or a sale are, and those
    are already inside the acquisition and disposal values."""
    path = out_dir / "data" / "fees.json"
    if not path.exists():
        return 0.0
    try:
        charges = json.loads(path.read_text()).get("charges") or []
    except Exception:
        return 0.0
    return round(
        sum(
            abs(_num(c.get("amountEur")))
            for c in charges
            if str(c.get("date", ""))[:4] == year and c.get("category") != "transaction"
        ),
        2,
    )


def foreign_holdings_at_year_end(out_dir: Path, year: str) -> tuple[float, float, str] | None:
    """(cash, securities, as-of) held outside Spain, closest to 31 December.

    Crypto is left out on purpose: it belongs to the Modelo 721, and the two
    forms do not overlap. Same fallback as the crypto figure — a year still
    running has no year end, so the latest snapshot stands in and the caller
    says which date it really is.
    """
    path = out_dir / "data" / "history.json"
    if not path.exists():
        return None
    try:
        days = json.loads(path.read_text()).get("days") or []
    except Exception:
        return None
    candidates = [
        d for d in days if str(d.get("date", ""))[:4] == year and (d.get("degiro") or d.get("revolut"))
    ]
    if not candidates:
        return None
    last = max(candidates, key=lambda d: d["date"])
    degiro = last.get("degiro") or {}
    revolut = last.get("revolut") or {}
    cash = (
        _num(degiro.get("cashEur"))
        + _num(revolut.get("cashCurrentEur"))
        + _num(revolut.get("cashSavingsEur"))
    )
    # `valueEur` is the securities line only; DEGIRO reports its cash apart.
    securities = _num(degiro.get("valueEur"))
    return cash, securities, last["date"]


def build_checks(out_dir: Path, year: str, is_current: bool) -> list[dict]:
    checks: list[dict] = []

    crypto = crypto_value_at_year_end(out_dir, year)
    if crypto:
        value, as_of = crypto
        threshold = modelo_threshold(MODELO_721_THRESHOLDS, year)
    if crypto and threshold is not None:
        over = value >= threshold
        checks.append(
            notice(
                "modelo-721",
                "warn" if over else "info",
                valueEur=round(value, 2),
                asOf=as_of,
                open=is_current,
                over=over,
                threshold=threshold,
            )
        )

    foreign = foreign_holdings_at_year_end(out_dir, year)
    threshold = modelo_threshold(MODELO_720_THRESHOLDS, year)
    if foreign and threshold is not None:
        cash, securities, as_of = foreign
        # Per block, never on the sum: 40.000 € in accounts and 40.000 € in
        # securities is 80.000 € abroad and still nothing to file.
        blocks = [("cuentas", cash), ("valores", securities)]
        over_blocks = [name for name, value in blocks if value >= threshold]
        checks.append(
            notice(
                "modelo-720",
                "warn" if over_blocks else "info",
                accountsEur=round(cash, 2),
                securitiesEur=round(securities, 2),
                asOf=as_of,
                open=is_current,
                over=bool(over_blocks),
                overBlocks=over_blocks,
                overLabel=" y ".join(over_blocks).capitalize(),
                threshold=threshold,
            )
        )

    fees = non_deductible_fees(out_dir, year)
    if fees:
        checks.append(notice("comisiones-no-deducibles", "info", amountEur=fees, year=year))

    return checks


# ── Assembling the export ──────────────────────────────────────────────────────


def aggregate_asset_year(asset: Asset, disposals: list[dict], remaining) -> dict:
    """One row of the summary table: everything sold of one asset in one year."""
    remaining = dec(remaining)
    acquisition_dates = sorted(
        {p["acquisitionDate"] for d in disposals for p in d["parcels"] if p["acquisitionDate"]}
    )
    disposal_dates = sorted({d["date"] for d in disposals})

    return {
        "key": asset.key,
        "symbol": asset.symbol,
        "name": asset.name,
        "assetType": asset.asset_type,
        "platform": asset.platform,
        "isin": asset.isin,
        "currency": asset.currency,
        "listed": asset.listed,
        "quantity": qty(sum((dec(d["quantity"]) for d in disposals), ZERO)),
        "remainingQuantity": qty(remaining) if remaining > 0 else 0.0,
        "fullExit": remaining <= 0,
        "acquisitionDate": acquisition_dates[0] if len(acquisition_dates) == 1 else "varias",
        "acquisitionDates": acquisition_dates,
        "acquisitionValueEur": total(d["acquisitionValueEur"] for d in disposals),
        "disposalDate": disposal_dates[0] if len(disposal_dates) == 1 else "varias",
        "disposalDates": disposal_dates,
        "disposalValueEur": total(d["disposalValueEur"] for d in disposals),
        "buyFeesEur": total(d["buyFeesEur"] for d in disposals),
        "sellFeesEur": total(d["sellFeesEur"] for d in disposals),
        "resultEur": total(d["resultEur"] for d in disposals),
        # Partial deferrals mean this is no longer "the disposals that aren't
        # deferred" but a sum of per-disposal computable halves.
        "computableResultEur": total(d["computableResultEur"] for d in disposals),
        "deferredLossEur": total(d["deferredLossEur"] for d in disposals),
        "reintegratedLossEur": total(d["reintegratedLossEur"] for d in disposals),
        "deferred": any(d.get("deferred") for d in disposals),
        "review": any(d.get("review") for d in disposals),
        "incomplete": any(d.get("incomplete") for d in disposals),
        "disposals": disposals,
    }


def pending_from_manual(entries: list[dict]) -> list[dict]:
    """`pendingLosses` from manual_trades.json, normalised.

    Each entry is one line of "Saldos netos negativos … pendientes de
    compensación" from a filed return: the origin year, the block ("gp" for
    ganancias y pérdidas, "rcm" for rendimientos del capital mobiliario), and
    the amount still pending at 1 January of `pendingAt` — the year whose
    return it is to be used in. Without `pendingAt` the amount is taken as
    pending from the year after the origin.
    """
    out = []
    for entry in entries:
        try:
            origin = int(entry.get("originYear") or entry.get("year"))
        except (TypeError, ValueError):
            continue
        block = str(entry.get("block") or "").lower()
        if block not in ("gp", "rcm"):
            continue
        amount = abs(dec(_num(entry.get("amountEur", entry.get("amount")))))
        pending_at = int(entry.get("pendingAt") or origin + 1)
        out.append({"origin": origin, "block": block, "amount": amount, "pendingAt": pending_at})
    return out


def integrate_savings(years: list[dict], manual_pending: list[dict]) -> None:
    """Base del ahorro, cuota and doble imposición for every year, oldest first.

    The balances each year leaves pending feed the next one, so this walks the
    years in order. A balance written in manual_trades.json is what a filed
    return says, so from its `pendingAt` year it overrides whatever was
    computed here for that origin year and block.
    """
    state: dict[tuple[int, str], Decimal] = {}
    unapplied = sorted(manual_pending, key=lambda e: e["pendingAt"])
    for year_data in sorted(years, key=lambda y: y["year"]):
        year = int(year_data["year"])
        while unapplied and unapplied[0]["pendingAt"] <= year:
            entry = unapplied.pop(0)
            state[(entry["origin"], entry["block"])] = entry["amount"]

        gains = dec(year_data["capitalGains"]["netEur"])
        income_block = year_data["investmentIncome"]
        income = dec(income_block["dividends"]["grossEur"]) + dec(income_block["staking"]["grossEur"])
        pending = [Pending(o, b, a) for (o, b), a in state.items() if a > 0]
        result = integrate(year, gains, income, pending)
        state = {(p.origin, p.block): p.amount for p in result.carried}

        base = result.gains + result.income
        cuota = savings_tax(base, year)
        dividends = income_block["dividends"]
        foreign = dec(dividends["foreignGrossEur"])
        # The foreign income left in the base after offsetting, reduced in the
        # same proportion as the whole income block.
        foreign_in_base = cents(foreign * result.income / income) if income > 0 else ZERO
        if cuota is None:
            avg = deduction = None
            net_cuota = None
        else:
            avg = average_rate_pct(cuota, base)
            deduction = double_tax_deduction(dividends["creditableEur"], foreign_in_base, avg)
            net_cuota = cuota - deduction

        notes = []
        if cuota is None:
            notes.append(notice("sin-reglas", "warn", year=str(year), what="la escala del ahorro"))
        if dividends["withholdingEur"] and year not in TREATY_RATES:
            notes.append(notice("sin-reglas", "warn", year=str(year), what="los convenios de doble imposición"))
        for item in result.expired:
            notes.append(
                notice(
                    "perdidas-caducadas",
                    "warn",
                    origin=item.origin,
                    block=item.block,
                    amountEur=float(item.amount),
                    year=str(year),
                )
            )
        year_data["warnings"].extend(notes)

        year_data["savings"] = {
            "gainsBlockEur": float(cents(gains)),
            "incomeBlockEur": float(cents(income)),
            "crossOffsetEur": float(result.cross_offset),
            "pendingApplied": [
                {
                    "originYear": a["origin"],
                    "block": a["block"],
                    "ownBlockEur": float(a["ownEur"]),
                    "otherBlockEur": float(a["otherEur"]),
                }
                for a in result.applied
            ],
            "pendingCarried": [
                {"originYear": p.origin, "block": p.block, "amountEur": float(p.amount)}
                for p in result.carried
            ],
            "pendingExpired": [
                {"originYear": p.origin, "block": p.block, "amountEur": float(p.amount)}
                for p in result.expired
            ],
            "gainsInBaseEur": float(result.gains),
            "incomeInBaseEur": float(result.income),
            "baseEur": float(base),
            "scaleKnown": cuota is not None,
            "cuotaEur": float(cuota) if cuota is not None else None,
            "averageRatePct": float(avg) if avg is not None else None,
            "foreignIncomeInBaseEur": float(foreign_in_base),
            "creditableForeignTaxEur": dividends["creditableEur"],
            "doubleTaxDeductionEur": float(deduction) if deduction is not None else None,
            "cuotaAfterDeductionEur": float(net_cuota) if net_cuota is not None else None,
            "treatySource": TREATY_SOURCE,
        }
        # The headline estimate: the whole savings base, offsets applied,
        # less the double-taxation deduction. None rather than a guess for a
        # year with no scale.
        year_data["capitalGains"]["estimatedTaxEur"] = (
            float(net_cuota) if net_cuota is not None else None
        )


def build_years(
    assets: dict[str, Asset],
    out_dir: Path,
    dividends: list[dict],
    rewards: list[dict],
    base_warnings: list[tuple[str, dict]],
    manual: dict | None = None,
    isins: dict[str, str] | None = None,
) -> list[dict]:
    manual = manual or {}
    rows_by_year: dict[str, list[dict]] = defaultdict(list)
    warnings_by_year: dict[str, list[dict]] = defaultdict(list)
    for year, item in base_warnings:
        warnings_by_year[year].append(item)

    for asset in assets.values():
        disposals, warnings = match_fifo(asset)
        notes = flag_two_month_rule(asset, disposals)
        for year, item in warnings + notes:
            warnings_by_year[year].append(item)
        if not disposals:
            continue

        grouped: dict[str, list[dict]] = defaultdict(list)
        for disposal in disposals:
            grouped[disposal["date"][:4]].append(disposal)
        # Each year gets the position as *it* closed, not as it stands now.
        for year, year_disposals in grouped.items():
            left = sum((held_at_year_end(lot, disposals, year) for lot in asset.lots), ZERO)
            rows_by_year[year].append(aggregate_asset_year(asset, year_disposals, left))

    # A year with only dividends, or only a staking reward, still has to show up.
    years = set(rows_by_year) | set(warnings_by_year)
    years |= {str(p.get("date", ""))[:4] for p in dividends if p.get("date")}
    years |= {r["date"][:4] for r in rewards}
    years.discard("")

    this_year = str(date.today().year)
    out = []
    for year in sorted(years, reverse=True):
        rows = sorted(rows_by_year.get(year, []), key=lambda r: r["resultEur"])
        gains = total(r["computableResultEur"] for r in rows if r["computableResultEur"] > 0)
        losses = total(r["computableResultEur"] for r in rows if r["computableResultEur"] < 0)
        deferred = total(d["deferredLossEur"] for r in rows for d in r["disposals"])
        # Losses an earlier year deferred and this one gets back, because the
        # repurchase that blocked them was sold. They never belonged to any
        # single sale's result, so they are their own line — but they are
        # unquestionably part of what the year nets out to.
        reintegrated = total(r["reintegratedLossEur"] for r in rows)
        net = total([gains, losses, reintegrated])
        dividend_block = dividends_for_year(
            dividends, year, isins, manual.get("dividendCountries")
        )
        dividend_notes = dividend_block.pop("notes")
        staking_block = staking_for_year(rewards, year)

        out.append(
            {
                "year": year,
                "filedIn": str(int(year) + 1),
                "open": year == this_year,
                "capitalGains": {
                    "totalGainEur": gains,
                    "totalLossEur": losses,
                    "netEur": net,
                    "deferredLossEur": deferred,
                    "reintegratedLossEur": reintegrated,
                    # Set by integrate_savings once every year is known.
                    "estimatedTaxEur": None,
                    "incomplete": any(r["incomplete"] for r in rows),
                    "byAsset": rows,
                },
                "investmentIncome": {
                    "dividends": dividend_block,
                    "staking": staking_block,
                },
                "checks": build_checks(out_dir, year, year == this_year),
                "warnings": warnings_by_year.get(year, []) + dividend_notes,
            }
        )
    integrate_savings(out, pending_from_manual(manual.get("pendingLosses") or []))
    return out


# ── Renta Web filing pack ──────────────────────────────────────────────────────

# Section titles are the stable part of the form; box numbers are renumbered
# almost every year, so none is hardcoded here as if it were fact. They are
# looked up in the AEAT's dictionary of the year (renta_casillas.py) and, where
# that fails, the agent navigates by these titles and the field labels.
SECTIONS = {
    "acciones-cotizadas": (
        "Ganancias y pérdidas patrimoniales derivadas de la transmisión de acciones o "
        "participaciones negociadas en mercados oficiales"
    ),
    "monedas-virtuales": (
        "Ganancias y pérdidas patrimoniales derivadas de la transmisión de monedas virtuales "
        "por particulares"
    ),
    "perdidas-reintegradas": (
        "Pérdidas patrimoniales de ejercicios anteriores no computadas por recompra de "
        "valores homogéneos, que se integran al transmitirse esos valores"
    ),
    "dividendos": (
        "Rendimientos del capital mobiliario. Dividendos y demás rendimientos por la "
        "participación en fondos propios de entidades"
    ),
    "staking": (
        "Rendimientos del capital mobiliario. Rendimientos por la cesión a terceros de "
        "capitales propios"
    ),
    "doble-imposicion": (
        "Deducción por doble imposición internacional por razón de las rentas obtenidas y "
        "gravadas en el extranjero"
    ),
    "saldos-pendientes": (
        "Saldos netos negativos de ejercicios anteriores pendientes de compensación, a "
        "integrar en la base imponible del ahorro"
    ),
}


def _es_date(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _merge_staking_parcels(parcels: list[dict]) -> list[dict]:
    """The staking parcels of one sale collapsed into a single line.

    A year of daily rewards is a year of FIFO lots, and one entry per lot means
    dozens of lines of a few cents each that together come to a handful of
    euros — more form to fill than the amount deserves. They all share the
    disposal date, so the only thing adding them up loses is the acquisition
    date, and that is precisely what the "valores adquiridos en distintas
    fechas" box is for. The rewards stay one by one in `taxes.json`, which is
    the audit trail; this only shortens the filing brief.
    """
    staking = [p for p in parcels if p["source"] == "staking"]
    if len(staking) < 2:
        return parcels
    days = sorted(p["acquisitionDate"] for p in staking if p["acquisitionDate"])
    acquisition = round(sum(p["acquisitionValueEur"] for p in staking), 2)
    disposal = round(sum(p["disposalValueEur"] for p in staking), 2)
    merged = {
        "quantity": round(sum(p["quantity"] for p in staking), 10),
        # No single date to give: the entry says "varias" and carries the range.
        "acquisitionDate": None,
        "acquisitionValueEur": acquisition,
        "buyFeesEur": round(sum(p["buyFeesEur"] for p in staking), 2),
        "disposalValueEur": disposal,
        "sellFeesEur": round(sum(p["sellFeesEur"] for p in staking), 2),
        # From the two totals, not from the sum of the parcels' own results, so
        # the line still reads transmisión − adquisición = resultado.
        "resultEur": round(disposal - acquisition, 2),
        "source": "staking",
        "note": "Recompensas de staking agrupadas en una sola línea",
        "fxRate": None,
        "fxSource": None,
        "mergedCount": len(staking),
        "mergedFrom": days[0] if days else None,
        "mergedTo": days[-1] if days else None,
    }
    out: list[dict] = []
    placed = False
    for parcel in parcels:
        if parcel["source"] != "staking":
            out.append(parcel)
        elif not placed:
            # In the place of the first reward lot, so the line keeps its spot
            # in the FIFO order.
            out.append(merged)
            placed = True
    return out


def _split_deferral(disposal: dict, parcels: list[dict]) -> list[float]:
    """The sale's deferred loss shared out over its parcels, pro rata by
    result and summing back to it exactly."""
    total = disposal["deferredLossEur"]
    if not total or not parcels:
        return [0.0] * len(parcels)
    result = disposal["resultEur"]
    if not result:
        return [0.0] * len(parcels)
    shares = [round(total * p["resultEur"] / result, 2) for p in parcels[:-1]]
    shares.append(round(total - sum(shares), 2))
    return shares


def _missing_text(row: dict, disposal: dict, parcel: dict | None = None) -> str | None:
    """What the acquisition-value field says instead of a number when the
    figure cannot be filed: the purchase is missing, or a conversion had no
    official rate."""
    unit = "monedas" if row["assetType"] == "crypto" else "acciones"
    if parcel is not None:
        if parcel["source"] == "uncovered":
            return f"Faltan compras de {_es_qty(parcel['quantity'])} {unit}"
        if parcel.get("incomplete"):
            return "Falta el tipo de cambio oficial de la compra"
        if disposal.get("incomplete") and "sin-tipo-de-cambio" in disposal["incompleteReasons"]:
            return "Falta el tipo de cambio oficial de la venta"
        return None
    if not disposal.get("incomplete"):
        return None
    if disposal.get("missingQuantity"):
        return f"Faltan compras de {_es_qty(disposal['missingQuantity'])} {unit}"
    return "Falta el tipo de cambio oficial"


def _gain_entries(row: dict) -> tuple[list[dict], list[dict]]:
    """One entry per sale, plus the per-parcel breakdown as the fallback.

    What the law taxes is the transmission, not the FIFO parcel: one sell order
    is one alteración patrimonial whose result is worked out internally by
    FIFO. And since 2015 the acquisition date does not change the tax — every
    gain goes to the base del ahorro regardless of how long it was held — so
    splitting a sale by acquisition date buys nothing but lines to type. It
    would only matter for coeficientes de abatimiento (acquired before
    31/12/1994), which nothing here is. The broker's own datos fiscales, which
    the return is checked against, come per operation too.

    The per-parcel list is still built, because the screen may insist on one
    acquisition date per line; it goes to `parcelEntries` for that case.
    """
    label = "Denominación de la moneda virtual" if row["assetType"] == "crypto" else "Entidad emisora"

    def fees(buy: float, sell: float, acquisition: float, disposal: float) -> dict:
        """The two commissions apart, plus the figures they were folded into.

        Renta Web's own window has no box for them — the buying commission is
        already inside the valor de adquisición and the selling one already out
        of the valor de transmisión. Cartera de Valores is the other route and
        does ask for them ("Gastos de la operación", on the AD and on the TR
        alike), and there the amount of the operation goes gross, so both
        readings are given rather than left to be worked out at the keyboard.
        """
        return {
            "buyEur": round(buy, 2),
            "sellEur": round(sell, 2),
            "grossAcquisitionEur": round(acquisition - buy, 2),
            "grossDisposalEur": round(disposal + sell, 2),
        }

    entries = []
    # One counter for the whole asset: two sell orders of the same stock on the
    # same day would otherwise both number their parcels from 1, and the ids are
    # what an agent ticks off as it fills the form.
    index = 0
    for disposal in row["disposals"]:
        # The deferral is computed on the whole sale, but the form is filled in
        # one line per FIFO parcel — so it has to be split across them, or the
        # same blocked euros get marked once per acquisition date and the
        # return blocks several times the loss the rule actually blocks. Split
        # pro rata by result, with the rounding remainder on the last parcel so
        # the lines add back up to the sale's deferral to the cent.
        parcels = _merge_staking_parcels(disposal["parcels"])
        parcel_deferred = _split_deferral(disposal, parcels)
        for parcel, deferred_eur in zip(parcels, parcel_deferred):
            index += 1
            acquired = (
                "varias" if parcel.get("mergedCount") else _es_date(parcel["acquisitionDate"])
            )
            fields = [
                {"label": label, "value": row["name"], "format": "text"},
                {"label": "Fecha de transmisión", "value": _es_date(disposal["date"]), "format": "date"},
                {"label": "Valor de transmisión", "value": parcel["disposalValueEur"], "format": "eur"},
                {"label": "Fecha de adquisición", "value": acquired, "format": "date"},
                {"label": "Valor de adquisición", "value": parcel["acquisitionValueEur"], "format": "eur"},
            ]
            missing = _missing_text(row, disposal, parcel)
            if missing:
                fields[-1] = {"label": "Valor de adquisición", "value": missing, "format": "missing"}
            if row["assetType"] != "crypto":
                fields.insert(1, {
                    "label": "Nº de valores transmitidos",
                    "value": parcel["quantity"],
                    "format": "number",
                })
            entries.append(
                {
                    # "-lote-" keeps these apart from the per-sale ids, which
                    # are also numbered when a stock was sold twice in a day.
                    "id": f"{row['symbol']}-{disposal['date']}-lote-{index}",
                    "asset": row["symbol"],
                    "isin": row["isin"],
                    "resultEur": parcel["resultEur"],
                    "feesEur": fees(
                        parcel["buyFeesEur"],
                        parcel["sellFeesEur"],
                        parcel["acquisitionValueEur"],
                        parcel["disposalValueEur"],
                    ),
                    "incomplete": bool(missing),
                    "deferred": bool(disposal.get("deferred")),
                    "deferredLossEur": deferred_eur,
                    "partiallyDeferred": bool(
                        disposal.get("deferred")
                        and disposal["deferredLossEur"] != disposal["resultEur"]
                    ),
                    "fields": fields,
                }
            )
            if parcel.get("mergedCount"):
                entries[-1]["merged"] = {
                    "kind": "staking",
                    "count": parcel["mergedCount"],
                    "fromDate": _es_date(parcel["mergedFrom"]),
                    "toDate": _es_date(parcel["mergedTo"]),
                }

    # One line per sale: the deferral is taken from the sale itself, not split
    # and reassembled, and the transmission date stays the real one — merging
    # two different sale days under "varias" would blur the date that fixes the
    # ejercicio, which is not the same kind of shortcut as merging acquisition
    # dates.
    sales: list[dict] = []
    seen: dict[str, int] = {}
    for disposal in row["disposals"]:
        parcels = _merge_staking_parcels(disposal["parcels"])
        days = sorted({p["acquisitionDate"] for p in parcels if p["acquisitionDate"]})
        seen[disposal["date"]] = seen.get(disposal["date"], 0) + 1
        # Two sell orders of the same stock on the same day are two entries
        # that would otherwise share an id.
        suffix = f"-{seen[disposal['date']]}" if seen[disposal["date"]] > 1 else ""
        fields = [
            {"label": label, "value": row["name"], "format": "text"},
            {"label": "Fecha de transmisión", "value": _es_date(disposal["date"]), "format": "date"},
            {"label": "Valor de transmisión", "value": disposal["disposalValueEur"], "format": "eur"},
            {
                "label": "Fecha de adquisición",
                "value": _es_date(days[0]) if len(days) == 1 else "varias",
                "format": "date",
            },
            {"label": "Valor de adquisición", "value": disposal["acquisitionValueEur"], "format": "eur"},
        ]
        missing = _missing_text(row, disposal)
        if missing:
            fields[-1] = {"label": "Valor de adquisición", "value": missing, "format": "missing"}
        if row["assetType"] != "crypto":
            fields.insert(1, {
                "label": "Nº de valores transmitidos",
                "value": disposal["quantity"],
                "format": "number",
            })
        staking = next((p for p in parcels if p.get("mergedCount")), None)
        sales.append(
            {
                "id": f"{row['symbol']}-{disposal['date']}{suffix}",
                "asset": row["symbol"],
                "isin": row["isin"],
                "resultEur": disposal["resultEur"],
                "incomplete": bool(missing),
                "feesEur": fees(
                    disposal["buyFeesEur"],
                    disposal["sellFeesEur"],
                    disposal["acquisitionValueEur"],
                    disposal["disposalValueEur"],
                ),
                "deferred": bool(disposal.get("deferred")),
                "deferredLossEur": disposal["deferredLossEur"],
                "partiallyDeferred": bool(
                    disposal.get("deferred")
                    and disposal["deferredLossEur"] != disposal["resultEur"]
                ),
                # What the line is made of, so the acquisition dates it folds
                # away are still on the page and `parcelEntries` can be found.
                "acquisitionDates": [_es_date(d) for d in days],
                "parcelCount": len(parcels),
                "fields": fields,
                **(
                    {"merged": {
                        "kind": "staking",
                        "count": staking["mergedCount"],
                        "fromDate": _es_date(staking["mergedFrom"]),
                        "toDate": _es_date(staking["mergedTo"]),
                    }}
                    if staking
                    else {}
                ),
            }
        )
    return sales, entries


# What the commission split in `feesEur` is for, per section. Renta Web's own
# window takes the two valores already net of commissions and offers no box for
# them; Cartera de Valores, the alternative route for listed shares, asks for
# "Gastos de la operación" on every acquisition (AD) and every transmission
# (TR) and wants the amount of the operation gross. Crypto has no equivalent
# helper, so there the split is only there to be checked against the broker.
FEE_NOTE = {
    "acciones-cotizadas": (
        " Cada entrada trae además `feesEur` con la comisión de compra y la de venta por "
        "separado. En esta pantalla NO se teclean: la de compra ya está sumada dentro del "
        "valor de adquisición y la de venta ya está restada del valor de transmisión. Solo "
        "hacen falta si rellenas por el programa Cartera de Valores, que pide «Gastos de la "
        "operación» tanto en el alta de adquisición (AD) como en la de transmisión (TR); en "
        "ese caso el importe de la operación va bruto, y lo tienes en "
        "`grossAcquisitionEur` y `grossDisposalEur`."
    ),
    "monedas-virtuales": (
        " Cada entrada trae además `feesEur` con la comisión de compra y la de venta por "
        "separado, para poder cuadrarlas con el extracto. NO se teclean: la de compra ya "
        "está sumada dentro del valor de adquisición y la de venta ya está restada del "
        "valor de transmisión."
    ),
}


BLOCKED_NOTE = (
    "⛔ NO RELLENES ESTE APARTADO hasta completar los datos: hay ventas sin la compra que "
    "las cubre o sin tipo de cambio oficial (entradas con `incomplete: true`, cuyo valor de "
    "adquisición dice qué falta). Con coste 0 la ganancia sale inflada y pagarías de más. "
)


# Cited once at the top of the annex, not under every sale.
ANNEX_RULES = [
    "Art. 35 LIRPF: valores de adquisición y de transmisión por su importe real, con los "
    "gastos y tributos inherentes a la operación.",
    "Art. 37.1.a LIRPF: transmisión de valores admitidos a negociación.",
    "Art. 37.2 LIRPF: valores homogéneos — se transmiten primero los adquiridos antes (FIFO), "
    "sumando todos los intermediarios.",
    "Art. 33.5.f y g LIRPF: pérdidas no computables por recompra de valores homogéneos "
    "(dos meses si cotizan, un año si no).",
    "Art. 49 LIRPF: integración y compensación en la base imponible del ahorro.",
    "CNMV, Guía informativa sobre la fiscalidad de los productos financieros.",
]

SOURCE_FILES = {
    "DEGIRO": "historial de operaciones de DEGIRO (API, totalPlusAllFeesInBaseCurrency)",
    "Bybit": "bybit.csv (export de operaciones spot de Bybit)",
    "Revolut": "data/revolut_trades.json (ledger de Revolut)",
    "manual": "manual_trades.json",
}

_FX_SOURCE_TEXT = {
    "degiro": "conversión real del bróker",
    "ecb": "tipo de referencia del BCE",
    "ecb-file": "tipo de referencia del BCE",
    "identity": None,
}


def _fx_text(rate, source, rate_day, currency) -> str | None:
    if not currency or currency == "EUR" or source == "identity":
        return None
    if rate is None:
        return "sin tipo de cambio oficial"
    label = _FX_SOURCE_TEXT.get(source, source or "tipo de cambio")
    when = f" del {_es_date(rate_day)}" if rate_day and source in ("ecb", "ecb-file") else ""
    return f"{label}{when}: 1 {currency} = {_es_num(rate, 6)} EUR"


def _es_num(value, places: int | None = None) -> str:
    """A plain number with a decimal comma: prices, rates, quantities."""
    text = f"{float(value):.{places}f}" if places is not None else f"{float(value):g}"
    return text.replace(".", ",")


def build_annex(year_data: dict) -> dict:
    """How every figure of every sale was reached, for a requerimiento.

    Foreign brokers don't report the acquisition cost to the AEAT, so proving
    it is on the taxpayer. One block per sale: the transmission (price ×
    quantity → conversion → less commissions), one line per FIFO lot of the
    acquisition, the result, and the repurchase deferral if any.
    """
    sales = []
    platforms: set[str] = set()
    for row in year_data["capitalGains"]["byAsset"]:
        for disposal in row["disposals"]:
            platforms.add(disposal["platform"])
            gross = total([disposal["disposalValueEur"], disposal["sellFeesEur"]])
            lots = []
            for number, parcel in enumerate(disposal["parcels"], start=1):
                if parcel.get("platform"):
                    platforms.add(parcel["platform"])
                lots.append(
                    {
                        "number": number,
                        "quantity": parcel["quantity"],
                        "lotQuantity": parcel.get("lotQuantity"),
                        "date": _es_date(parcel["acquisitionDate"]),
                        "price": parcel.get("price"),
                        "currency": parcel.get("currency"),
                        "fx": _fx_text(
                            parcel.get("fxRate"), parcel.get("fxSource"), None, parcel.get("currency")
                        ),
                        "valueEur": parcel["acquisitionValueEur"],
                        "feesEur": parcel["buyFeesEur"],
                        "source": parcel["source"],
                        "platform": parcel.get("platform"),
                        "missing": parcel["source"] == "uncovered",
                    }
                )
            sales.append(
                {
                    "asset": row["symbol"],
                    "name": row["name"],
                    "isin": row["isin"],
                    "platform": disposal["platform"],
                    "date": _es_date(disposal["date"]),
                    "quantity": disposal["quantity"],
                    "price": disposal.get("price"),
                    "currency": disposal.get("currency"),
                    "fx": _fx_text(
                        disposal.get("fxRate"),
                        disposal.get("fxSource"),
                        disposal.get("fxDate"),
                        disposal.get("currency"),
                    ),
                    "grossEur": gross,
                    "sellFeesEur": disposal["sellFeesEur"],
                    "disposalValueEur": disposal["disposalValueEur"],
                    "lots": lots,
                    "acquisitionValueEur": disposal["acquisitionValueEur"],
                    "resultEur": disposal["resultEur"],
                    "deferredLossEur": disposal["deferredLossEur"],
                    "deferredBecause": [_es_date(d) for d in disposal.get("deferredBecause", [])],
                    "computableResultEur": disposal["computableResultEur"],
                    "incomplete": disposal.get("incomplete", False),
                }
            )
    sources = sorted({SOURCE_FILES.get(p, SOURCE_FILES["manual"]) for p in platforms})
    return {"rules": ANNEX_RULES, "sources": sources, "sales": sales}


def build_renta_pack(year_data: dict) -> dict:
    year = year_data["year"]
    sections = []

    for section_id, asset_types in (
        ("acciones-cotizadas", ("stock", "etf")),
        ("monedas-virtuales", ("crypto",)),
    ):
        rows = [
            r for r in year_data["capitalGains"]["byAsset"] if r["assetType"] in asset_types
        ]
        if not rows:
            continue
        entries: list[dict] = []
        parcel_entries: list[dict] = []
        for row in rows:
            per_sale, per_parcel = _gain_entries(row)
            entries.extend(per_sale)
            parcel_entries.extend(per_parcel)
        blocked = any(e.get("incomplete") for e in entries)
        merged_note = (
            " Una entrada con `merged` incluye además todas las recompensas de staking que "
            "financiaron esa venta; el detalle recompensa a recompensa está en `taxes.json`."
            if any(e.get("merged") for e in entries)
            else ""
        )
        sections.append(
            {
                "id": section_id,
                "rentaLabel": SECTIONS[section_id],
                "casillaHint": "verificar en pantalla",
                "entries": entries,
                "parcelEntries": parcel_entries,
                "blocked": blocked,
                "note": (
                    (BLOCKED_NOTE if blocked else "")
                    + "Rellena una entrada por cada línea de `entries`: una por venta, que es la "
                    "unidad que se declara (una transmisión, una ganancia o pérdida). Cuando la "
                    "venta se cubrió con varias compras, la fecha de adquisición es 'varias' y "
                    "las tienes en `acquisitionDates`: hay que marcar en la pantalla 'valores "
                    "adquiridos en distintas fechas'. Si la pantalla no lo ofrece y exige una "
                    "fecha de adquisición por línea, usa `parcelEntries`, que desglosa cada "
                    "venta por lote FIFO; los totales son los mismos. Las entradas con "
                    "`deferred: true` SÍ se declaran: la transmisión existió; lo que cambia es "
                    "que hay que marcar la recompra de valores homogéneos para que la pérdida no "
                    "se integre este ejercicio."
                    + merged_note
                    + FEE_NOTE[section_id]
                ),
            }
        )

    # The other half of the two-month rule: losses an earlier year had to leave
    # out, now that the shares that blocked them are gone. They belong to no
    # single transmission of this year, so they get their own section instead
    # of being folded into a sale's result and quietly changing its figures.
    released = [
        (row, disposal)
        for row in year_data["capitalGains"]["byAsset"]
        for disposal in row["disposals"]
        if disposal["reintegratedLossEur"]
    ]
    if released:
        entries = []
        for row, disposal in released:
            for origin in disposal["reintegratedFrom"]:
                entries.append(
                    {
                        "id": f"{row['symbol']}-reintegro-{origin['date']}-{disposal['date']}",
                        "asset": row["symbol"],
                        "isin": row["isin"],
                        "resultEur": origin["amountEur"],
                        "fields": [
                            {"label": "Entidad emisora", "value": row["name"], "format": "text"},
                            {
                                "label": "Nº de valores transmitidos",
                                "value": origin["quantity"],
                                "format": "number",
                            },
                            {
                                "label": "Fecha de la pérdida bloqueada",
                                "value": _es_date(origin["date"]),
                                "format": "date",
                            },
                            {
                                "label": "Fecha en que se transmiten los valores recomprados",
                                "value": _es_date(disposal["date"]),
                                "format": "date",
                            },
                            {
                                "label": "Pérdida que se integra",
                                "value": origin["amountEur"],
                                "format": "eur",
                            },
                        ],
                    }
                )
        sections.append(
            {
                "id": "perdidas-reintegradas",
                "rentaLabel": SECTIONS["perdidas-reintegradas"],
                "casillaHint": "verificar en pantalla",
                "entries": entries,
                "note": (
                    "⚠️ Este apartado es el que más hay que mirar en pantalla: es la pérdida que "
                    f"en su día marcaste como no computable por recompra y que en {year} ya sí se "
                    "integra, porque has vendido los valores que la bloqueaban. No es el "
                    "resultado de ninguna venta de este año — va aparte, y conviene comprobar "
                    "contra la declaración del ejercicio en que se bloqueó."
                ),
            }
        )

    dividends = year_data["investmentIncome"]["dividends"]
    savings = year_data.get("savings") or {}
    if dividends["grossEur"]:
        excess = dividends.get("excessByCountry") or []
        sections.append(
            {
                "id": "dividendos",
                "rentaLabel": SECTIONS["dividendos"],
                "casillaHint": "verificar en pantalla",
                "blocked": bool(dividends.get("incomplete")),
                "entries": [
                    {
                        "id": "dividendos-total",
                        "incomplete": bool(dividends.get("incomplete")),
                        "fields": [
                            {"label": "Ingresos íntegros", "value": dividends["grossEur"], "format": "eur"},
                            {
                                "label": "Retenciones (España)",
                                "value": dividends.get("spanishWithholdingEur", 0.0),
                                "format": "eur",
                            },
                        ],
                    }
                ],
                "note": (
                    (
                        "⛔ NO RELLENES ESTE APARTADO todavía: algún dividendo no tiene tipo de "
                        "cambio oficial del BCE y su importe en euros no es definitivo. "
                        if dividends.get("incomplete")
                        else ""
                    )
                    + "Suelen venir ya cargados en los datos fiscales: contrasta el íntegro. Va "
                    "en bruto; lo retenido fuera no se resta aquí, va en la deducción por doble "
                    "imposición internacional."
                    + (
                        " Lo retenido por encima del convenio no se deduce en España: "
                        + "; ".join(
                            f"{x['country']} {_es(x['amountEur'])} €" for x in excess
                        )
                        + ". Eso se reclama a cada país."
                        if excess
                        else ""
                    )
                ),
            }
        )
        if dividends.get("foreignGrossEur") and dividends.get("creditableEur"):
            deduction = savings.get("doubleTaxDeductionEur")
            sections.append(
                {
                    "id": "doble-imposicion",
                    "rentaLabel": SECTIONS["doble-imposicion"],
                    "casillaHint": "verificar en pantalla",
                    "blocked": bool(dividends.get("incomplete")),
                    "entries": [
                        {
                            "id": "doble-imposicion-total",
                            "fields": [
                                {
                                    "label": "Rendimientos netos obtenidos en el extranjero "
                                    "incluidos en la base liquidable",
                                    "value": savings.get("foreignIncomeInBaseEur", dividends["foreignGrossEur"]),
                                    "format": "eur",
                                },
                                {
                                    "label": "Impuesto satisfecho en el extranjero",
                                    "value": dividends["creditableEur"],
                                    "format": "eur",
                                },
                                {
                                    "label": "Deducción por doble imposición internacional",
                                    "value": deduction,
                                    "format": "eur",
                                },
                            ],
                        }
                    ],
                    "note": (
                        "El impuesto extranjero que se teclea es el DEDUCIBLE: lo retenido, con "
                        "el tope del convenio de cada país. No es lo retenido sin más. La "
                        "deducción además no puede pasar de rendimientos extranjeros × tipo "
                        f"medio del ahorro ({_es(savings['averageRatePct']) if savings.get('averageRatePct') is not None else '—'} %); "
                        "Renta Web la calcula sola, la cifra de aquí es para comprobarla."
                    ),
                }
            )

    staking = year_data["investmentIncome"]["staking"]
    if staking["grossEur"]:
        sections.append(
            {
                "id": "staking",
                "rentaLabel": SECTIONS["staking"],
                "casillaHint": "verificar en pantalla",
                "entries": [
                    {
                        "id": "staking-total",
                        "fields": [
                            {"label": "Ingresos íntegros", "value": staking["grossEur"], "format": "eur"},
                            {"label": "Retenciones", "value": 0.0, "format": "eur"},
                        ],
                    }
                ],
                "note": (
                    "Recompensas de staking valoradas en euros el día de cobro. No las carga "
                    "Hacienda: hay que añadirlas a mano."
                ),
            }
        )

    applied = savings.get("pendingApplied") or []
    if applied:
        entries = []
        for item in applied:
            block_name = (
                "ganancias y pérdidas patrimoniales"
                if item["block"] == "gp"
                else "rendimientos del capital mobiliario"
            )
            fields = []
            for cross, amount in ((False, item["ownBlockEur"]), (True, item["otherBlockEur"])):
                if not amount:
                    continue
                fields.append(
                    {
                        "label": (
                            f"Saldo negativo de {block_name} de {item['originYear']} compensado "
                            + ("con el otro bloque (límite del 25 %)" if cross else "con su propio bloque")
                        ),
                        "value": amount,
                        "format": "eur",
                        # What renta_casillas needs to find the box by label.
                        "block": item["block"],
                        "origin": item["originYear"],
                        "cross": cross,
                    }
                )
            entries.append(
                {"id": f"saldo-{item['block']}-{item['originYear']}", "fields": fields}
            )
        sections.append(
            {
                "id": "saldos-pendientes",
                "rentaLabel": SECTIONS["saldos-pendientes"],
                "casillaHint": "verificar en pantalla",
                "entries": entries,
                "note": (
                    "Lo que se aplica este año de los saldos negativos de años anteriores, el "
                    "más antiguo primero. Comprueba que el saldo de partida coincide con el de "
                    "la declaración del año pasado (apartado de saldos pendientes de "
                    "compensación)."
                ),
            }
        )

    for section in sections:
        section.setdefault("blocked", False)

    return {
        "fiscalYear": int(year),
        "filedIn": int(year_data["filedIn"]),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "yearIsOpen": year_data["open"],
        "disclaimer": (
            "Cifras calculadas por plout taxes a partir de los extractos del usuario. "
            "No son asesoramiento fiscal: revísalas antes de presentar nada."
        ),
        "sections": sections,
        "blockedSections": [x["id"] for x in sections if x["blocked"]],
        "savings": savings,
        "annex": build_annex(year_data),
        "checks": year_data["checks"],
        "warnings": year_data["warnings"],
    }


def render_renta_markdown(pack: dict) -> str:
    """The brief an agent reads before touching Renta Web."""
    year = pack["fiscalYear"]
    lines = [
        f"# Renta {year} — guion de relleno",
        "",
        f"Ejercicio **{year}**, que se presenta en **{pack['filedIn']}**. "
        f"Generado el {pack['generatedAt'][:10]} por `plout taxes`.",
        "",
        "## Reglas para quien rellene el formulario",
        "",
        f"1. **No inventes ninguna cifra.** Todo lo que hay que teclear está en "
        f"`renta-{year}.json`; si un dato no está ahí, para y pregunta.",
        "2. **No envíes la declaración.** Rellena, guarda y deja el borrador. Presentar lo hace "
        "siempre la persona.",
        "3. **Si un rótulo de pantalla no coincide** con el de aquí, para y pregunta: los números "
        "de casilla cambian cada ejercicio y los títulos de apartado pueden reformularse.",
        "4. Importes en euros con dos decimales; fechas en `dd/mm/aaaa`.",
        "5. **Una entrada por venta**, que es lo que se declara: una transmisión, una ganancia o "
        "pérdida. Donde pone «varias» en la fecha de adquisición hay que marcar en la pantalla "
        "«valores adquiridos en distintas fechas»; si esa pantalla no lo ofrece, al final de "
        "este documento está el desglose por lote para rellenar línea a línea.",
        "6. **Las comisiones no se teclean aparte** en la ventana normal de Renta Web: la de "
        "compra ya va sumada en el valor de adquisición y la de venta ya va restada del valor "
        "de transmisión. Se desglosan bajo cada entrada por dos motivos: para poder cuadrarlas "
        "con el extracto, y porque el programa *Cartera de Valores* (solo acciones) sí pide "
        "«Gastos de la operación» en cada compra y en cada venta, con el importe en bruto.",
        "",
    ]

    if pack["yearIsOpen"]:
        lines += [
            f"> ⚠️ El ejercicio {year} sigue abierto. Esto es un seguimiento, no la declaración "
            f"definitiva: si vendes algo más antes del 31/12 habrá que regenerarlo.",
            "",
        ]

    blocked = [s for s in pack["sections"] if s.get("blocked")]
    if blocked:
        lines += [
            "## ⛔ Apartados bloqueados — no los rellenes todavía",
            "",
            "Faltan datos para dar estas cifras por buenas (una compra que no se conoce o un "
            "tipo de cambio oficial que no se pudo obtener). Complétalos y vuelve a generar "
            "el paquete antes de tocar estos apartados:",
            "",
        ]
        lines += [f"- {s['rentaLabel']}" for s in blocked] + [""]

    savings = pack.get("savings") or {}
    if savings:
        lines += ["## Base del ahorro (estimación)", ""]
        lines += [
            "| Concepto | Importe |",
            "|---|---:|",
            f"| Saldo de ganancias y pérdidas del año | {_es(savings['gainsBlockEur'])} € |",
            f"| Saldo de rendimientos del capital mobiliario | {_es(savings['incomeBlockEur'])} € |",
        ]
        if savings["crossOffsetEur"]:
            lines.append(
                f"| Compensado entre bloques (máx. 25 %) | {_es(savings['crossOffsetEur'])} € |"
            )
        for item in savings["pendingApplied"]:
            name = "G/P" if item["block"] == "gp" else "RCM"
            lines.append(
                f"| Saldo negativo {name} de {item['originYear']} aplicado | "
                f"{_es(item['ownBlockEur'] + item['otherBlockEur'])} € |"
            )
        lines.append(f"| **Base imponible del ahorro** | **{_es(savings['baseEur'])} €** |")
        if savings.get("cuotaEur") is not None:
            lines += [
                f"| Cuota (escala del ahorro de {pack['fiscalYear']}) | {_es(savings['cuotaEur'])} € |",
                f"| Tipo medio | {_es(savings['averageRatePct'])} % |",
                f"| Deducción por doble imposición | {_es(savings['doubleTaxDeductionEur'])} € |",
                f"| **Cuota estimada** | **{_es(savings['cuotaAfterDeductionEur'])} €** |",
            ]
        else:
            lines.append(f"| Cuota | sin reglas para {pack['fiscalYear']} |")
        lines.append("")
        for item in savings["pendingCarried"]:
            name = "ganancias y pérdidas" if item["block"] == "gp" else "rendimientos del capital mobiliario"
            lines.append(
                f"- Queda pendiente para años siguientes: {_es(item['amountEur'])} € de "
                f"{name} de {item['originYear']}."
            )
        if savings["pendingCarried"]:
            lines.append("")

    lines += ["## Apartados", ""]
    for section in pack["sections"]:
        lines += [
            f"### {section['rentaLabel']}",
            "",
        ]
        if section.get("blocked"):
            lines += [
                "> ⛔ **No rellenes este apartado hasta añadir lo que falta.** Las entradas "
                "marcadas como incompletas dicen qué es.",
                "",
            ]
        lines += [
            f"_Casilla: {section['casillaHint']}._ {section.get('note', '')}".strip(),
            "",
        ]
        for entry in section["entries"]:
            title = entry.get("asset") or entry["id"]
            lines.append(f"**{title}** — entrada `{entry['id']}`")
            lines.append("")
            has_boxes = any(f.get("casilla") for f in entry["fields"])
            lines.append("| Campo | Casilla | Valor |" if has_boxes else "| Campo | Valor |")
            lines.append("|---|---|---|" if has_boxes else "|---|---|")
            for f in entry["fields"]:
                value = f["value"]
                if f["format"] == "eur" and isinstance(value, (int, float)):
                    shown = f"{_es(value)} €"
                elif f["format"] == "number" and isinstance(value, (int, float)):
                    shown = f"{value:g}"
                elif f["format"] == "missing":
                    shown = f"⛔ **{value}**"
                else:
                    shown = "—" if value is None else str(value)
                if has_boxes:
                    lines.append(f"| {f['label']} | {f.get('casilla') or '—'} | {shown} |")
                else:
                    lines.append(f"| {f['label']} | {shown} |")
            if entry.get("resultEur") is not None:
                gap = " |" if has_boxes else ""
                lines.append(f"| _(resultado, no se teclea)_ |{gap} _{_es(entry['resultEur'])} €_ |")
            lines.append("")
            days = entry.get("acquisitionDates") or []
            if len(days) > 1:
                lines.append(
                    f"Cubierta con **{len(days)} adquisiciones**, de {days[0]} a {days[-1]}. "
                    "Marca en la pantalla «valores adquiridos en distintas fechas»."
                )
                lines.append("")
            merged = entry.get("merged")
            if merged:
                lines.append(
                    f"Entre ellas van **{merged['count']} recompensas de staking** cobradas "
                    f"entre el {merged['fromDate']} y el {merged['toDate']}, contadas como un "
                    "solo lote: son adquisiciones de céntimos y desglosarlas no cambia ninguna "
                    "cifra. El detalle día a día está en `taxes.json`."
                )
                lines.append("")
            fees = entry.get("feesEur")
            if fees is not None:
                lines.append(
                    f"Comisiones — compra **{_es(fees['buyEur'])} €** (ya sumada en el valor de "
                    f"adquisición) · venta **{_es(fees['sellEur'])} €** (ya restada del valor de "
                    "transmisión). No se teclean en esta pantalla."
                )
                if section["id"] == "acciones-cotizadas":
                    lines.append(
                        "Por *Cartera de Valores*, que sí las pide: alta AD → importe "
                        f"{_es(fees['grossAcquisitionEur'])} €, gastos de la operación "
                        f"{_es(fees['buyEur'])} €; alta TR → importe "
                        f"{_es(fees['grossDisposalEur'])} €, gastos de la operación "
                        f"{_es(fees['sellEur'])} €."
                    )
                lines.append("")
            if entry.get("deferred"):
                if entry.get("partiallyDeferred"):
                    lines.append(
                        f"⚠️ De esta línea, **{_es(entry['deferredLossEur'])} €** de pérdida "
                        "**no son computables** este ejercicio (recompra parcial de valores "
                        "homogéneos dentro de los dos meses); el resto sí. Marca la recompra "
                        "en la pantalla."
                    )
                else:
                    lines.append(
                        "⚠️ Pérdida **no computable** este ejercicio: marca en la pantalla la "
                        "recompra de valores homogéneos dentro de los dos meses."
                    )
                lines.append("")

    # The fallback route, compact: only needed if the screen refuses a line
    # with several acquisition dates, and then what is wanted is the figures,
    # not a table per parcel.
    split = [
        s for s in pack["sections"]
        if any(e.get("parcelCount", 1) > 1 for e in s["entries"]) and s.get("parcelEntries")
    ]
    if split:
        lines += [
            "## Desglose por lote — solo si la pantalla exige una fecha de adquisición por línea",
            "",
            "No hace falta para rellenar: es la misma venta partida por lotes FIFO, y los totales "
            "coinciden con los de arriba. Úsalo solo si la pantalla no admite «valores adquiridos "
            "en distintas fechas», y entonces teclea estas líneas **en lugar de** la entrada de "
            "la venta, nunca además de ella.",
            "",
        ]
        for section in split:
            lines += [f"### {section['rentaLabel']}", ""]
            lines.append("| Entrada | Activo | Adquirido | Valor adq. | Transmitido | Valor trans. | Resultado |")
            lines.append("|---|---|---|---:|---|---:|---:|")
            for entry in section["parcelEntries"]:
                f = {x["label"]: x["value"] for x in entry["fields"]}
                flag = " ⚠️" if entry.get("deferred") else ""
                acquisition = f["Valor de adquisición"]
                acquisition = (
                    f"{_es(acquisition)} €" if isinstance(acquisition, (int, float)) else f"⛔ {acquisition}"
                )
                lines.append(
                    f"| `{entry['id']}`{flag} | {entry['asset']} | "
                    f"{f['Fecha de adquisición'] or '—'} | {acquisition} | "
                    f"{f['Fecha de transmisión']} | {_es(f['Valor de transmisión'])} € | "
                    f"{_es(entry['resultEur'])} € |"
                )
            lines.append("")
            if any(e.get("deferred") for e in section["parcelEntries"]):
                lines += [
                    "⚠️ = línea con pérdida diferida por recompra de valores homogéneos: la "
                    "parte no computable de cada una está en `deferredLossEur` de "
                    "`parcelEntries` (repartida a prorrata entre los lotes de la venta).",
                    "",
                ]

    lines += render_annex(pack.get("annex"))

    if pack["warnings"]:
        lines += ["## Avisos — resuélvelos antes de presentar", ""]
        lines += [f"- {w['message']}" for w in pack["warnings"]] + [""]
    if pack["checks"]:
        lines += ["## Comprobaciones", ""]
        lines += [f"- {c['message']}" for c in pack["checks"]] + [""]

    lines += ["---", "", pack["disclaimer"], ""]
    return "\n".join(lines)



def render_annex(annex: dict | None) -> list[str]:
    """Anexo de justificación: one table per sale, for a requerimiento."""
    if not annex or not annex["sales"]:
        return []
    lines = [
        "## Anexo — justificación de cada venta",
        "",
        "No hace falta para rellenar la declaración. Sirve para contestar un requerimiento: "
        "los brókers extranjeros no informan a Hacienda del coste de compra, así que "
        "justificarlo es cosa del contribuyente.",
        "",
        "**Normativa aplicada:**",
        "",
    ]
    lines += [f"- {rule}" for rule in annex["rules"]]
    lines += ["", "**Datos de origen:** " + "; ".join(annex["sources"]) + ".", ""]
    lines += [
        "_Los importes de cada lote se muestran redondeados al céntimo; el valor de "
        "adquisición de la venta se redondea una sola vez sobre la suma exacta, así que la "
        "suma de las líneas puede diferir en un céntimo del total._",
        "",
    ]
    for sale in annex["sales"]:
        title = f"### {sale['name']} ({sale['asset']}) — venta del {sale['date']}"
        lines += [title + (" ⛔ incompleta" if sale["incomplete"] else ""), ""]
        if sale["isin"]:
            lines += [f"ISIN {sale['isin']} · {sale['platform']}", ""]
        lines += ["| Paso | Detalle | Importe |", "|---|---|---:|"]
        if sale["price"] is not None and sale["currency"]:
            detail = f"{_es_num(sale['quantity'])} × {_es_num(sale['price'])} {sale['currency']}"
            if sale["fx"]:
                detail += f" → {sale['fx']}"
            lines.append(f"| Importe de la venta | {detail} | {_es(sale['grossEur'])} € |")
        else:
            lines.append(f"| Importe de la venta | {_es_num(sale['quantity'])} unidades | {_es(sale['grossEur'])} € |")
        lines.append(f"| − Comisiones de venta | | {_es(sale['sellFeesEur'])} € |")
        lines.append(f"| **Valor de transmisión** | | **{_es(sale['disposalValueEur'])} €** |")
        for lot in sale["lots"]:
            if lot["missing"]:
                lines.append(
                    f"| Lote {lot['number']} | ⛔ {_es_num(lot['quantity'])} sin compra conocida | — |"
                )
                continue
            detail = _es_num(lot["quantity"])
            if lot["lotQuantity"] and lot["lotQuantity"] != lot["quantity"]:
                detail += f" de {_es_num(lot['lotQuantity'])}"
            detail += f" compradas el {lot['date']}"
            if lot["price"] is not None and lot["currency"]:
                detail += f" a {_es_num(lot['price'])} {lot['currency']}"
            if lot["fx"]:
                detail += f" ({lot['fx']})"
            if lot["feesEur"]:
                detail += f", con {_es(lot['feesEur'])} € de comisiones proporcionales"
            if lot["platform"]:
                detail += f" · {lot['platform']}"
            lines.append(f"| Lote {lot['number']} | {detail} | {_es(lot['valueEur'])} € |")
        lines.append(f"| **Valor de adquisición** | | **{_es(sale['acquisitionValueEur'])} €** |")
        lines.append(f"| **Resultado** | | **{_es(sale['resultEur'])} €** |")
        if sale["deferredLossEur"]:
            lines.append(
                f"| Pérdida aplazada por recompra | recompra el {', '.join(sale['deferredBecause'])} "
                f"| {_es(sale['deferredLossEur'])} € |"
            )
            lines.append(f"| Computable este año | | {_es(sale['computableResultEur'])} € |")
        lines.append("")
    return lines


def previous_degiro_trades(out_dir: Path) -> list[dict]:
    """The DEGIRO half of the ledger from the last full run, for --no-degiro."""
    path = out_dir / "taxes.json"
    if not path.exists():
        return []
    try:
        trades = json.loads(path.read_text()).get("trades") or []
    except Exception as exc:
        print(f"  warning: could not reread taxes.json ({exc}).", file=sys.stderr)
        return []
    return [t for t in trades if t.get("platform") == "DEGIRO"]


# ── main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--from",
        dest="from_date",
        default="2015-01-01",
        help="Earliest date to scan (YYYY-MM-DD, default 2015-01-01)",
    )
    parser.add_argument(
        "--year",
        help="Fiscal year for the Renta filing pack (default: the most recent year with data)",
    )
    parser.add_argument(
        "--bybit",
        default=str(BYBIT_FILE),
        help=f"Bybit spot-trade export (default: {BYBIT_FILE.name})",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT),
        help="Directory containing the data/ folder (default: the project root)",
    )
    parser.add_argument(
        "--no-degiro",
        action="store_true",
        help="Skip the DEGIRO login and reuse the trades already in taxes.json",
    )
    parser.add_argument(
        "--no-bybit",
        action="store_true",
        help="Run without the Bybit export, accepting that its lots are missing",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print the normalized trade ledger before aggregating",
    )
    args = parser.parse_args()

    try:
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--from must be YYYY-MM-DD, got {args.from_date!r}")
    to_date = date.today()

    root = Path(args.output_dir).resolve()
    out_dir = root / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    degiro_trades: list[dict] = []
    if args.no_degiro:
        # Reuse the ledger from the last full run instead of dropping it: the
        # DEGIRO login can cost a confirmation on the phone (no TOTP secret and
        # no live shared session), and this flag exists
        # to iterate on the crypto and manual side without paying it again.
        degiro_trades = previous_degiro_trades(out_dir)
        print(f"  DEGIRO: {len(degiro_trades)} operaciones reutilizadas de taxes.json")
    else:
        api, _user_token = connect()
        transactions = fetch_transactions(api, from_date, to_date)
        product_ids = sorted({str(t["productId"]) for t in transactions if t.get("productId")})
        meta = fetch_product_meta(api, product_ids)
        degiro_trades = normalize_degiro(transactions, meta)
        print(f"  DEGIRO: {len(degiro_trades)} operaciones desde {from_date}")

    # A missing acquisition source must never pass quietly. Without these lots
    # the coins they bought look like they were acquired for nothing, so the
    # FIFO declares the whole sale price as gain — a far bigger, entirely
    # plausible-looking tax bill. Refuse to write a return built on that unless
    # it is asked for outright.
    bybit_path = Path(args.bybit).resolve()
    if not bybit_path.exists() and not args.no_bybit:
        sys.exit(
            f"No se encuentra {bybit_path}.\n"
            "Ese fichero aporta los lotes de adquisición de las criptos compradas en "
            "Bybit. Sin él, esas monedas se declararían con valor de adquisición 0 y la "
            "plusvalía saldría inflada.\n"
            "Restaura el export de Bybit, o pasa --no-bybit si de verdad quieres "
            "calcular sin esos lotes."
        )
    bybit_trades = parse_bybit(bybit_path, debug=args.debug)
    if bybit_trades:
        print(f"  Bybit: {len(bybit_trades)} operaciones de {bybit_path.name}")
    elif args.no_bybit:
        print("  Bybit: omitido (--no-bybit) — sus lotes de adquisición no se cuentan")

    manual = load_manual(MANUAL_FILE)
    if manual["disposals"] or manual["rewards"] or manual["lots"]:
        print(
            f"  Manual: {len(manual['disposals'])} ventas, {len(manual['rewards'])} recompensas, "
            f"{len(manual['lots'])} lotes de {MANUAL_FILE.name}"
        )

    if args.debug:
        print("\nLedger normalizado:")
        for trade in sorted(degiro_trades + bybit_trades, key=lambda t: t["date"]):
            print(
                f"  {trade['date']}  {trade['side']:<4} {trade['symbol']:<8} "
                f"{trade['quantity']:>14.8f} @ {trade['price']:>12,.4f} {trade['currency']:<5} "
                f"→ €{trade['totalPlusFeesEur']:>10,.2f}"
            )
        print()

    fx_warnings = check_degiro_fx(degiro_trades)
    if fx_warnings:
        print(f"  DEGIRO: {len(fx_warnings)} operaciones con un cambio desviado del BCE; se usa el BCE")

    ledger = load_revolut_ledger(root)
    assets, warnings, rewards = build_assets(degiro_trades, bybit_trades, manual, ledger)
    # Dividends only carry DEGIRO's product id; the trades know its ISIN.
    isins = {str(t["productId"]): t["isin"] for t in degiro_trades if t.get("isin")}
    years = build_years(
        assets, root, load_dividends(root), rewards, fx_warnings + warnings, manual, isins
    )

    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "DEGIRO + bybit.csv + manual_trades.json",
        "baseCurrency": "EUR",
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "years": years,
        "trades": sorted(degiro_trades + bybit_trades, key=lambda t: t["date"], reverse=True),
    }
    out_file = out_dir / "taxes.json"
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {out_file}")

    for year in years:
        gains = year["capitalGains"]
        flag = " (ejercicio abierto)" if year["open"] else ""
        estimate = gains["estimatedTaxEur"]
        print(
            f"  {year['year']}{flag}: {len(gains['byAsset'])} activos vendidos, "
            f"neto €{gains['netEur']:,.2f}, cuota estimada "
            + (f"€{estimate:,.2f}" if estimate is not None else "sin reglas para ese año")
        )
        for warning in year["warnings"]:
            print(f"    ⚠ {warning['message']}")

    # Filing pack for one year — the thing an agent reads to fill Renta Web.
    if not years:
        return
    target = args.year or years[0]["year"]
    year_data = next((y for y in years if y["year"] == target), None)
    if year_data is None:
        raise SystemExit(f"No hay datos del ejercicio {target}; hay: {', '.join(y['year'] for y in years)}")

    pack = build_renta_pack(year_data)
    renta_dir = out_dir / "renta"
    renta_dir.mkdir(parents=True, exist_ok=True)
    # Box numbers from the AEAT's dictionary of that year, when it is out.
    annotate_pack(pack, load_dictionary(target, renta_dir))
    (renta_dir / f"renta-{target}.json").write_text(
        json.dumps(pack, indent=2, ensure_ascii=False) + "\n"
    )
    (renta_dir / f"renta-{target}.md").write_text(render_renta_markdown(pack))
    entries = sum(len(s["entries"]) for s in pack["sections"])
    print(f"  Paquete Renta {target}: {entries} entradas en {renta_dir}/renta-{target}.{{json,md}}")
    verified = [s["id"] for s in pack["sections"] if s.get("verified")]
    if verified:
        print(f"  Casillas verificadas con el diccionario de la AEAT: {', '.join(verified)}")
    if pack["blockedSections"]:
        print(f"  ⛔ Apartados bloqueados por datos incompletos: {', '.join(pack['blockedSections'])}")


if __name__ == "__main__":
    main()
