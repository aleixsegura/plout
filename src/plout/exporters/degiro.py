"""
Export DEGIRO portfolio positions to data/positions.json.

Uses the unofficial degiro-connector library (installed by `pip install -e .`)
to log in with DEGIRO_USERNAME / DEGIRO_PASSWORD from .env (or environment),
fetch the current portfolio and account totals, and write a single JSON file
the web app serves at /positions.json.

Optional .env keys:
  DEGIRO_INT_ACCOUNT  — the account number; looked up when absent.
  DEGIRO_TOTP_SECRET  — the authenticator-app secret, for a login that needs
                        no phone (the account's 2FA must use an authenticator
                        app rather than the in-app confirmation).

The session is shared with the other DEGIRO exporters through
.degiro-session.json (see SESSION_FILE), so a batch of exports logs in once.
--logout ends it.

All monetary values come straight from DEGIRO: per-position `value`, `plBase`
and `todayPlBase` are in the account base currency (EUR), while `price` and
`breakEvenPrice` are in the product's own currency (USD for US stocks). The
only conversion done here applies DEGIRO's own `averageFxRate` to the cost
basis (`size * breakEvenPrice`) so it can be compared against the EUR `value`.

`plEur` is the *unrealized* P/L (`value - cost`), matching the "potential G/L"
the DEGIRO app shows per position. It is deliberately not `value + plBase`:
plBase carries the product's whole account history, so that sum also includes
what earlier partial sells already banked (and the buy fees DEGIRO leaves out
of `breakEvenPrice`) — exported separately as `realizedPlEur`.

The REST portfolio snapshot prices every position at the *previous* close —
`todayPlBase` comes back as exactly `-value`, so today's P/L is always 0 and
`price` lags a full session. DEGIRO's own app hides this by overlaying quotes
from the vwd feed on top of that snapshot, so this script does the same: it
re-prices each position from vwd (see `fetch_quotes`) and falls back to the
stale snapshot price per position when a quote is unavailable. Pass
--no-quotes to keep DEGIRO's raw snapshot.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from plout.config import ENV_FILE, PROJECT_ROOT, load_env_file
from plout.history import record_snapshot

import requests

try:
    from degiro_connector.core.constants import urls
    from degiro_connector.core.exceptions import DeGiroConnectionError
    from degiro_connector.trading.api import API as TradingAPI
    from degiro_connector.trading.models.account import UpdateOption, UpdateRequest
    from degiro_connector.trading.models.credentials import Credentials
except ImportError:
    sys.exit(
        "degiro-connector is not installed.\n"
        "  .venv/bin/pip install -e .\n"
        "(create the venv first if needed: python3 -m venv .venv)"
    )


# How long to wait for the in-app login approval when there's no terminal.
APPROVAL_WAIT_SECONDS = 180
APPROVAL_POLL_SECONDS = 5


def get_env(name: str) -> str | None:
    return os.environ.get(name) or load_env_file(ENV_FILE).get(name) or None


# ── session reuse ──────────────────────────────────────────────────────────────

# Every DEGIRO exporter used to log in (and out) on its own, which cost one
# phone confirmation per script. The session id is shared instead: the first
# script to log in leaves it here, the rest resume it, and DEGIRO itself expires
# it after 30 minutes without a request. Gitignored — it grants account access.
SESSION_FILE = PROJECT_ROOT / ".degiro-session.json"
# Past this a saved session is certainly dead, so don't even spend a request on it.
SESSION_MAX_AGE_SECONDS = 12 * 3600


def load_session(username: str, path: Path = SESSION_FILE, now: float | None = None) -> dict | None:
    """The saved session for `username`, or None when absent, foreign or stale."""
    try:
        session = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(session, dict) or session.get("username") != username:
        return None
    if not session.get("session_id") or not session.get("int_account"):
        return None
    age = (time.time() if now is None else now) - float(session.get("saved_at") or 0)
    if age > SESSION_MAX_AGE_SECONDS:
        return None
    return session


def save_session(
    username: str,
    session_id: str,
    int_account: int,
    user_token: int | None,
    path: Path = SESSION_FILE,
) -> None:
    payload = {
        "username": username,
        "session_id": session_id,
        "int_account": int_account,
        "user_token": user_token,
        "saved_at": time.time(),
    }
    # Created 0600 from the start rather than chmod-ed after the write.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)


def forget_session(path: Path = SESSION_FILE) -> None:
    path.unlink(missing_ok=True)


def _client_details(api: TradingAPI) -> dict | None:
    """The client-details `data`, or None when the session is not (or no longer) valid."""
    # degiro-connector logs every failed request at CRITICAL, which is just
    # noise when probing a session that has expired.
    logging.disable(logging.CRITICAL)
    try:
        details = api.get_client_details()
    except Exception:
        details = None
    finally:
        logging.disable(logging.NOTSET)
    data = details.get("data") if isinstance(details, dict) else None
    return data if isinstance(data, dict) and data.get("intAccount") else None


def _resume(credentials: Credentials, session: dict) -> tuple[TradingAPI, dict] | None:
    credentials.int_account = int(session["int_account"])
    api = TradingAPI(credentials=credentials)
    api.connection_storage.session_id = session["session_id"]
    data = _client_details(api)
    return (api, data) if data else None


def _wait_for_fresh_totp_window() -> None:
    """Sleep past the tail of the current 30 s TOTP window, so the code
    degiro-connector generates is not already expired by the time DEGIRO checks it."""
    remaining = 30 - time.time() % 30
    if remaining < 3:
        time.sleep(remaining + 0.5)


def _login(credentials: Credentials) -> TradingAPI:
    api = TradingAPI(credentials=credentials)
    if credentials.totp_secret_key:
        _wait_for_fresh_totp_window()
    try:
        api.connect()
        return api
    except DeGiroConnectionError as exc:
        # Status 12 = DEGIRO's in-app login confirmation: the failed attempt
        # returns an inAppToken and pushes a notification to the phone app;
        # once approved, retrying the login with that token succeeds.
        details = getattr(exc, "error_details", None)
        token = getattr(details, "in_app_token", None)
        if getattr(details, "status", None) != 12 or not token:
            raise
        print(
            "DEGIRO asks you to confirm this login in their app.\n"
            f"  Open the DEGIRO app on your phone and tap 'Yes' (code: {token}).\n"
            "  (Set DEGIRO_TOTP_SECRET in .env to log in without the phone — see README.)",
            file=sys.stderr,
        )
        credentials.in_app_token = token
        if sys.stdin.isatty():
            input("  Press Enter here once you have approved it... ")
            api = TradingAPI(credentials=credentials)
            api.connect()
            return api
        # No terminal to wait on (cron, an agent shell, a pipe): poll the
        # login with the token instead — it only succeeds once the tap lands.
        print(
            f"  Waiting up to {APPROVAL_WAIT_SECONDS}s for the approval...",
            file=sys.stderr,
        )
        deadline = time.monotonic() + APPROVAL_WAIT_SECONDS
        while True:
            api = TradingAPI(credentials=credentials)
            try:
                api.connect()
                return api
            except DeGiroConnectionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(APPROVAL_POLL_SECONDS)


def connect() -> tuple[TradingAPI, int | None]:
    """Log in to DEGIRO, resuming the session another exporter left when it is still alive.

    The session is deliberately not logged out afterwards (see SESSION_FILE);
    `plout degiro --logout` ends it explicitly.
    """
    username = get_env("DEGIRO_USERNAME")
    password = get_env("DEGIRO_PASSWORD")
    missing = [k for k, v in (("DEGIRO_USERNAME", username), ("DEGIRO_PASSWORD", password)) if not v]
    if missing:
        raise SystemExit(
            f"Missing DEGIRO credentials: {', '.join(missing)}.\n"
            f"  Add them to {ENV_FILE} (one KEY=value per line)."
        )

    int_account = get_env("DEGIRO_INT_ACCOUNT")
    # With the account's 2FA set to an authenticator app, degiro-connector
    # derives the one-time code from this secret: no phone involved at all.
    totp_secret = get_env("DEGIRO_TOTP_SECRET")
    credentials = Credentials(
        username=username,
        password=password,
        int_account=int(int_account) if int_account else None,
        totp_secret_key=totp_secret.replace(" ", "") if totp_secret else None,
    )

    saved = load_session(username)
    resumed = _resume(credentials, saved) if saved else None
    if resumed:
        api, data = resumed
        print("Reusing the DEGIRO session from an earlier export.", file=sys.stderr)
    else:
        credentials.int_account = int(int_account) if int_account else None
        api = _login(credentials)
        # `id` doubles as the vwd "user token" the quote feed authenticates with.
        data = _client_details(api) or {}

    if api.credentials.int_account is None:
        api.credentials.int_account = data["intAccount"]
    user_token = data.get("id")
    user_token = int(user_token) if user_token is not None else None
    try:
        save_session(
            username,
            api.connection_storage.session_id,
            api.credentials.int_account,
            user_token,
        )
    except OSError as exc:
        print(f"  warning: could not save the DEGIRO session ({exc})", file=sys.stderr)
    return api, user_token


def logout() -> None:
    """End the shared session on DEGIRO's side and forget it locally."""
    username = get_env("DEGIRO_USERNAME")
    password = get_env("DEGIRO_PASSWORD")
    saved = load_session(username) if username else None
    if saved and password:
        credentials = Credentials(
            username=username, password=password, int_account=int(saved["int_account"])
        )
        api = TradingAPI(credentials=credentials)
        api.connection_storage.session_id = saved["session_id"]
        try:
            api.logout()
        except Exception:
            pass
    forget_session()
    print("Logged out of DEGIRO." if saved else "No DEGIRO session to end.")


# ── raw-update parsing ─────────────────────────────────────────────────────────

def _fields(row_value: list[dict]) -> dict:
    """DEGIRO update rows are lists of {name, value} pairs — flatten to a dict."""
    return {f["name"]: f.get("value") for f in row_value}


def _base_ccy(value) -> float:
    """plBase/todayPlBase arrive as one-key dicts like {"EUR": -125.40}."""
    if isinstance(value, dict):
        return float(value.get("EUR", next(iter(value.values()), 0.0) or 0.0))
    return float(value or 0.0)


def fetch_portfolio(api: TradingAPI) -> tuple[list[dict], list[dict], dict]:
    """Return (product position rows, cash rows, total-portfolio fields) as dicts."""
    update = api.get_update(
        request_list=[
            UpdateRequest(option=UpdateOption.PORTFOLIO, last_updated=0),
            UpdateRequest(option=UpdateOption.TOTAL_PORTFOLIO, last_updated=0),
        ],
        raw=True,
    )

    rows = []
    cash_rows = []
    for row in update.get("portfolio", {}).get("value", []):
        fields = _fields(row.get("value", []))
        fields["id"] = str(row["id"])
        if fields.get("positionType") == "CASH":
            cash_rows.append(fields)  # one row per account+currency: EUR, USD, FLATEX_EUR, …
            continue
        if fields.get("positionType") != "PRODUCT":
            continue
        if float(fields.get("size") or 0) <= 0:
            continue  # sold positions linger with size 0
        rows.append(fields)

    total = _fields(update.get("totalPortfolio", {}).get("value", []))
    return rows, cash_rows, total


def fetch_product_meta(api: TradingAPI, product_ids: list[str]) -> dict[str, dict]:
    if not product_ids:
        return {}
    info = api.get_products_info(product_list=[int(pid) for pid in product_ids], raw=True)
    return info.get("data", {})


# ── vwd quotes ─────────────────────────────────────────────────────────────────

# degiro-connector ships a ChartFetcher for this endpoint, but importing it
# pulls in polars just to format timeseries this script never asks for.
CHART_URL = urls.CHART.rstrip("?")
QUOTE_TZ = "Europe/Madrid"
QUOTE_BATCH = 20  # series per request; the endpoint happily takes several
QUOTE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://trader.degiro.nl/",
}


def _vwd_series(product: dict) -> str | None:
    """Chart-feed series key for a product, e.g. "issueid:360148977"."""
    vwd_id = product.get("vwdId")
    if not vwd_id:
        return None  # untradable, or no market-data licence for this product
    kind = product.get("vwdIdentifierType") or "issueid"
    return f"{kind}:{vwd_id}"


def _quote_from_series(series: dict) -> dict | None:
    """Read one series entry of a chart response.

    Asking for a bare "issueid:X" series (no price:/ohlc: prefix) makes vwd
    answer with a metadata series whose data is a dict of quote fields instead
    of a list of samples — the same dict the trader UI reads. Series that
    failed come back as {"type": "error", ...} and yield None here.
    """
    data = series.get("data")
    if not isinstance(data, dict) or data.get("lastPrice") is None:
        return None
    return {
        "price": float(data["lastPrice"]),
        "at": data.get("lastTime") or data.get("lastDate"),
        "quality": data.get("quality"),
    }


def fetch_quotes(user_token: int, meta: dict[str, dict]) -> dict[str, dict]:
    """Last vwd price per product id — the quotes DEGIRO's own app displays.

    Delayed by 15 minutes on exchanges the account has no real-time licence
    for, but that is what the app shows too, and it beats the previous-close
    prices baked into the portfolio snapshot. Failures degrade per product: a
    missing quote just leaves that position on its snapshot price.
    """
    product_by_series = {}
    for product_id, product in meta.items():
        series = _vwd_series(product)
        if series:
            product_by_series[series] = product_id

    quotes: dict[str, dict] = {}
    keys = list(product_by_series)
    for start in range(0, len(keys), QUOTE_BATCH):
        chunk = keys[start : start + QUOTE_BATCH]
        params = {
            "requestid": "1",
            "resolution": "PT1H",
            "period": "P1D",
            "series": chunk,
            "culture": "en-US",
            "format": "json",
            "tz": QUOTE_TZ,
            "userToken": user_token,
        }
        try:
            response = requests.get(
                CHART_URL, params=params, headers=QUOTE_HEADERS, timeout=20
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            print(f"  warning: vwd quote request failed ({exc})", file=sys.stderr)
            continue

        for series in payload.get("series", []):
            quote = _quote_from_series(series)
            product_id = product_by_series.get(series.get("id"))
            if quote and product_id:
                quotes[product_id] = quote

    missing = [meta[pid].get("symbol", pid) for pid in meta if pid not in quotes]
    if missing:
        print(
            f"  warning: no vwd quote for {', '.join(missing)} — "
            "keeping DEGIRO's previous-close price for those.",
            file=sys.stderr,
        )
    delayed = {q["quality"] for q in quotes.values() if q.get("quality") != "REALTIME"}
    if delayed:
        print(f"  note: some quotes are not real-time ({', '.join(sorted(map(str, delayed)))}).")
    return quotes


def fetch_exchange_names(api: TradingAPI) -> dict[int, str]:
    # Cosmetic only — exchangeId → short name like NSY/XET.
    try:
        config = api.get_products_config()
        return {
            e["id"]: e.get("hiqAbbr") or e.get("name") or str(e["id"])
            for e in config.get("exchanges", [])
        }
    except Exception:
        return {}


# ── position building ──────────────────────────────────────────────────────────

def build_positions(
    raw_rows: list[dict],
    meta: dict[str, dict],
    exchanges: dict[int, str],
    quotes: dict[str, dict],
) -> list[dict]:
    positions = []
    for row in raw_rows:
        product = meta.get(row["id"], {})
        size = float(row.get("size") or 0)
        snapshot_value_eur = float(row.get("value") or 0)
        value_eur = snapshot_value_eur
        break_even = float(row.get("breakEvenPrice") or 0)
        fx = row.get("averageFxRate")
        fx = float(fx) if fx else None

        # plBase accumulates the whole history of the product (realized results
        # of earlier partial sells included), so `value + plBase` is DEGIRO's
        # account-lifetime P/L for the position, not the "G/P potencial" its app
        # shows. todayPlBase is the negated EUR value at previous close.
        pl_base = _base_ccy(row.get("plBase"))
        today_pl_base = _base_ccy(row.get("todayPlBase"))
        lifetime_cost_eur = -pl_base

        snapshot_price = float(row["price"]) if row.get("price") is not None else None
        last_price = snapshot_price
        price_source = "degiro-snapshot"
        price_at = None
        quote = quotes.get(row["id"])
        if quote and snapshot_price and size and value_eur:
            # `value` is EUR and `price` is the product's currency, so their
            # ratio is the FX rate DEGIRO itself applied to this position —
            # re-pricing needs no external FX source.
            eur_per_unit = value_eur / (size * snapshot_price)
            last_price = quote["price"]
            value_eur = size * last_price * eur_per_unit
            price_source = "vwd"
            price_at = quote.get("at")

        # Cost basis of the shares still held: breakEvenPrice is in the product's
        # currency and averageFxRate is the rate DEGIRO applied while building
        # the position, but its direction isn't documented (observed accounts
        # return the product→base multiplier, ~0.86 for USD/EUR). The two
        # directions sit ~30% apart, so pick whichever lands nearest the spot
        # product→EUR rate implied by the snapshot (`value` is EUR, `price` is
        # the product's currency), and fall back to the lifetime cost when the
        # snapshot carries no price.
        raw_cost = size * break_even
        rate = 1.0
        if fx and fx not in (0, 1):
            spot_rate = (
                snapshot_value_eur / (size * snapshot_price)
                if snapshot_price and size and snapshot_value_eur
                else None
            )
            if spot_rate:
                rate = min((fx, 1 / fx), key=lambda r: abs(r - spot_rate))
            elif lifetime_cost_eur > 0 and raw_cost > 0:
                rate = min((fx, 1 / fx), key=lambda r: abs(lifetime_cost_eur - raw_cost * r))
            else:
                rate = fx
        cost_eur = raw_cost * rate

        # Unrealized P/L — DEGIRO's "G/P potencial" for the position.
        pl_eur = value_eur - cost_eur
        today_pl_eur = value_eur + today_pl_base
        # The rest of plBase: what earlier sells of this product already banked,
        # plus the buy fees DEGIRO keeps in plBase but leaves out of
        # breakEvenPrice (so a never-sold position shows a small negative here).
        realized_pl_eur = cost_eur - lifetime_cost_eur

        exchange_id = product.get("exchangeId")
        try:
            exchange = exchanges.get(int(exchange_id)) if exchange_id is not None else None
        except (TypeError, ValueError):
            exchange = None

        positions.append(
            {
                "id": row["id"],
                "symbol": product.get("symbol") or row["id"],
                "name": product.get("name") or "",
                # What prices.py looks the price history up by.
                "isin": product.get("isin"),
                # DEGIRO's own classification ("STOCK", "ETF", "FUND", …),
                # which the web app turns into the row's type icon.
                "productType": product.get("productType"),
                "quantity": size,
                "avgPrice": round(break_even, 4),
                "currency": product.get("currency") or "EUR",
                "lastPrice": round(last_price, 4) if last_price is not None else None,
                "lastPriceSource": price_source,
                "lastPriceAt": price_at,
                "valueEur": round(value_eur, 2),
                "costEur": round(cost_eur, 2),
                "plEur": round(pl_eur, 2),
                "todayPlEur": round(today_pl_eur, 2),
                "realizedPlEur": round(realized_pl_eur, 2),
                "averageFxRate": fx,
                "exchange": exchange or (str(exchange_id) if exchange_id is not None else None),
            }
        )

    positions.sort(key=lambda p: p["valueEur"], reverse=True)
    return positions


def build_cash_breakdown(cash_rows: list[dict]) -> list[dict]:
    """Per-currency cash balances, merged across the DEGIRO and flatex accounts.

    Cash rows have `size` in the row's own currency and `value` as DEGIRO's EUR
    countervalue (identical for EUR rows), so no FX conversion is needed here.
    """
    by_ccy: dict[str, dict] = {}
    for row in cash_rows:
        ccy = str(row["id"]).removeprefix("FLATEX_")
        entry = by_ccy.setdefault(ccy, {"currency": ccy, "amount": 0.0, "amountEur": 0.0})
        entry["amount"] += float(row.get("size") or 0)
        entry["amountEur"] += float(row.get("value") or 0)

    entries = [
        {
            "currency": e["currency"],
            "amount": round(e["amount"], 2),
            "amountEur": round(e["amountEur"], 2),
        }
        for e in by_ccy.values()
        if round(e["amount"], 2) != 0 or round(e["amountEur"], 2) != 0
    ]
    entries.sort(key=lambda e: (e["currency"] != "EUR", -e["amountEur"]))
    return entries


def build_summary(
    positions: list[dict],
    cash_breakdown: list[dict],
    total_portfolio: dict,
) -> dict:
    # totalPortfolio's totalCash is degiroCash + flatexCash — EUR rows only, so
    # foreign-currency cash (e.g. a USD account) is missing from it. Total the
    # per-currency breakdown instead; totalCash stays as the fallback for the
    # (never observed) case of an update without cash rows.
    if cash_breakdown:
        cash = sum(e["amountEur"] for e in cash_breakdown)
    else:
        cash = total_portfolio.get("totalCash")
    # Net liquidation = re-priced positions + cash, which is what DEGIRO's own
    # app totals. reportNetliq can't be used as a base and shifted by the
    # re-pricing delta: it is not reliably at the previous close (observed
    # already carrying today's move), so shifting it counted today's P/L twice.
    # It stays as the fallback when there is no cash figure to add up.
    if cash is not None:
        netliq = sum(p["valueEur"] for p in positions) + float(cash)
    else:
        netliq = total_portfolio.get("reportNetliq")
    net_deposits = total_portfolio.get("totalDepositWithdrawal")

    # DEGIRO's account-level "Total P/L" (realized + unrealized + dividends +
    # fees + FX since the account opened) = net liquidation − net deposits.
    account_pl = None
    if netliq is not None and net_deposits is not None:
        account_pl = round(float(netliq) - float(net_deposits), 2)

    return {
        "totalValueEur": round(sum(p["valueEur"] for p in positions), 2),
        "totalPlEur": round(sum(p["plEur"] for p in positions), 2),
        "totalTodayPlEur": round(sum(p["todayPlEur"] for p in positions), 2),
        "cashEur": round(float(cash), 2) if cash is not None else None,
        "cashBreakdown": cash_breakdown,
        "netLiquidationEur": round(float(netliq), 2) if netliq is not None else None,
        "netDepositsEur": round(float(net_deposits), 2) if net_deposits is not None else None,
        "accountPlEur": account_pl,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT),
        help="Directory containing the data/ folder (default: the project root)",
    )
    parser.add_argument(
        "--no-quotes",
        action="store_true",
        help="Skip the vwd quote refresh and keep DEGIRO's previous-close prices",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="End the DEGIRO session the exporters share, then exit",
    )
    args = parser.parse_args()

    if args.logout:
        logout()
        return

    out_dir = Path(args.output_dir).resolve() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "positions.json"

    api, user_token = connect()
    raw_rows, cash_rows, total_portfolio = fetch_portfolio(api)
    meta = fetch_product_meta(api, [row["id"] for row in raw_rows])
    exchanges = fetch_exchange_names(api)

    quotes: dict[str, dict] = {}
    if args.no_quotes:
        pass
    elif user_token is None:
        print(
            "  warning: no vwd user token in the client details — "
            "falling back to DEGIRO's previous-close prices.",
            file=sys.stderr,
        )
    else:
        quotes = fetch_quotes(user_token, meta)

    positions = build_positions(raw_rows, meta, exchanges, quotes)
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "DEGIRO via degiro-connector",
        "baseCurrency": "EUR",
        "summary": build_summary(positions, build_cash_breakdown(cash_rows), total_portfolio),
        "positions": positions,
    }

    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(positions)} positions to {out_file}")

    # Best-effort: a history failure must never fail an export that succeeded.
    try:
        record_snapshot(out_dir)
    except Exception as exc:
        print(f"  warning: could not update history.json ({exc})", file=sys.stderr)

    for p in positions:
        sign = "+" if p["plEur"] >= 0 else ""
        last = f"{p['lastPrice']:.4g}" if p["lastPrice"] is not None else "?"
        stale = "" if p["lastPriceSource"] == "vwd" else "  (prev close)"
        print(f"  {p['symbol']:<6} {p['quantity']:>8.4g} @ {last:<9} {p['currency']:<4}"
              f"avg {p['avgPrice']:<9.4g} value €{p['valueEur']:>9.2f}  "
              f"P/L {sign}€{p['plEur']:.2f}{stale}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(
            f"DEGIRO export failed: {exc}\n"
            f"  Check DEGIRO_USERNAME/DEGIRO_PASSWORD in {ENV_FILE}; if credentials are\n"
            f"  correct, DEGIRO may have changed their API — try updating degiro-connector."
        )
