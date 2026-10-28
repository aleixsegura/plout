"""
Export ~3 years of daily closes, in EUR, to data/prices.json.

The web app's Correlation page turns these into log returns, a correlation
matrix and the risk figures built on it, so every series is in the investor's
currency: a USD stock's EUR return is what the portfolio actually lives through,
FX included.

Sources, all keyless and best-effort:
  onvista    api.onvista.de, looked up by ISIN — covers every DEGIRO product,
             US stocks and Xetra/Tradegate ETFs alike. Yahoo would be the usual
             pick but rate-limits keyless clients to the point of uselessness.
  coinbase   api.exchange.coinbase.com daily candles for the Revolut crypto,
             quoted straight in EUR.
  ecb        api.frankfurter.dev (ECB reference rates) to convert non-EUR closes.

The ISIN comes from positions.json, or from taxes.json's trades for exports that
predate the field.

A `benchmark` series rides along for the Holdings return chart's "vs index"
line: Vanguard FTSE Global All-Cap Acc (VALL). It only listed in August 2026,
so the years before are Vanguard FTSE All-World Acc (VWCE) — same provider,
same methodology minus the small caps — chain-linked on VALL's first close.

A series that fails to download keeps the previous run's copy, so one flaky
request never empties the page.

Like marketcaps.py, the web app's dev server runs this with
--max-age-hours, so it exits before its first request unless the export is stale
or a holding is new.

Usage:
  plout prices [--output-dir DIR] [--max-age-hours 12]
"""

import argparse
import json
import sys
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from plout.config import PROJECT_ROOT
from plout.exporters.marketcaps import BROWSER_HEADERS, US_EXCHANGES, _read_json

OUT_NAME = "prices.json"

# Three years of returns is the paper's look-back (T); the extra month makes
# sure the window still starts full after holidays and the first return lost.
HISTORY_DAYS = 3 * 365 + 30

# DEGIRO `productType` values with no price series worth correlating.
SKIP_TYPES = {"CURRENCY", "INDEX", "FUTURE"}

ONVISTA_API = "https://api.onvista.de/api/v1"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{pair}/candles"
COINBASE_MAX_CANDLES = 300  # per request, so a 3-year series takes four
ECB_URL = "https://api.frankfurter.dev/v1/{start}..{end}"

# DEGIRO exchange short name → onvista `codeExchange`. Tradegate is where DEGIRO
# books most European trades, but Xetra has the deeper book and the cleaner
# closing auction, so Tradegate products are priced off Xetra (same ISIN).
ONVISTA_EXCHANGE = {
    "XET": "GER", "XETRA": "GER", "TDG": "GER", "GER": "GER", "ETR": "GER",
    "FRA": "FRA", "FSX": "FRA", "STU": "STU", "MUN": "MUN", "BER": "BER",
    "EPA": "PAR", "PAR": "PAR", "EAM": "AMS", "AMS": "AMS", "EBR": "BRU",
    "BRU": "BRU", "MIL": "MIL", "BIT": "MIL", "MAD": "MAD", "BME": "MAD",
    "LSE": "LSE", "LON": "LSE", "SWX": "SWX", "EBS": "SWX", "VIE": "VIE",
}

# The yardstick for the return chart, and the proxy that stands in for it
# before it listed. Both are accumulating, so their closes are total return.
BENCHMARK = {"symbol": "VALL", "name": "Vanguard FTSE Global All-Cap UCITS ETF (Acc)", "isin": "IE000VAHT5T0"}
BENCHMARK_PROXY = {"symbol": "VWCE", "name": "Vanguard FTSE All-World UCITS ETF (Acc)", "isin": "IE00BK5BQT80"}

# Quote currencies some venues use for minor units; closes are divided by 100.
MINOR_UNITS = {"GBX": "GBP", "GBp": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


@dataclass
class Target:
    """One holding to price, under the symbol the web app calls it by."""

    symbol: str
    source: str  # "onvista" | "coinbase"
    isin: str | None = None
    exchange: str | None = None
    currency: str | None = None


# ── Symbol collection ──────────────────────────────────────────────────────────

def collect_targets(out_dir: Path) -> list[Target]:
    """Every holding in positions.json and revolut.json that has a price."""
    targets: list[Target] = []

    # Older positions.json files carry no ISIN; the tax export records it per
    # trade, keyed by the same DEGIRO product id.
    taxes = _read_json(out_dir / "taxes.json") or {}
    isin_by_product = {
        str(t["productId"]): t["isin"]
        for t in taxes.get("trades", [])
        if t.get("productId") and t.get("isin")
    }

    positions = _read_json(out_dir / "positions.json") or {}
    for position in positions.get("positions", []):
        symbol = position.get("symbol")
        if not symbol:
            continue
        if (position.get("productType") or "").upper() in SKIP_TYPES:
            continue
        targets.append(
            Target(
                symbol=symbol,
                source="onvista",
                isin=position.get("isin") or isin_by_product.get(str(position.get("id"))),
                exchange=(position.get("exchange") or "").upper() or None,
                currency=position.get("currency"),
            )
        )

    revolut = _read_json(out_dir / "revolut.json") or {}
    for position in revolut.get("positions", []):
        symbol = position.get("symbol")
        if symbol:
            # The Revolut exporter only ever captures crypto.
            targets.append(Target(symbol=symbol, source="coinbase"))

    return targets


# ── onvista ────────────────────────────────────────────────────────────────────

def pick_notation(quotes: list[dict], exchange: str | None, currency: str | None) -> dict | None:
    """The listing to price a DEGIRO product off, from an onvista quoteList.

    A US exchange means the home listing in USD, not one of the dozen thinly
    traded German venues quoting it in EUR; a European one maps to its onvista
    venue. Within the candidates the product's own currency wins.
    """
    def market(q: dict) -> dict:
        return q.get("market") or {}

    if exchange in US_EXCHANGES:
        candidates = [q for q in quotes if market(q).get("isoCountry") == "US"]
    else:
        code = ONVISTA_EXCHANGE.get(exchange or "")
        candidates = [q for q in quotes if code and market(q).get("codeExchange") == code]
    if not candidates:
        return None
    same_currency = [q for q in candidates if q.get("isoCurrency") == currency]
    return (same_currency or candidates)[0]


def resolve_onvista(session: requests.Session, target: Target) -> dict | None:
    """Instrument + notation for an ISIN: {entityType, entityValue, idNotation, market}."""
    resp = session.get(
        f"{ONVISTA_API}/instruments/query", params={"searchValue": target.isin}, timeout=20
    )
    resp.raise_for_status()
    match = next(
        (i for i in resp.json().get("list", []) if i.get("isin") == target.isin), None
    )
    if match is None:
        return None
    entity_type, entity_value = match["entityType"], match["entityValue"]

    resp = session.get(
        f"{ONVISTA_API}/instruments/{entity_type}/{entity_value}/snapshot", timeout=20
    )
    resp.raise_for_status()
    snapshot = resp.json()
    quote = pick_notation(
        snapshot.get("quoteList", {}).get("list", []), target.exchange, target.currency
    ) or snapshot.get("quote")  # onvista's own default venue as the last resort
    if not quote:
        return None
    market = quote.get("market") or {}
    notation = market.get("idNotation") or quote.get("idNotation")
    if not notation:
        return None
    return {
        "entityType": entity_type,
        "entityValue": entity_value,
        "idNotation": notation,
        "market": market.get("name"),
    }


def parse_onvista_eod(payload: dict) -> tuple[list[str], list[float], str | None]:
    """(dates, closes, currency) from an eod_history response.

    The response is columnar: `datetimeLast` holds one timestamp per session
    (noon UTC, so the UTC date is the trading date) and `last` the close.
    """
    stamps = payload.get("datetimeLast") or []
    closes = payload.get("last") or []
    by_date: dict[str, float] = {}
    for stamp, close in zip(stamps, closes):
        if stamp is None or close is None:
            continue
        by_date[datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()] = float(close)
    dates = sorted(by_date)
    return dates, [by_date[d] for d in dates], payload.get("isoCurrency")


def fetch_onvista(session: requests.Session, ref: dict, start: date) -> tuple[list[str], list[float], str | None]:
    resp = session.get(
        f"{ONVISTA_API}/instruments/{ref['entityType']}/{ref['entityValue']}/eod_history",
        params={"idNotation": ref["idNotation"], "range": "Y5", "startDate": start.isoformat()},
        timeout=30,
    )
    resp.raise_for_status()
    return parse_onvista_eod(resp.json())


# ── Coinbase ───────────────────────────────────────────────────────────────────

def parse_coinbase(candles: list[list]) -> dict[str, float]:
    """date → close from Coinbase candles: [time, low, high, open, close, volume]."""
    out = {}
    for candle in candles:
        if len(candle) < 5 or candle[4] is None:
            continue
        out[datetime.fromtimestamp(candle[0], timezone.utc).date().isoformat()] = float(candle[4])
    return out


def fetch_coinbase(session: requests.Session, symbol: str, start: date, end: date) -> tuple[list[str], list[float]]:
    """Daily EUR closes, in windows of COINBASE_MAX_CANDLES days."""
    by_date: dict[str, float] = {}
    window_start = start
    while window_start <= end:
        window_end = min(end, window_start + timedelta(days=COINBASE_MAX_CANDLES - 1))
        resp = session.get(
            COINBASE_CANDLES_URL.format(pair=f"{symbol.upper()}-EUR"),
            params={
                "granularity": 86400,
                "start": window_start.isoformat(),
                "end": (window_end + timedelta(days=1)).isoformat(),
            },
            timeout=20,
        )
        resp.raise_for_status()
        by_date.update(parse_coinbase(resp.json()))
        window_start = window_end + timedelta(days=1)
    dates = sorted(by_date)
    return dates, [by_date[d] for d in dates]


# ── FX ─────────────────────────────────────────────────────────────────────────

def fetch_ecb_rates(currencies: set[str], start: date, end: date) -> dict[str, dict[str, float]]:
    """currency → {date: units per EUR}, from the ECB reference rates."""
    if not currencies:
        return {}
    resp = requests.get(
        ECB_URL.format(start=start.isoformat(), end=end.isoformat()),
        params={"base": "EUR", "symbols": ",".join(sorted(currencies))},
        timeout=30,
    )
    resp.raise_for_status()
    out: dict[str, dict[str, float]] = {c: {} for c in currencies}
    for day, rates in resp.json().get("rates", {}).items():
        for currency, rate in rates.items():
            out.setdefault(currency, {})[day] = float(rate)
    return out


def to_eur(
    dates: list[str], closes: list[float], currency: str | None, fx: dict[str, dict[str, float]]
) -> list[float] | None:
    """Closes converted to EUR at each day's ECB rate.

    A day without a fixing (ECB holidays, a US session on a TARGET holiday)
    takes the latest earlier rate; days before the first fixing take the first.
    None when the currency has no rates at all.
    """
    divisor = 1.0
    if currency in MINOR_UNITS:
        currency, divisor = MINOR_UNITS[currency], 100.0
    if not currency or currency == "EUR":
        return [round(c / divisor, 6) for c in closes]
    rates = fx.get(currency)
    if not rates:
        return None
    fix_dates = sorted(rates)
    out = []
    for day, close in zip(dates, closes):
        i = bisect_right(fix_dates, day) - 1
        rate = rates[fix_dates[max(i, 0)]]
        out.append(round(close / divisor / rate, 6))
    return out


# ── Export ─────────────────────────────────────────────────────────────────────

def fetch_series(session: requests.Session, target: Target, previous: dict | None, start: date, end: date) -> dict | None:
    """One holding's raw series: {dates, closes, currency, market?, ref?}."""
    if target.source == "coinbase":
        dates, closes = fetch_coinbase(session, target.symbol, start, end)
        return {"dates": dates, "closes": closes, "currency": "EUR", "market": "Coinbase"}

    if not target.isin:
        return None
    # The ISIN → notation lookup is two requests and never changes, so it's
    # carried over from the previous run while the ISIN is the same.
    ref = (previous or {}).get("ref")
    if not ref or (previous or {}).get("isin") != target.isin:
        ref = resolve_onvista(session, target)
        if ref is None:
            return None
    dates, closes, currency = fetch_onvista(session, ref, start)
    return {
        "dates": dates,
        "closes": closes,
        "currency": currency or target.currency,
        "market": ref.get("market"),
        "ref": ref,
    }


def build_series(targets: list[Target], previous: dict) -> tuple[dict, list[str]]:
    """symbol → series entry, plus the symbols nothing could be priced for."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=HISTORY_DAYS)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old_series = previous.get("series") or {}

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    raw: dict[str, dict] = {}
    for target in targets:
        try:
            series = fetch_series(session, target, old_series.get(target.symbol), start, end)
        except Exception as exc:
            print(f"  warning: {target.symbol}: {target.source} request failed ({exc})", file=sys.stderr)
            series = None
        if series and series["dates"]:
            raw[target.symbol] = series

    foreign = {
        MINOR_UNITS.get(s["currency"], s["currency"])
        for s in raw.values()
        if s["currency"] and MINOR_UNITS.get(s["currency"], s["currency"]) != "EUR"
    }
    try:
        fx = fetch_ecb_rates(foreign, start, end)
    except Exception as exc:
        print(f"  warning: ECB rates request failed ({exc})", file=sys.stderr)
        fx = {}

    out: dict[str, dict] = {}
    unresolved: list[str] = []
    for target in targets:
        series = raw.get(target.symbol)
        closes_eur = to_eur(series["dates"], series["closes"], series["currency"], fx) if series else None
        if series and closes_eur:
            entry = {
                "source": target.source,
                "isin": target.isin,
                "market": series.get("market"),
                "currency": series["currency"],
                "at": now,
                "dates": series["dates"],
                "closesEur": closes_eur,
            }
            if series.get("ref"):
                entry["ref"] = series["ref"]
            out[target.symbol] = {k: v for k, v in entry.items() if v is not None}
        elif target.symbol in old_series:
            out[target.symbol] = old_series[target.symbol]
        else:
            unresolved.append(target.symbol)
    return dict(sorted(out.items())), unresolved


def splice(
    proxy_dates: list[str], proxy_closes: list[float], dates: list[str], closes: list[float]
) -> tuple[list[str], list[float]]:
    """One continuous series: the proxy until the target's first close, then the
    target's closes rescaled so its first one equals the proxy's that day (or
    the proxy's last before it). Returns carry over exactly; levels are the
    proxy's."""
    if not dates:
        return proxy_dates, proxy_closes
    start = dates[0]
    i = bisect_right(proxy_dates, start)
    if i == 0:
        return dates, closes
    scale = proxy_closes[i - 1] / closes[0]
    # Proxy closes strictly before the target's first date; from there on, the target.
    head = i - 1 if proxy_dates[i - 1] == start else i
    return proxy_dates[:head] + dates, proxy_closes[:head] + [round(c * scale, 4) for c in closes]


def build_benchmark(previous: dict | None) -> dict | None:
    """The spliced benchmark series in EUR, or the previous run's on failure."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=HISTORY_DAYS)
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    old_refs = (previous or {}).get("refs") or {}
    raw = {}
    for spec in (BENCHMARK, BENCHMARK_PROXY):
        target = Target(symbol=spec["symbol"], source="onvista", isin=spec["isin"], exchange="XET", currency="EUR")
        old = {"isin": spec["isin"], "ref": old_refs[spec["symbol"]]} if spec["symbol"] in old_refs else None
        try:
            series = fetch_series(session, target, old, start, end)
        except Exception as exc:
            print(f"  warning: benchmark {spec['symbol']}: onvista request failed ({exc})", file=sys.stderr)
            series = None
        if not series or not series["dates"] or (series["currency"] or "EUR") != "EUR":
            return previous
        raw[spec["symbol"]] = series

    main, proxy = raw[BENCHMARK["symbol"]], raw[BENCHMARK_PROXY["symbol"]]
    dates, closes = splice(proxy["dates"], proxy["closes"], main["dates"], main["closes"])
    return {
        "symbol": BENCHMARK["symbol"],
        "name": BENCHMARK["name"],
        "isin": BENCHMARK["isin"],
        "proxy": {k: BENCHMARK_PROXY[k] for k in ("symbol", "name", "isin")},
        # First date that is the benchmark itself rather than the proxy.
        "splicedAt": main["dates"][0],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "refs": {sym: s["ref"] for sym, s in raw.items() if s.get("ref")},
        "dates": dates,
        "closesEur": closes,
    }


def is_fresh(previous: dict, targets: list[Target], max_age_hours: float) -> bool:
    """Is the last export recent enough, and does it still cover every holding?

    Same contract as marketcaps.is_fresh: a new holding forces a refresh
    however recent the file is, an unresolvable one doesn't keep forcing them.
    """
    series = previous.get("series") if isinstance(previous, dict) else None
    if not series or not previous.get("benchmark"):
        return False
    try:
        updated = datetime.fromisoformat(previous["updatedAt"])
    except (KeyError, TypeError, ValueError):
        return False
    if (datetime.now(timezone.utc) - updated).total_seconds() > max_age_hours * 3600:
        return False
    known = set(series) | set(previous.get("unresolved", []))
    return all(t.symbol in known for t in targets)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT),
        help="Directory containing the data/ folder (default: the project root)",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        help=(
            "Do nothing if the existing export is younger than this and already "
            "covers every holding (how the web app's dev server runs it)."
        ),
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / OUT_NAME

    targets = collect_targets(out_dir)
    if not targets:
        raise SystemExit(
            f"No holdings found in {out_dir}/.\n"
            "  Run `plout degiro` / `plout revolut` first."
        )

    previous = _read_json(out_file) or {}
    if args.max_age_hours is not None and is_fresh(previous, targets, args.max_age_hours):
        print(f"Prices are up to date ({previous['updatedAt']}); nothing to fetch.")
        return

    print(f"Fetching daily prices for {len(targets)} holdings...", flush=True)
    series, unresolved = build_series(targets, previous)
    benchmark = build_benchmark(previous.get("benchmark"))

    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "onvista (DEGIRO products), Coinbase (crypto), ECB reference rates (FX)",
        "baseCurrency": "EUR",
        "unresolved": unresolved,
        "series": series,
        "benchmark": benchmark,
    }
    out_file.write_text(json.dumps(payload, ensure_ascii=False) + "\n")

    print(f"Wrote {len(series)} price series to {out_file}")
    if benchmark:
        print(f"  benchmark {benchmark['symbol']}: {benchmark['dates'][0]} → {benchmark['dates'][-1]}"
              f" ({benchmark['proxy']['symbol']} before {benchmark['splicedAt']})")
    else:
        print("  warning: no benchmark series — the return chart's index line is hidden.", file=sys.stderr)
    if unresolved:
        print(
            f"  warning: no prices for {', '.join(unresolved)} — they are left out "
            "of the correlation page.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
