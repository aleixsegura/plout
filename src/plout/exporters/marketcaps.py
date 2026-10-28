"""
Export market capitalisations to data/marketcaps.json.

Market cap is resolved from public quote APIs for what is already exported:
the DEGIRO positions and the Revolut crypto holdings. ETFs and funds are
skipped — they have no market cap (their size is AUM, which is not the same
number).

Sources, all keyless and best-effort:
  yahoo      query[12].finance.yahoo.com/v7/finance/quote, batched. Covers
             non-US listings too (Xetra, Paris, …) via "VWCE.DE" style
             suffixes, derived from each position's exchange. Needs a cookie + crumb pair
             and rate-limits with 429s, which is why there are fallbacks.
  nasdaq     api.nasdaq.com summary endpoint, one request per US symbol — the
             fallback whenever Yahoo is unavailable for a US-listed stock.
  coingecko  api.coingecko.com/api/v3/coins/markets for crypto (BTC, …).

A symbol that no source answers for keeps the value from the previous run
(entries carry their own `at`), so a rate-limited Yahoo never empties the file.

Normally nobody runs this by hand: the web app's dev server starts it (see the
`plout:market-caps` plugin in web/vite.config.ts) with --max-age-hours, so
it exits before its first request unless the export is stale or a symbol is new.

Usage:
  plout marketcaps [--output-dir DIR] [--max-age-hours 12]
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from plout.config import PROJECT_ROOT

OUT_NAME = "marketcaps.json"

# DEGIRO `productType` values with no market cap:
# funds are sized by AUM, and currencies/indices by nothing at all.
SKIP_TYPES = {"ETF", "MUTUAL_FUND", "FUND", "CURRENCY", "INDEX", "BOND", "FUTURE"}

# Yahoo and Nasdaq both reject default python-requests clients.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

YAHOO_HOSTS = ("query2", "query1")
YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_BATCH = 50
NASDAQ_SUMMARY_URL = "https://api.nasdaq.com/api/quote/{symbol}/summary"
NASDAQ_WORKERS = 8
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"

# DEGIRO's short names for the US exchanges — the positions whose symbol Yahoo
# lists bare, and whose ticker the Nasdaq fallback can look up.
US_EXCHANGES = {"NSY", "NDQ", "ASE", "OTC", "ARCA", "BATS", "PCX", "PNK"}

# DEGIRO exchange short name → Yahoo symbol suffix. Tradegate (TDG) is
# where DEGIRO books most European trades but Yahoo barely lists it, so its
# products resolve against Xetra — same ISIN, same company, same market cap.
YAHOO_SUFFIX_BY_EXCHANGE = {
    **{e: "" for e in US_EXCHANGES},
    "XET": ".DE", "XETRA": ".DE", "TDG": ".DE", "GER": ".DE", "ETR": ".DE",
    "FRA": ".F", "FSX": ".F", "STU": ".SG", "MUN": ".MU", "BER": ".BE",
    "EPA": ".PA", "PAR": ".PA", "EAM": ".AS", "AMS": ".AS", "EBR": ".BR",
    "BRU": ".BR", "MIL": ".MI", "BIT": ".MI", "MAD": ".MC", "BME": ".MC",
    "LSE": ".L", "LON": ".L", "SWX": ".SW", "EBS": ".SW", "VIE": ".VI",
    "OSL": ".OL", "OSE": ".OL", "STO": ".ST", "OMX": ".ST", "CPH": ".CO",
    "HEL": ".HE", "LIS": ".LS", "WSE": ".WA", "TOR": ".TO", "TSX": ".TO",
    "HKS": ".HK", "TSE": ".T", "ASX": ".AX",
}


@dataclass
class Target:
    """One company to resolve, plus every symbol spelling the app calls it by.

    Yahoo spells a holding "VWCE.DE" where DEGIRO says plain "VWCE", and the
    web app looks market caps up by whichever symbol the row carries, so one
    resolved value is written out under every spelling.
    """

    aliases: set[str] = field(default_factory=set)
    yahoo: str | None = None
    nasdaq: str | None = None
    coingecko: str | None = None


def _base(symbol: str) -> str:
    """Ticker without its exchange suffix: "VWCE.DE" → "VWCE"."""
    return symbol.split(".")[0]


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ── Symbol collection ──────────────────────────────────────────────────────────

def collect_targets(out_dir: Path) -> dict[str, Target]:
    """Everything worth a market cap, keyed by Yahoo symbol (or "crypto:btc")."""
    targets: dict[str, Target] = {}

    def target(key: str) -> Target:
        return targets.setdefault(key, Target())

    positions = _read_json(out_dir / "positions.json") or {}
    for position in positions.get("positions", []):
        symbol = position.get("symbol")
        if not symbol:
            continue
        if (position.get("productType") or "").upper() in SKIP_TYPES:
            continue
        exchange = (position.get("exchange") or "").upper()
        suffix = YAHOO_SUFFIX_BY_EXCHANGE.get(exchange)
        if suffix is None:
            # Unknown exchange: USD products are US-listed often enough to be
            # worth the bare-symbol guess; anything else is left alone.
            if position.get("currency") != "USD":
                continue
            suffix = ""
        key = symbol + suffix
        entry = target(key)
        entry.yahoo = entry.yahoo or key
        entry.aliases.add(symbol)
        if exchange in US_EXCHANGES:
            entry.nasdaq = entry.nasdaq or _base(symbol)

    revolut = _read_json(out_dir / "revolut.json") or {}
    for position in revolut.get("positions", []):
        symbol = position.get("symbol")
        if not symbol:
            continue
        # The Revolut exporter only ever captures crypto.
        entry = target(f"crypto:{symbol.lower()}")
        entry.coingecko = symbol.lower()
        entry.aliases.add(symbol)

    return targets


# ── Sources ────────────────────────────────────────────────────────────────────

def _entry(cap, currency: str | None, name: str | None, source: str) -> dict:
    entry = {"marketCap": int(cap), "currency": currency, "source": source}
    if name:
        entry["name"] = name
    return entry


def _yahoo_crumb(session: requests.Session) -> str | None:
    """Yahoo's quote API needs a session cookie plus the crumb bound to it."""
    try:
        session.get(YAHOO_COOKIE_URL, timeout=15)
    except Exception:
        pass  # this request is only here for the Set-Cookie; it often 404s
    for host in YAHOO_HOSTS:
        try:
            resp = session.get(
                f"https://{host}.finance.yahoo.com/v1/test/getcrumb", timeout=15
            )
        except Exception:
            continue
        crumb = resp.text.strip()
        if resp.status_code == 200 and crumb and " " not in crumb:
            return crumb
    return None


def fetch_yahoo(symbols: list[str]) -> dict[str, dict]:
    """Market cap per Yahoo symbol. Empty when Yahoo rate-limits the run."""
    if not symbols:
        return {}
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    crumb = _yahoo_crumb(session)
    if crumb is None:
        print(
            "  warning: Yahoo refused a quote crumb (rate limit) — "
            "falling back to Nasdaq for US symbols.",
            file=sys.stderr,
        )
        return {}

    out: dict[str, dict] = {}
    for start in range(0, len(symbols), YAHOO_BATCH):
        chunk = symbols[start : start + YAHOO_BATCH]
        params = {"symbols": ",".join(chunk), "crumb": crumb}
        payload = None
        error = None
        for host in YAHOO_HOSTS:
            try:
                resp = session.get(
                    f"https://{host}.finance.yahoo.com/v7/finance/quote",
                    params=params,
                    timeout=20,
                )
                resp.raise_for_status()
                payload = resp.json()
                break
            except Exception as exc:
                error = exc
        if payload is None:
            print(f"  warning: Yahoo quote request failed ({error})", file=sys.stderr)
            continue
        for quote in payload.get("quoteResponse", {}).get("result", []):
            symbol = quote.get("symbol")
            cap = quote.get("marketCap")
            if not symbol or not cap:
                continue
            out[symbol] = _entry(
                cap, quote.get("currency"), quote.get("longName") or quote.get("shortName"), "yahoo"
            )
    return out


def _parse_number(value) -> int | None:
    """Nasdaq formats every figure for display: "16,570,217,182", "N/A"."""
    if value is None:
        return None
    try:
        return int(float(str(value).replace(",", "").replace("$", "").strip()))
    except ValueError:
        return None


def fetch_nasdaq(symbol: str) -> dict | None:
    try:
        resp = requests.get(
            NASDAQ_SUMMARY_URL.format(symbol=symbol),
            params={"assetclass": "stocks"},
            headers=BROWSER_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
    except Exception:
        return None
    summary = data.get("summaryData") or {}
    cap = _parse_number((summary.get("MarketCap") or {}).get("value"))
    if not cap:
        return None
    # The endpoint only covers US listings, so the figure is always in USD.
    return _entry(cap, "USD", data.get("companyName"), "nasdaq")


def fetch_coingecko(coins: list[str]) -> dict[str, dict]:
    """Market cap per coin symbol (lowercase), in USD."""
    if not coins:
        return {}
    try:
        resp = requests.get(
            COINGECKO_URL,
            params={"vs_currency": "usd", "symbols": ",".join(sorted(coins))},
            headers=BROWSER_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        print(f"  warning: CoinGecko request failed ({exc})", file=sys.stderr)
        return {}

    out: dict[str, dict] = {}
    for coin in payload if isinstance(payload, list) else []:
        symbol = (coin.get("symbol") or "").lower()
        cap = coin.get("market_cap")
        if not symbol or not cap:
            continue
        # A ticker can be claimed by several coins; the real one is the big one.
        if symbol in out and out[symbol]["marketCap"] >= cap:
            continue
        out[symbol] = _entry(cap, "USD", coin.get("name"), "coingecko")
    return out


# ── Export ─────────────────────────────────────────────────────────────────────

def resolve(targets: dict[str, Target]) -> dict[str, dict]:
    """Best available market cap per target key."""
    yahoo = fetch_yahoo(sorted({t.yahoo for t in targets.values() if t.yahoo}))
    coingecko = fetch_coingecko([t.coingecko for t in targets.values() if t.coingecko])

    resolved: dict[str, dict] = {}
    pending: dict[str, str] = {}  # target key → US symbol still to look up
    for key, target in targets.items():
        value = None
        if target.yahoo:
            value = yahoo.get(target.yahoo)
        if value is None and target.coingecko:
            value = coingecko.get(target.coingecko)
        if value is not None:
            resolved[key] = value
        elif target.nasdaq:
            pending[key] = target.nasdaq

    # Nasdaq answers one symbol per request, so a Yahoo-less run would take
    # minutes served serially; the endpoint is happy with a few at a time.
    if pending:
        with ThreadPoolExecutor(max_workers=NASDAQ_WORKERS) as pool:
            for key, value in zip(pending, pool.map(fetch_nasdaq, pending.values())):
                if value is not None:
                    resolved[key] = value
    return resolved


def build_entries(
    targets: dict[str, Target], resolved: dict[str, dict], previous: dict
) -> tuple[dict[str, dict], list[str]]:
    """One entry per alias, keeping the previous value for what didn't resolve."""
    old_entries = previous.get("entries", {}) if isinstance(previous, dict) else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    entries: dict[str, dict] = {}
    unresolved: list[str] = []
    for key, target in sorted(targets.items()):
        value = resolved.get(key)
        if value is not None:
            entry = {**value, "at": now}
        else:
            # Same company, so any alias's previous entry will do.
            entry = next(
                (old_entries[a] for a in sorted(target.aliases) if a in old_entries), None
            )
            if entry is None:
                unresolved.append(sorted(target.aliases)[0])
                continue
        for alias in target.aliases:
            entries[alias] = entry

    return dict(sorted(entries.items())), unresolved


def is_fresh(previous: dict, targets: dict[str, Target], max_age_hours: float) -> bool:
    """Is the last export recent enough, and does it still cover every symbol?

    A new holding has no entry yet, so it forces a refresh however recent the
    file is — buying something shouldn't leave its row blank until the cache
    happens to expire.
    """
    entries = previous.get("entries") if isinstance(previous, dict) else None
    if not entries:
        return False
    try:
        updated = datetime.fromisoformat(previous["updatedAt"])
    except (KeyError, TypeError, ValueError):
        return False
    age = datetime.now(timezone.utc) - updated
    if age.total_seconds() > max_age_hours * 3600:
        return False
    # An unresolvable symbol (no source answers for it) would otherwise force a
    # full refetch on every single start, so only *new* symbols count: ones the
    # last run never wrote an entry for and never reported as unresolved.
    known = set(entries) | set(previous.get("unresolved", []))
    return all(target.aliases & known for target in targets.values())


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
            "covers every symbol. This is how the web app's dev server can fire "
            "the exporter on every start without hitting the APIs each time."
        ),
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / OUT_NAME

    targets = collect_targets(out_dir)
    if not targets:
        raise SystemExit(
            f"No symbols found in {out_dir}/.\n"
            "  Run `plout degiro` / `plout revolut` first."
        )

    previous = _read_json(out_file) or {}
    if args.max_age_hours is not None and is_fresh(previous, targets, args.max_age_hours):
        print(f"Market caps are up to date ({previous['updatedAt']}); nothing to fetch.")
        return

    print(f"Resolving market cap for {len(targets)} symbols...", flush=True)
    resolved = resolve(targets)
    entries, unresolved = build_entries(targets, resolved, previous)

    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance quote API, Nasdaq (US fallback), CoinGecko (crypto)",
        # Symbols no source answered for, so a --max-age-hours run can tell them
        # apart from symbols it has never seen before.
        "unresolved": unresolved,
        "entries": entries,
    }
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    by_source: dict[str, int] = {}
    for value in resolved.values():
        by_source[value["source"]] = by_source.get(value["source"], 0) + 1
    cached = len(targets) - len(resolved) - len(unresolved)
    breakdown = ", ".join(f"{name}: {count}" for name, count in sorted(by_source.items()))
    print(
        f"Wrote {len(entries)} symbol entries to {out_file}\n"
        f"  {len(resolved)} fresh ({breakdown or 'none'}), {cached} kept from the previous run"
    )
    if unresolved:
        shown = ", ".join(unresolved[:10])
        rest = f" and {len(unresolved) - 10} more" if len(unresolved) > 10 else ""
        print(
            f"  warning: no market cap for {shown}{rest} — they stay blank in the "
            "table. Non-US listings need Yahoo, so a rate-limited run leaves them "
            "for the next one.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
