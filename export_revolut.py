#!/usr/bin/env python3
"""
Export Revolut cash balances (current + savings) and crypto holdings to
data/revolut.json.

Revolut has no personal API, so this drives the Revolut web app
(https://app.revolut.com) with Playwright and a persistent browser profile,
capturing the app's own internal retail JSON API responses off the network —
no DOM scraping.

First run (or whenever the session expires):
  python3 export_revolut.py --login
opens a headed browser; log in with your phone number + passcode and approve
the push notification in the Revolut phone app. The session persists in
.revolut-profile/ (gitignored), so later runs work headlessly:
  python3 export_revolut.py

Besides balances, the EUR current account's full transaction history and the
Boosted (savings) account's daily interest are exported to
data/revolut_transactions.json — see write_transactions.

Balances arrive in minor units (cents, satoshis) and are converted with
Coinbase's public spot API, which also quotes fiat pairs. Every position and
pocket is normalized to EUR (the portfolio's base currency); crypto positions
additionally carry `lastPrice` in USD, the currency crypto is conventionally
quoted in.

If the export stops finding data, Revolut likely changed their internal API:
rerun with --debug (dumps every retail API response to .revolut-debug/) and
update CAPTURE_PATTERNS / ROUTES below.
"""

import argparse
import json
import os
import re
import signal
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import ENV_FILE, load_env_file
from history import record_snapshot

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit(
        "playwright is not installed.\n"
        "  .venv/bin/pip install -r requirements.txt\n"
        "  .venv/bin/playwright install chromium"
    )

SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_DIR = SCRIPT_DIR / ".revolut-profile"
DEBUG_DIR = SCRIPT_DIR / ".revolut-debug"
# Hand-maintained average buy prices per crypto symbol (Revolut exposes none,
# and the Bybit purchase prices were lost on transfer). See the file's _comment.
COST_BASIS_FILE = SCRIPT_DIR / "crypto_cost_basis.json"

APP_URL = "https://app.revolut.com"
API_PREFIX = "/api/retail/"

# Endpoint patterns pinned against the live web app (all fire on /home); when
# Revolut renames them, rerun with --debug and match the URLs in .revolut-debug/.
#   wallet      — fiat + crypto pockets ({pockets:[{type,currency,balance,…}]},
#                 balances in the currency's minor units).
#   money_boxes — savings / "Boosted" accounts ([{name,balance:{amount,currency}}]).
#   currencies  — {isoCode,exponent,isCrypto,isCommodity,…}; called several times
#                 (fiat / crypto / commodity sets), all merged into one lookup.
CAPTURE_PATTERNS: dict[str, re.Pattern] = {
    # user — the account holder's name, so a transfer to/from your own account
    #        at another bank is booked as money moved rather than spent/earned.
    "user": re.compile(r"/api/retail/user/current$"),
    "wallet": re.compile(r"/api/retail/user/current/wallet$"),
    "money_boxes": re.compile(r"/api/retail/user/current/money-boxes$"),
    "currencies": re.compile(r"/api/retail/currencies$"),
}
# Any authenticated response — the sign-in flow itself never gets a 200 from
# /user/current, so one means the session is live. Login detection only.
LOGGED_IN_PATTERN = re.compile(r"/api/retail/user/current(/|$)")

# Per-pocket transaction history. The web app only ever asks for the EUR
# current account (there is no crypto screen on the web at all), but the
# endpoint takes any pocket id — so the crypto pockets' history is there for
# the asking. That history is the only place a closed position survives: once a
# coin is sold, the wallet snapshot above shows nothing whatsoever, and with it
# goes every trace of what the sale made. See build_crypto_ledger.
TRANSACTIONS_ENDPOINT = "/api/retail/user/current/transactions/last"
TRANSACTIONS_PAGE_SIZE = 200
# Paging for the full current-account history. The web app itself only ever
# asks for the latest page (which is time-windowed: ~2 months, whatever
# `count` says); older rows are reached by re-asking with an upper timestamp
# bound. The parameter name is tried in this order and the first one
# that yields rows not already seen is kept (recorded as `pageParam` in the
# output) — so a rename on Revolut's side shows up as a warning, not a silent
# one-page export.
TRANSACTIONS_PAGE_PARAMS = ("to", "before")
TRANSACTIONS_MAX_PAGES = 200
TRANSACTIONS_PAGE_PAUSE_MS = 300
TRANSACTIONS_FILE = "revolut_transactions.json"
# A row older than the newest stored one by this much is not refetched on an
# incremental run; anything younger is, so a transaction that settled late
# (state change, updated merchant) is refreshed.
TRANSACTIONS_REFETCH_DAYS = 7
ROUTES: dict[str, str] = {"home": "/home"}
# Required before writing output. money-boxes is optional (an account may hold
# no savings); currencies is required to classify/convert balances.
REQUIRED_CAPTURES = ("wallet", "currencies")

LOGIN_TIMEOUT_S = 300  # how long to wait for the user to complete login
LOGIN_GRACE_S = 8      # if not auto-authenticated within this, prompt for login
CAPTURE_TIMEOUT_S = 45
HEADERS_GRACE_S = 20   # how long to wait for the app's own transactions call after the wallet


def get_env(name: str) -> str | None:
    return os.environ.get(name) or load_env_file(ENV_FILE).get(name) or None


# ── Network capture ────────────────────────────────────────────────────────────


class ResponseCapture:
    """Collects JSON bodies of retail-API responses matching CAPTURE_PATTERNS.

    Bodies are appended per key (some endpoints respond more than once, e.g.
    paginated or per-pocket); the transforms below merge lists as needed.
    """

    def __init__(self, patterns: dict[str, re.Pattern], debug_dir: Path | None):
        self.patterns = patterns
        self.debug_dir = debug_dir
        self.captured: dict[str, list[Any]] = {}
        self._debug_count = 0

    def handler(self, response) -> None:
        url = urllib.parse.urlparse(response.url)
        if API_PREFIX not in url.path:
            return
        ctype = response.headers.get("content-type", "")
        if "json" not in ctype:
            return
        try:
            body = response.json()
        except Exception:
            return

        if self.debug_dir is not None:
            self._debug_count += 1
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", url.path).strip("_")
            out = self.debug_dir / f"{self._debug_count:03d}_{response.status}_{safe}.json"
            out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
            query = f"?{url.query}" if url.query else ""
            print(f"  [debug] {response.status} {response.request.method} {url.path}{query} -> {out.name}")

        # Only successful responses count as captured data. /user/current and
        # friends return 401 on every pre-login load, so matching on status
        # avoids a false "logged in" / bogus capture.
        if response.status >= 400:
            return
        for key, pattern in self.patterns.items():
            if pattern.search(url.path):
                self.captured.setdefault(key, []).append(body)

    def wait_for(self, page, keys: tuple[str, ...], timeout_s: int) -> None:
        waited = 0.0
        while waited < timeout_s:
            if all(k in self.captured for k in keys):
                return
            page.wait_for_timeout(250)
            waited += 0.25
        missing = [k for k in keys if k not in self.captured]
        raise TimeoutError(
            f"never saw {', '.join(missing)} response(s) from the Revolut API "
            f"(page ended on {page.url}). Rerun with --debug and check "
            f"CAPTURE_PATTERNS/ROUTES against the dumped URLs."
        )


# ── Browser / session handling ─────────────────────────────────────────────────


def launch(pw, headed: bool):
    """Persistent context on PROFILE_DIR; real Chrome first (less bot-flagged),
    bundled Chromium as fallback."""
    kwargs = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=not headed,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        return pw.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except PlaywrightError:
        return pw.chromium.launch_persistent_context(**kwargs)


def is_logged_out_url(url: str) -> bool:
    """Logged-out sessions get bounced into the sign-in flow. The URL can also
    stay on /home while the SPA renders the sign-in screen, so this is only a
    fast-path check — the authoritative signal is whether authenticated API
    responses (wallet) ever arrive; see ResponseCapture.wait_for."""
    return any(part in url for part in ("/start", "/login", "/signin"))


def _await_login(page, capture) -> None:
    """Wait until the app receives an authenticated (200) retail response.

    Revolut keeps the access token out of persisted storage, so a restarted
    browser usually lands logged-out and must re-authenticate. If the session
    is still warm this returns within a second or two; otherwise it prompts and
    waits for the user to complete login in the visible window.
    """
    prompted = False
    waited = 0.0
    while "logged_in" not in capture.captured:
        if waited >= LOGIN_TIMEOUT_S:
            raise SystemExit("Timed out waiting for login — try again.")
        if not prompted and (waited >= LOGIN_GRACE_S or is_logged_out_url(page.url)):
            print("Please log in to Revolut in the browser window that opened:")
            print("  1. Enter your phone number and passcode.")
            print("  2. Approve the push notification in the Revolut phone app.")
            print(f"  (waiting up to {LOGIN_TIMEOUT_S // 60} minutes)")
            prompted = True
        page.wait_for_timeout(500)
        waited += 0.5


def close_quietly(context, timeout_s: int = 15) -> None:
    """Close the browser without ever blocking the export. Playwright's
    context.close() can stall (seen on Python 3.14); since the captured data is
    already plain JSON in hand, a hung close must not lose it — so bound it with
    an alarm and give up if it overruns."""
    def _raise(*_):
        raise TimeoutError()

    previous = signal.signal(signal.SIGALRM, _raise)
    try:
        signal.alarm(timeout_s)
        context.close()
    except Exception:
        pass
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def collect(pw, headed: bool, debug: bool, login_only: bool = False):
    """Open the app (logging in if needed) and capture wallet/savings/currencies
    in one live context — the only way to hold Revolut's in-memory session.

    Returns (context, captured, headers) — `headers` being the app's own
    request headers, replayed by fetch_pocket_transactions. The caller writes
    output first, then closes the context via close_quietly — closing before
    the file is written risks a Playwright hang losing the data."""
    debug_dir = None
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        for old in DEBUG_DIR.glob("*.json"):
            old.unlink()
        debug_dir = DEBUG_DIR

    context = launch(pw, headed=headed)
    try:
        patterns = {**CAPTURE_PATTERNS, "logged_in": LOGGED_IN_PATTERN}
        capture = ResponseCapture(patterns, debug_dir)
        page = context.pages[0] if context.pages else context.new_page()
        page.on("response", capture.handler)

        # Keep the headers of the app's own transactions call so the crypto
        # pockets can be asked for with the same credentials (auth rides on the
        # cookie plus x-device-id) instead of driving a UI that has no crypto
        # screen. First call wins: they are identical across calls.
        headers: dict[str, str] = {}

        def remember_headers(request) -> None:
            if TRANSACTIONS_ENDPOINT in request.url and not headers:
                headers.update(request.all_headers())

        page.on("request", remember_headers)

        page.goto(APP_URL + ROUTES["home"], wait_until="domcontentloaded")
        _await_login(page, capture)

        if login_only:
            page.wait_for_timeout(5000)  # let auth settle into the profile
            print(f"Logged in. Session warmed in {PROFILE_DIR.name}/")
            return context, capture.captured, headers

        print("Capturing wallet, savings and currencies…")
        capture.wait_for(page, REQUIRED_CAPTURES, CAPTURE_TIMEOUT_S)
        # money-boxes is optional but usually present; brief grace so a savings
        # account isn't missed on a slow load.
        if "money_boxes" not in capture.captured:
            try:
                capture.wait_for(page, ("money_boxes",), 8)
            except TimeoutError:
                print("  note: no savings (money-boxes) response — treating as none.")
        # The home page asks for the latest transactions right after the
        # wallet; give it a moment so the replayable headers are in hand
        # before the ledger and transaction exports need them.
        waited = 0.0
        while not headers and waited < HEADERS_GRACE_S:
            page.wait_for_timeout(250)
            waited += 0.25
        if not headers:
            print("  note: the app never requested transactions — history exports will be skipped.")
        return context, capture.captured, headers
    except BaseException:
        close_quietly(context)
        raise


def _replay_headers(headers: dict[str, str]) -> dict[str, str]:
    """Hop-by-hop and pseudo headers can't be resent; everything else (cookie,
    x-device-id, the client fingerprint) is what makes the call authentic."""
    return {
        k: v
        for k, v in headers.items()
        if not k.startswith(":") and k not in ("host", "content-length", "accept-encoding")
    }


def _leg_id(row: dict) -> str:
    return str(row.get("legId") or row.get("id") or "")


def _row_ts(row: dict) -> int:
    return int(row.get("startedDate") or row.get("createdDate") or 0)


def fetch_pocket_history(
    context,
    headers: dict[str, str],
    pocket_id: str,
    *,
    until_ms: int | None = None,
    label: str = "",
    debug_dir: Path | None = None,
) -> tuple[list[dict], str | None]:
    """Page a pocket's history backwards until it runs out.

    Returns (rows, page_param): every leg seen (de-duplicated by legId, newest
    first) and the paging parameter that worked — None when only the first
    page could be fetched. Stops early once a page reaches `until_ms` (an
    incremental run only needs what is newer than the file already has).
    """
    replay = _replay_headers(headers)
    base = f"{APP_URL}{TRANSACTIONS_ENDPOINT}?count={TRANSACTIONS_PAGE_SIZE}&internalPocketId={pocket_id}"

    def get(url: str) -> list[dict] | None:
        try:
            response = context.request.get(url, headers=replay)
            body = response.json() if response.ok else None
        except Exception as exc:
            print(f"  warning: history request failed for {label or pocket_id} ({exc})")
            return None
        if debug_dir is not None:
            n = len(list(debug_dir.glob("tx_*.json"))) + 1
            (debug_dir / f"tx_{n:03d}_{label or pocket_id}.json").write_text(
                json.dumps(body, indent=2, ensure_ascii=False) + "\n"
            )
        return [r for r in body if isinstance(r, dict)] if isinstance(body, list) else None

    seen: dict[str, dict] = {}
    first = get(base)
    if first is None:
        return [], None
    for r in first:
        seen[_leg_id(r)] = r
    # The first page is bounded by time as much as by count (it comes back
    # shorter than `count` even when older rows exist), so a short page is
    # not the end: keep asking until a page brings nothing new.
    if not first:
        return [], None

    oldest = min(_row_ts(r) for r in first)
    if until_ms is not None and oldest <= until_ms:
        return list(seen.values()), None

    page_param: str | None = None
    pages = 1
    while pages < TRANSACTIONS_MAX_PAGES:
        params = (page_param,) if page_param else TRANSACTIONS_PAGE_PARAMS
        page: list[dict] | None = None
        for candidate in params:
            page = get(f"{base}&{candidate}={oldest - 1}")
            if page and any(_leg_id(r) not in seen for r in page):
                page_param = candidate
                break
            page = None
        if page is None:
            # A first page shorter than `count` that no parameter extends is
            # simply the whole history; only a full page that won't page is
            # worth a warning.
            if page_param is None and len(first) >= TRANSACTIONS_PAGE_SIZE:
                print(
                    f"  warning: no paging parameter in {TRANSACTIONS_PAGE_PARAMS} returned "
                    f"older rows for {label or pocket_id} — only the latest "
                    f"{TRANSACTIONS_PAGE_SIZE} rows were exported. Rerun with --debug and "
                    f"scroll the transaction list to see what the app sends."
                )
            break
        pages += 1
        for r in page:
            seen.setdefault(_leg_id(r), r)
        oldest = min(oldest, min(_row_ts(r) for r in page))
        if until_ms is not None and oldest <= until_ms:
            break
        context.pages[0].wait_for_timeout(TRANSACTIONS_PAGE_PAUSE_MS) if context.pages else None
    rows = sorted(seen.values(), key=_row_ts, reverse=True)
    print(f"  {label or pocket_id}: {len(rows)} rows over {pages} page(s)")
    return rows, page_param


def fetch_pocket_transactions(
    context, headers: dict[str, str], pockets: list[dict]
) -> list[dict]:
    """Replay the app's transactions call once per pocket, returning every row.

    Rows come back per leg, and the same leg appears in both pockets it touches,
    so the caller must de-duplicate — see build_crypto_ledger.
    """
    replay = _replay_headers(headers)
    rows: list[dict] = []
    for pocket in pockets:
        url = (
            f"{APP_URL}{TRANSACTIONS_ENDPOINT}"
            f"?count={TRANSACTIONS_PAGE_SIZE}&internalPocketId={pocket['id']}"
        )
        try:
            response = context.request.get(url, headers=replay)
            body = response.json() if response.ok else None
        except Exception as exc:
            print(f"  warning: no history for the {pocket['currency']} pocket ({exc})")
            continue
        if not isinstance(body, list):
            print(f"  warning: unexpected history shape for the {pocket['currency']} pocket")
            continue
        rows.extend(r for r in body if isinstance(r, dict))
    return rows


# ── Currency metadata (from /api/retail/currencies) ────────────────────────────
#
# Each entry: {"isoCode","exponent","isCrypto","isCommodity","name",...}. The
# endpoint is called several times with different sets (fiat / crypto / others);
# we merge them all. `exponent` is the minor-unit scale: EUR=2, most crypto=8.


class Currencies:
    def __init__(self, bodies: list[Any]):
        self.by_code: dict[str, dict] = {}
        for body in bodies:
            for c in body if isinstance(body, list) else []:
                if isinstance(c, dict) and c.get("isoCode"):
                    self.by_code.setdefault(c["isoCode"], c)

    def exponent(self, code: str) -> int:
        return int(self.by_code.get(code, {}).get("exponent", 2))

    def is_crypto(self, code: str) -> bool:
        return bool(self.by_code.get(code, {}).get("isCrypto"))

    def is_commodity(self, code: str) -> bool:
        return bool(self.by_code.get(code, {}).get("isCommodity"))

    def name(self, code: str) -> str:
        return self.by_code.get(code, {}).get("name", code)

    def amount(self, minor: Any, code: str) -> float:
        """Minor units (e.g. cents, satoshis) → whole units."""
        return float(minor) / (10 ** self.exponent(code))


# ── Crypto pricing (Coinbase public spot, keyless) ─────────────────────────────

# Crypto is quoted in USD by convention — that is the number every price site
# shows — while this portfolio's base currency is
# EUR. The two sit ~13% apart, so each position carries both: `lastPrice` in
# QUOTE_CURRENCY for display next to the USD cost basis, and `lastPriceEur` for
# the portfolio maths.
QUOTE_CURRENCY = "USD"

_price_cache: dict[tuple[str, str], float | None] = {}


def spot_price(base: str, quote: str = "EUR") -> float | None:
    """Coinbase spot price of one `base` unit in `quote`, or None when the pair
    is unavailable (the position is then reported without a value in it).

    Coinbase quotes fiat pairs too (GBP-EUR, USD-EUR), which is what the cash
    and cost-basis conversions below rely on.
    """
    if base == quote:
        return 1.0
    key = (base, quote)
    if key in _price_cache:
        return _price_cache[key]
    pair = f"{urllib.parse.quote(base)}-{urllib.parse.quote(quote)}"
    url = f"https://api.coinbase.com/v2/prices/{pair}/spot"
    price: float | None = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stocks-exporter"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            price = float(json.loads(resp.read())["data"]["amount"])
    except Exception as exc:
        print(f"  warning: no {quote} price for {base} ({exc}); reporting unvalued.")
    _price_cache[key] = price
    return price


def crypto_price_eur(symbol: str) -> float | None:
    """Spot price of `symbol` in EUR. Kept as a named wrapper because
    export_dividends.py imports it for its own FX conversions."""
    return spot_price(symbol, "EUR")


def _round_price(price: float) -> float:
    """Round a unit price for output. Sub-euro coins would lose all their
    significant digits at 2 decimals, so scale the precision with the price."""
    if abs(price) >= 1:
        return round(price, 2)
    return round(price, 8)


# Not _price_cache: that one is symbol-keyed and shared with fiat_to_eur.
_dated_price_cache: dict[tuple[str, str], float | None] = {}


def crypto_price_eur_at(symbol: str, date: str) -> float | None:
    """Spot EUR price of `symbol` on a past UTC date (YYYY-MM-DD), via the same
    Coinbase endpoint with a `date` param; None when unavailable."""
    key = (symbol, date)
    if key in _dated_price_cache:
        return _dated_price_cache[key]
    url = f"https://api.coinbase.com/v2/prices/{urllib.parse.quote(symbol)}-EUR/spot?date={date}"
    price: float | None = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stocks-exporter"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            price = float(json.loads(resp.read())["data"]["amount"])
    except Exception as exc:
        print(f"  warning: no EUR price for {symbol} on {date} ({exc}); no today P/L.")
    _dated_price_cache[key] = price
    return price


# ── Transforms: captured JSON → output shape ───────────────────────────────────


# The app re-fetches wallet/money-boxes several times per load; only the last
# (freshest) response should be counted, or balances multiply.
def _latest(bodies: list[Any]) -> Any:
    return bodies[-1] if bodies else None


def _iter_pockets(captured: dict[str, list[Any]]):
    wallet = _latest(captured.get("wallet", []))
    pockets = wallet.get("pockets") if isinstance(wallet, dict) else None
    for p in pockets or []:
        if isinstance(p, dict):
            yield p


# Fiat and crypto pockets are both type CURRENT — the currency's isCrypto flag
# is what separates them, not the pocket type.
def build_cash(captured: dict[str, list[Any]], ccy: Currencies) -> list[dict]:
    cash: list[dict] = []

    # Fiat pockets in the main wallet (current-account balances, one per currency).
    for p in _iter_pockets(captured):
        if p.get("state") != "ACTIVE" or p.get("closed"):
            continue
        code = p.get("currency", "")
        if not code or ccy.is_crypto(code) or ccy.is_commodity(code):
            continue
        amount = ccy.amount(p.get("balance", 0), code)
        if amount == 0:
            continue
        cash.append(
            {
                "id": str(p.get("id", "")),
                "name": f"{code} account",
                "kind": "current",
                "currency": code,
                "amount": round(amount, 2),
                "amountEur": round(fiat_to_eur(amount, code), 2),
            }
        )

    # Savings / "Boosted" accounts (money-boxes): balance is {amount, currency}.
    for mb in _latest(captured.get("money_boxes", [])) or []:
        if isinstance(mb, dict) and mb.get("state") == "ACTIVE":
            bal = mb.get("balance") or {}
            code = bal.get("currency", "")
            if not code:
                continue
            amount = ccy.amount(bal.get("amount", 0), code)
            if amount == 0:
                continue
            entry = {
                "id": str(mb.get("id", "")),
                "name": mb.get("name") or f"{code} savings",
                "kind": "savings",
                "currency": code,
                "amount": round(amount, 2),
                "amountEur": round(fiat_to_eur(amount, code), 2),
            }
            # The deposit block is where the rate lives (gross NIR and AER)
            # together with what the account has paid since it was opened.
            entry.update(_savings_terms(mb, ccy))
            cash.append(entry)

    return cash


def fiat_to_eur(amount: float, code: str) -> float:
    if code == "EUR":
        return amount
    rate = crypto_price_eur(code)  # Coinbase also quotes fiat pairs (GBP-EUR, …)
    return amount * rate if rate else amount


def load_cost_basis() -> dict[str, dict]:
    """Manual average buy prices from crypto_cost_basis.json, keyed by symbol
    (e.g. {"BTC": {"avgPrice": 60000, "currency": "USD"}}). Missing/broken file
    just means no cost basis — positions are then reported without P&L."""
    if not COST_BASIS_FILE.exists():
        return {}
    try:
        data = json.loads(COST_BASIS_FILE.read_text())
    except Exception as exc:
        print(f"  warning: could not read {COST_BASIS_FILE.name} ({exc}); skipping cost basis.")
        return {}
    return {
        sym: entry
        for sym, entry in data.items()
        if isinstance(entry, dict) and isinstance(entry.get("avgPrice"), (int, float))
    }


# ── Crypto ledger (per-pocket transaction history) ─────────────────────────────
#
# Amounts are in minor units like everywhere else, and each transaction is
# stored per *leg* — a Revolut X trade is two rows, one per side, sharing a
# `groupKey` and even an `id`; only `legId` tells them apart. The shapes that
# matter, all with a `state` that is CANCELLED for orders that never filled:
#
#   REVX_EXCHANGE  a Revolut X trade. The fiat leg carries the gross amount and
#                  the fee; the crypto leg's `counterpart.amount` is the *net*
#                  proceeds, which is why the fiat leg is the one read here —
#                  tax law wants the gross and the fee apart, not netted.
#   TOPUP          coins arriving from an outside wallet (the Bybit transfer).
#                  Moving your own coins is not an acquisition, so this is
#                  recorded as a deposit: the cost basis stays with the
#                  original purchase, wherever that was.
#   REWARD         a staking payout, credited in the STAKING pocket.
#   TRANSFER       internal staking/unstaking moves — no tax event.
#   REVX_TRANSFER  fiat shuffling between the current and Revolut X accounts.
#
# Anything unrecognised is counted in `ignoredTypes` rather than dropped in
# silence, so a shape change shows up as a number instead of a missing sale.
LEDGER_IGNORED_TYPES = ("TRANSFER", "REVX_TRANSFER")


def _ledger_date(row: dict) -> str:
    """Row timestamp as a local-time ISO string — the moment that goes on the
    tax return, in the timezone the trade was made in."""
    millis = row.get("startedDate") or row.get("createdDate") or 0
    return (
        datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )


def build_crypto_ledger(rows: list[dict], ccy: Currencies) -> dict:
    """Normalize raw per-pocket transactions into trades, deposits and rewards."""
    # The same leg comes back from every pocket it touches; `legId` is the only
    # per-leg identifier (both legs of a trade share `id`).
    legs: dict[str, dict] = {}
    for row in rows:
        leg_id = row.get("legId") or row.get("id")
        if leg_id:
            legs[leg_id] = row

    groups: dict[str, list[dict]] = {}
    for leg in legs.values():
        groups.setdefault(leg.get("groupKey") or leg.get("id") or "", []).append(leg)

    trades: list[dict] = []
    deposits: list[dict] = []
    rewards: list[dict] = []
    ignored: dict[str, int] = {}

    for group in groups.values():
        kind = group[0].get("type")
        completed = [leg for leg in group if leg.get("state") == "COMPLETED"]
        if kind in LEDGER_IGNORED_TYPES:
            continue
        if not completed:
            # A cancelled order never moved anything; counting it as a disposal
            # would invent a sale that never happened.
            ignored[f"{kind}:not-completed"] = ignored.get(f"{kind}:not-completed", 0) + 1
            continue

        if kind == "REVX_EXCHANGE":
            crypto = next((l for l in completed if ccy.is_crypto(l.get("currency", ""))), None)
            fiat = next((l for l in completed if not ccy.is_crypto(l.get("currency", ""))), None)
            if crypto is None or fiat is None:
                ignored["REVX_EXCHANGE:incomplete"] = (
                    ignored.get("REVX_EXCHANGE:incomplete", 0) + 1
                )
                continue
            symbol = crypto["currency"]
            quantity = abs(ccy.amount(crypto.get("amount", 0), symbol))
            gross = abs(ccy.amount(fiat.get("amount", 0), fiat["currency"]))
            fee = abs(ccy.amount(fiat.get("fee", 0), fiat["currency"]))
            trades.append(
                {
                    "id": crypto.get("id"),
                    "platform": "Revolut",
                    "date": _ledger_date(crypto),
                    "side": "sell" if crypto.get("amount", 0) < 0 else "buy",
                    "symbol": symbol,
                    "name": ccy.name(symbol),
                    "quantity": round(quantity, 10),
                    "currency": fiat["currency"],
                    # Gross first, fee apart: export_taxes.py subtracts the fee
                    # itself to reach the disposal value.
                    "grossAmount": round(gross, 2),
                    "feeAmount": round(fee, 2),
                    "price": _round_price(gross / quantity) if quantity else None,
                    "description": crypto.get("description"),
                }
            )
        elif kind == "TOPUP":
            for leg in completed:
                symbol = leg.get("currency", "")
                if not ccy.is_crypto(symbol):
                    continue
                deposits.append(
                    {
                        "id": leg.get("id"),
                        "platform": "Revolut",
                        "date": _ledger_date(leg),
                        "symbol": symbol,
                        "quantity": round(ccy.amount(leg.get("amount", 0), symbol), 10),
                        "method": leg.get("topupMethod"),
                    }
                )
        elif kind == "REWARD":
            for leg in completed:
                symbol = leg.get("currency", "")
                if not ccy.is_crypto(symbol):
                    continue
                rewards.append(
                    {
                        "id": leg.get("id"),
                        "platform": "Revolut",
                        "date": _ledger_date(leg),
                        "symbol": symbol,
                        "quantity": round(ccy.amount(leg.get("amount", 0), symbol), 10),
                    }
                )
        else:
            ignored[str(kind)] = ignored.get(str(kind), 0) + len(group)

    by_date = lambda entries: sorted(entries, key=lambda e: e["date"], reverse=True)
    return {
        "trades": by_date(trades),
        "deposits": by_date(deposits),
        "rewards": by_date(rewards),
        "ignoredTypes": ignored,
    }


def build_positions(captured: dict[str, list[Any]], ccy: Currencies) -> list[dict]:
    # Aggregate crypto across pockets of the same currency (a coin can sit in
    # both a CURRENT and a STAKING pocket).
    qty_by_symbol: dict[str, float] = {}
    for p in _iter_pockets(captured):
        if p.get("state") != "ACTIVE" or p.get("closed"):
            continue
        code = p.get("currency", "")
        if not code or not ccy.is_crypto(code):
            continue
        qty = ccy.amount(p.get("balance", 0), code)
        if qty > 0:
            qty_by_symbol[code] = qty_by_symbol.get(code, 0.0) + qty

    cost_basis = load_cost_basis()
    # Crypto trades 24/7, so "previous close" is a convention: the spot price
    # at the previous UTC day (DEGIRO's todayPl uses exchange previous close).
    prev_day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    positions: list[dict] = []
    for symbol, quantity in qty_by_symbol.items():
        price = crypto_price_eur(symbol)
        value_eur = round(price * quantity, 2) if price is not None else None
        prev_price = crypto_price_eur_at(symbol, prev_day) if price is not None else None
        # The quote-currency (USD) price is display-only: the Holdings table
        # quotes crypto in USD, the way every price site does.
        quote_price = spot_price(symbol, QUOTE_CURRENCY)
        pos = {
            "symbol": symbol,
            "name": ccy.name(symbol),
            "quantity": quantity,
            "currency": "EUR",
            "lastPrice": _round_price(quote_price) if quote_price is not None else None,
            "priceCurrency": QUOTE_CURRENCY,
            "lastPriceEur": _round_price(price) if price is not None else None,
            "valueEur": value_eur,
            "todayPlEur": (
                round(quantity * (price - prev_price), 2) if prev_price is not None else None
            ),
        }
        # Fold in a manual cost basis when one is configured: cost is the
        # current quantity at the average unit price (avg-cost method),
        # converted to EUR so P&L is comparable with DEGIRO's EUR figures.
        entry = cost_basis.get(symbol)
        if entry is not None:
            avg_price = float(entry["avgPrice"])
            avg_ccy = str(entry.get("currency", "EUR")).upper()
            rate = spot_price(avg_ccy, "EUR")
            if rate:
                cost_eur = round(quantity * avg_price * rate, 2)
                pos["avgPrice"] = avg_price
                pos["avgPriceCurrency"] = avg_ccy
                pos["costEur"] = cost_eur
                pos["plEur"] = None if value_eur is None else round(value_eur - cost_eur, 2)
            else:
                print(f"  warning: no {avg_ccy}->EUR rate for {symbol} cost basis; reporting without P&L.")
        positions.append(pos)

    positions.sort(key=lambda p: -(p["valueEur"] or 0))
    return positions


# ── Account transactions (spending, income, savings interest) ──────────────────
#
# The EUR current pocket's history is the bank statement: card payments,
# transfers in and out, Bizum, top-ups, and one leg of every internal move (to
# the Boosted account, to Revolut X, into crypto). The Boosted account's own
# pocket is where the daily interest is credited. Both are paged in full with
# fetch_pocket_history; what comes out is one normalized row per leg plus the
# monthly aggregates the web app charts.
#
# Kinds, in the order the rules are tried (state != COMPLETED is skipped first):
#   interest  a savings interest credit
#   transfer  money moved between your own accounts — Boosted, Revolut X,
#             crypto, currency exchanges and deposits to your broker. Excluded
#             from spending and income.
#   fee       an explicit fee row, or the fee carried by another row (emitted
#             as its own synthetic row so spending stays gross)
#   expense   a card payment or an outgoing transfer to someone else
#   refund    a card refund (subtracts from that category's spending)
#   income    a top-up, Bizum or transfer received
#   other     anything unrecognised — counted in `ignoredTypes`, never dropped
INTEREST_TYPES = ("INTEREST", "SAVINGS_INTEREST", "SAVINGS_INTEREST_PAYMENT")
INTERNAL_TRANSACTION_TYPES = ("REVX_TRANSFER", "EXCHANGE", "REVX_EXCHANGE")
# Outgoing transfers whose description names one of these are money moved to
# your own broker, not spent. Lower-case substrings; extend as you add brokers.
TRANSFER_INTERNAL_COUNTERPARTS = ("stichting degiro", "degiro b.v.")


def _own_names(captured: dict[str, list[Any]]) -> set[str]:
    """Lower-cased spellings of the account holder's name (from /user/current):
    "first last" and "last first". Empty when the response wasn't captured."""
    body = _latest(captured.get("user", []))
    user = body.get("user") if isinstance(body, dict) else None
    if not isinstance(user, dict):
        return set()
    first = " ".join(str(user.get("firstName") or "").split()).lower()
    last = " ".join(str(user.get("lastName") or "").split()).lower()
    names = set()
    if first and last:
        names.update({f"{first} {last}", f"{last} {first}"})
    return names


def _current_pocket(captured: dict[str, list[Any]], ccy: Currencies) -> dict | None:
    for p in _iter_pockets(captured):
        if p.get("type") == "CURRENT" and p.get("currency") == "EUR":
            return p
    return None


def _savings_account(captured: dict[str, list[Any]]) -> dict | None:
    """The first active money-box (the Boosted account)."""
    for mb in _latest(captured.get("money_boxes", [])) or []:
        if isinstance(mb, dict) and mb.get("state") == "ACTIVE":
            return mb
    return None


def _savings_pocket_ids(mb: dict | None) -> list[str]:
    """Candidate pocket ids for the savings history, most likely first: the
    deposit's pocket (the id the current account's SAVINGS_EXTERNAL legs point
    at), then the savings account id, then the money-box id."""
    if not mb:
        return []
    deposit = mb.get("deposit") or {}
    ids = [deposit.get("pocketId"), mb.get("savingsAccountId"), mb.get("id")]
    out: list[str] = []
    for i in ids:
        if i and str(i) not in out:
            out.append(str(i))
    return out


def _savings_terms(mb: dict, ccy: Currencies) -> dict:
    deposit = mb.get("deposit") or {}
    rates = deposit.get("rates") or {}
    earned = deposit.get("earnedInTotal") or {}
    out: dict[str, Any] = {}
    if isinstance(deposit.get("interestRate"), (int, float)):
        out["interestRate"] = float(deposit["interestRate"])
    if isinstance(rates.get("annualEffectiveRatePercentage"), (int, float)):
        out["aer"] = float(rates["annualEffectiveRatePercentage"])
    if isinstance(earned.get("amount"), (int, float)) and earned.get("currency") and earned["amount"]:
        code = earned["currency"]
        out["earnedInTotalEur"] = round(fiat_to_eur(ccy.amount(earned["amount"], code), code), 2)
    return out


def _description_key(row: dict) -> str:
    loc = row.get("localisedDescription")
    return str(loc.get("key", "")).lower() if isinstance(loc, dict) else ""


def classify_transaction(
    row: dict, *, own_pocket_ids: set[str], own_names: set[str] = frozenset()
) -> str | None:
    """Kind of a leg (see the table above), or None for a row to skip."""
    if row.get("state") != "COMPLETED":
        return None
    kind = str(row.get("type") or "")
    key = _description_key(row)
    amount = row.get("amount") or 0
    description = " ".join(str(row.get("description") or "").split()).lower()
    account = row.get("account") or {}

    # Card verifications and temporary holds settle at zero: nothing moved.
    if amount == 0 and not row.get("fee"):
        return None

    category = str(row.get("category") or row.get("tag") or "").lower()
    # Boosted interest arrives as a TRANSFER on the savings pocket with the key
    # vaults.saving_accounts.interest.gained and category "interest".
    if kind in INTEREST_TYPES or "interest" in key or "interest" in kind.lower() or category == "interest":
        return "interest"
    # Plan/subscription fees are CHARGE rows (transaction.description.fee.plan.subscription).
    if kind in ("FEE", "CHARGE") or ".fee." in key:
        return "fee"
    if kind in INTERNAL_TRANSACTION_TYPES or ".arrow." in key or key.endswith("arrow"):
        return "transfer"
    if account.get("type") == "SAVINGS_EXTERNAL":
        return "transfer"
    counterpart_account = row.get("counterpartAccount") or {}
    if str(counterpart_account.get("id", "")) in own_pocket_ids:
        return "transfer"
    if category == "crypto":
        return "transfer"
    if any(name in description for name in TRANSFER_INTERNAL_COUNTERPARTS):
        return "transfer"
    if kind in ("TOPUP", "TRANSFER"):
        payer = " ".join(str(row.get("payerName") or "").split()).lower()
        if any(name in description or (payer and name in payer) for name in own_names):
            return "transfer"
    if kind == "CARD_PAYMENT":
        return "expense" if amount < 0 else "refund"
    if kind == "CARD_REFUND":
        return "refund"
    if kind == "TOPUP":
        return "income" if amount > 0 else "other"
    if kind == "TRANSFER":
        return "expense" if amount < 0 else "income"
    return "other"


def _category(row: dict, kind: str) -> str:
    if kind == "interest":
        return "interest"
    if kind == "fee":
        return "fees"
    merchant = row.get("merchant") or {}
    raw = row.get("category") or row.get("tag") or merchant.get("category") or "other"
    return str(raw).strip().lower().replace(" ", "_") or "other"


def normalize_transaction(row: dict, ccy: Currencies, kind: str, account_kind: str) -> dict:
    code = str(row.get("currency") or "EUR")
    amount = ccy.amount(row.get("amount", 0), code)
    fee = ccy.amount(row.get("fee", 0), code) if row.get("fee") else 0.0
    if kind == "fee" and amount == 0 and fee > 0:
        amount, fee = -fee, 0.0
    merchant = row.get("merchant") or {}
    card = row.get("card") or {}
    counterpart = row.get("counterpart") or {}
    balance = row.get("balance")
    out: dict[str, Any] = {
        "id": str(row.get("id") or ""),
        "legId": _leg_id(row),
        "ts": _row_ts(row),
        "date": _ledger_date(row),
        "type": row.get("type"),
        "kind": kind,
        "category": _category(row, kind),
        "merchant": merchant.get("name") or None,
        "description": row.get("description") or None,
        "amount": round(amount, 2),
        "amountEur": round(fiat_to_eur(amount, code), 2),
        "fee": round(fee, 2),
        "currency": code,
        "account": account_kind,
    }
    if card.get("lastFour"):
        out["cardLastFour"] = str(card["lastFour"])
    if merchant.get("city"):
        out["city"] = merchant["city"]
    if merchant.get("country"):
        out["country"] = merchant["country"]
    if isinstance(counterpart.get("amount"), (int, float)) and counterpart.get("currency"):
        cc = counterpart["currency"]
        out["counterpart"] = {"amount": round(ccy.amount(counterpart["amount"], cc), 2), "currency": cc}
    if isinstance(balance, (int, float)):
        out["balance"] = round(ccy.amount(balance, code), 2)
    return out


def build_transactions(
    current_rows: list[dict],
    savings_rows: list[dict],
    ccy: Currencies,
    *,
    current_pocket_id: str,
    own_pocket_ids: set[str],
    own_names: set[str] = frozenset(),
) -> tuple[list[dict], dict[str, int]]:
    """Normalize both pockets' legs into one list, newest first.

    The current pocket returns both legs of an internal move (they share `id`,
    differ in `legId`); only the leg booked on the pocket itself is kept, so a
    move to the Boosted account is one `transfer` row, not two. From the savings
    pocket only the interest credits are taken — its deposits and withdrawals
    are the other side of moves the current account already shows.
    """
    ignored: dict[str, int] = {}
    txs: list[dict] = []
    seen: set[str] = set()

    def note(kind: str, reason: str) -> None:
        k = f"{kind}:{reason}"
        ignored[k] = ignored.get(k, 0) + 1

    for row in current_rows:
        leg = _leg_id(row)
        if leg in seen:
            continue
        seen.add(leg)
        account = row.get("account") or {}
        if account.get("id") and str(account["id"]) != current_pocket_id:
            # The far leg of an internal move (booked on the Boosted / Revolut X
            # side); the near leg, which the same page also carries, is the one
            # kept.
            continue
        kind = classify_transaction(row, own_pocket_ids=own_pocket_ids, own_names=own_names)
        if kind is None:
            note(str(row.get("type")), "skipped")
            continue
        if kind == "other":
            note(str(row.get("type")), "unrecognised")
        tx = normalize_transaction(row, ccy, kind, "current")
        txs.append(tx)
        if tx["fee"] > 0:
            txs.append(
                {
                    **tx,
                    "id": f"{tx['id']}:fee",
                    "legId": f"{tx['legId']}:fee",
                    "kind": "fee",
                    "category": "fees",
                    "amount": -tx["fee"],
                    "amountEur": -round(fiat_to_eur(tx["fee"], tx["currency"]), 2),
                    "fee": 0.0,
                }
            )

    for row in savings_rows:
        leg = _leg_id(row)
        if leg in seen:
            continue
        seen.add(leg)
        kind = classify_transaction(row, own_pocket_ids=own_pocket_ids, own_names=own_names)
        if kind != "interest":
            if kind is not None:
                note(f"savings:{row.get('type')}", "not-interest")
            continue
        txs.append(normalize_transaction(row, ccy, kind, "savings"))

    txs.sort(key=lambda t: (t["ts"], t["legId"]), reverse=True)
    return txs, ignored


def build_months(txs: list[dict]) -> list[dict]:
    months: dict[str, dict] = {}
    for tx in txs:
        m = tx["date"][:7]
        entry = months.setdefault(
            m,
            {"month": m, "expenses": 0.0, "income": 0.0, "interest": 0.0, "fees": 0.0,
             "net": 0.0, "count": 0, "byCategory": {}},
        )
        kind = tx["kind"]
        amount = tx["amountEur"]
        if kind in ("expense", "refund"):
            entry["expenses"] -= amount
            cat = tx["category"]
            entry["byCategory"][cat] = entry["byCategory"].get(cat, 0.0) - amount
            entry["count"] += 1
        elif kind == "income":
            entry["income"] += amount
            entry["count"] += 1
        elif kind == "interest":
            entry["interest"] += amount
        elif kind == "fee":
            entry["fees"] -= amount
    out = []
    for entry in months.values():
        entry["expenses"] = round(entry["expenses"], 2)
        entry["income"] = round(entry["income"], 2)
        entry["interest"] = round(entry["interest"], 2)
        entry["fees"] = round(entry["fees"], 2)
        entry["net"] = round(entry["income"] + entry["interest"] - entry["expenses"] - entry["fees"], 2)
        entry["byCategory"] = {
            k: round(v, 2)
            for k, v in sorted(entry["byCategory"].items(), key=lambda kv: -kv[1])
            if round(v, 2) != 0
        }
        out.append(entry)
    out.sort(key=lambda e: e["month"], reverse=True)
    return out


def build_interest(txs: list[dict]) -> dict:
    daily: dict[str, dict] = {}
    for tx in txs:
        if tx["kind"] != "interest":
            continue
        day = tx["date"][:10]
        entry = daily.setdefault(day, {"date": day, "amount": 0.0})
        entry["amount"] += tx["amountEur"]
        if "balance" in tx and "balanceEur" not in entry:
            entry["balanceEur"] = tx["balance"]  # rows are newest-first: keep the day's last
    by_month: dict[str, float] = {}
    for d in daily.values():
        d["amount"] = round(d["amount"], 2)
        by_month[d["date"][:7]] = by_month.get(d["date"][:7], 0.0) + d["amount"]
    days = sorted(daily.values(), key=lambda d: d["date"])
    return {
        "daily": days,
        "byMonth": [{"month": m, "amount": round(v, 2)} for m, v in sorted(by_month.items())],
        "total": round(sum(d["amount"] for d in days), 2),
    }


def _load_previous_transactions(out_file: Path) -> list[dict]:
    if not out_file.exists():
        return []
    try:
        data = json.loads(out_file.read_text())
        txs = data.get("transactions") if isinstance(data, dict) else None
        return [t for t in txs or [] if isinstance(t, dict) and t.get("legId") and "ts" in t]
    except Exception as exc:
        print(f"  warning: could not read the previous {out_file.name} ({exc}); refetching everything.")
        return []


def write_transactions(
    context,
    headers: dict[str, str],
    captured: dict[str, list[Any]],
    ccy: Currencies,
    out_dir: Path,
    *,
    full: bool = False,
    since: datetime | None = None,
    debug_dir: Path | None = None,
) -> None:
    """Fetch and write data/revolut_transactions.json.

    Incremental by default: rows older than what the file already holds (minus
    TRANSACTIONS_REFETCH_DAYS) are not requested again, so a routine run costs
    one or two pages. `full` refetches everything; `since` bounds the history.
    """
    if not headers:
        print("  warning: never saw the app's transactions call — no transactions exported.")
        return
    current = _current_pocket(captured, ccy)
    if not current or not current.get("id"):
        print("  warning: no EUR current pocket — no transactions exported.")
        return
    current_id = str(current["id"])
    savings = _savings_account(captured)
    savings_ids = _savings_pocket_ids(savings)
    own_ids = {str(p.get("id")) for p in _iter_pockets(captured) if p.get("id")} | set(savings_ids)

    out_file = out_dir / TRANSACTIONS_FILE
    previous = [] if full else _load_previous_transactions(out_file)
    until_ms: int | None = None
    if previous:
        newest = max(t["ts"] for t in previous)
        until_ms = newest - TRANSACTIONS_REFETCH_DAYS * 24 * 3600 * 1000
    if since is not None:
        since_ms = int(since.timestamp() * 1000)
        until_ms = since_ms if until_ms is None else max(until_ms, since_ms)

    print("Fetching account history…")
    current_rows, page_param = fetch_pocket_history(
        context, headers, current_id, until_ms=until_ms, label="current", debug_dir=debug_dir
    )
    savings_rows: list[dict] = []
    savings_pocket_used: str | None = None
    for pid in savings_ids:
        rows, _ = fetch_pocket_history(
            context, headers, pid, until_ms=until_ms, label="savings", debug_dir=debug_dir
        )
        if rows:
            savings_rows, savings_pocket_used = rows, pid
            break

    txs, ignored = build_transactions(
        current_rows, savings_rows, ccy,
        current_pocket_id=current_id, own_pocket_ids=own_ids, own_names=_own_names(captured),
    )
    if since is not None:
        txs = [t for t in txs if t["ts"] >= int(since.timestamp() * 1000)]

    # Merge with what the file already had: a refetched leg replaces its old
    # copy, everything older than the refetch window is kept as it was.
    merged: dict[str, dict] = {t["legId"]: t for t in previous}
    new_count = sum(1 for t in txs if t["legId"] not in merged)
    for t in txs:
        merged[t["legId"]] = t
    all_txs = sorted(merged.values(), key=lambda t: (t["ts"], t["legId"]), reverse=True)

    months = build_months(all_txs)
    interest = build_interest(all_txs)
    accounts: dict[str, Any] = {"current": {"id": current_id, "currency": current.get("currency", "EUR")}}
    if savings:
        bal = savings.get("balance") or {}
        code = bal.get("currency") or "EUR"
        created = savings.get("createdDate")
        accounts["savings"] = {
            "id": savings_pocket_used or (savings_ids[0] if savings_ids else None),
            "name": savings.get("name") or "Savings",
            "since": (
                datetime.fromtimestamp(created / 1000, tz=timezone.utc).astimezone().date().isoformat()
                if isinstance(created, (int, float)) else None
            ),
            "balanceEur": round(fiat_to_eur(ccy.amount(bal.get("amount", 0), code), code), 2),
            **_savings_terms(savings, ccy),
        }

    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Revolut retail API (current + savings account history)",
        "currency": "EUR",
        "fromDate": all_txs[-1]["date"][:10] if all_txs else None,
        "toDate": all_txs[0]["date"][:10] if all_txs else None,
        "pageParam": page_param,
        "accounts": accounts,
        "transactions": all_txs,
        "months": months,
        "interest": interest,
        "ignoredTypes": ignored,
    }
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {out_file}")
    print(f"  {len(all_txs)} rows ({new_count} new), {len(months)} month(s), "
          f"{payload['fromDate']} → {payload['toDate']}")
    if months:
        latest = months[0]
        top = ", ".join(f"{k} €{v:,.2f}" for k, v in list(latest["byCategory"].items())[:4])
        print(f"  {latest['month']}: spent €{latest['expenses']:,.2f}, income €{latest['income']:,.2f}"
              f"{'  — ' + top if top else ''}")
    if interest["daily"]:
        print(f"  interest: €{interest['total']:.2f} over {len(interest['daily'])} day(s)")
    elif savings:
        print("  no interest rows found on the savings pocket — rerun with --debug and inspect tx_*_savings.json")
    if ignored:
        print(f"  skipped: {ignored}")


# ── Main ───────────────────────────────────────────────────────────────────────


def write_crypto_ledger(context, headers, captured, ccy: Currencies, out_dir: Path) -> None:
    """Fetch and write data/revolut_trades.json.

    Pockets are not filtered by state: a closed pocket still holds the history
    of what went through it, which is exactly what a full exit leaves behind.
    Revolut X fiat pockets are queried too — a trade's legs are visible from
    both sides, so they cover a crypto pocket that has since disappeared.
    """
    if not headers:
        print("  warning: never saw the app's transactions call — no ledger exported.")
        return

    pockets = [
        p
        for p in _iter_pockets(captured)
        if p.get("id")
        and (ccy.is_crypto(p.get("currency", "")) or str(p.get("type", "")).startswith("REVX"))
    ]
    if not pockets:
        print("  no crypto pockets — no ledger to export.")
        return

    rows = fetch_pocket_transactions(context, headers, pockets)
    ledger = build_crypto_ledger(rows, ccy)
    # Coins sent in from another wallet are money in, not return: history.py
    # counts them at what they were worth on arrival.
    for d in ledger["deposits"]:
        price = crypto_price_eur_at(d["symbol"], d["date"][:10])
        d["valueEur"] = round(d["quantity"] * price, 2) if price is not None else None
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Revolut retail API (per-pocket transaction history)",
        "pockets": [{"id": p["id"], "currency": p.get("currency"), "type": p.get("type")} for p in pockets],
        **ledger,
    }
    out_file = out_dir / "revolut_trades.json"
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print(f"Wrote {out_file}")
    for t in ledger["trades"]:
        fee = f" fee {t['feeAmount']:.2f}" if t["feeAmount"] else ""
        print(
            f"  {t['side']:<4} {t['symbol']:<5} {t['quantity']:>14.8f} @ "
            f"{t['price']:>12,.2f} {t['currency']} = {t['grossAmount']:>10,.2f}{fee}"
            f"  ({t['date'][:10]})"
        )
    if ledger["deposits"]:
        print(f"  {len(ledger['deposits'])} deposits (transfers in, not acquisitions)")
    if ledger["rewards"]:
        total = {}
        for r in ledger["rewards"]:
            total[r["symbol"]] = total.get(r["symbol"], 0) + r["quantity"]
        summary = ", ".join(f"{v:.8f} {k}" for k, v in total.items())
        print(f"  {len(ledger['rewards'])} staking rewards: {summary}")
    if ledger["ignoredTypes"]:
        print(f"  skipped: {ledger['ignoredTypes']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory containing the data/ folder (default: current dir)",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Only establish/refresh the session (no export), then exit",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run with no visible window (usually blocked by Cloudflare; expert use)",
    )
    parser.add_argument("--debug", action="store_true", help=f"Dump retail API responses to {DEBUG_DIR.name}/")
    parser.add_argument(
        "--from",
        dest="since",
        metavar="YYYY-MM-DD",
        help="Earliest transaction date to keep (default: everything Revolut returns)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=f"Refetch the whole transaction history instead of only what is newer than {TRANSACTIONS_FILE}",
    )
    parser.add_argument(
        "--no-transactions",
        action="store_true",
        help="Skip the account transactions / interest export",
    )
    args = parser.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d").astimezone() if args.since else None

    # Headed by default: Revolut fronts the web app with Cloudflare, which serves
    # a bot challenge to headless browsers. The window opens on its own; if the
    # session is warm no interaction is needed, otherwise complete the login in it.
    pw = sync_playwright().start()

    if args.login:
        context, _, _ = collect(pw, headed=not args.headless, debug=args.debug, login_only=True)
        close_quietly(context)
        _hard_exit()

    context, captured, headers = collect(pw, headed=not args.headless, debug=args.debug)

    ccy = Currencies(captured.get("currencies", []))
    cash = build_cash(captured, ccy)
    positions = build_positions(captured, ccy)
    total_cash_eur = round(sum(c["amountEur"] for c in cash), 2)

    out_dir = Path(args.output_dir).resolve() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "revolut.json"

    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Revolut web app via Playwright",
        "baseCurrency": "EUR",
        "totalCashEur": total_cash_eur,
        "totalTodayPlEur": round(
            sum(p["todayPlEur"] for p in positions if p.get("todayPlEur") is not None), 2
        ),
        "cash": cash,
        "positions": positions,
    }
    # Write before closing the browser: close can hang, and losing the data
    # after a successful capture would be the worst outcome.
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print(f"Wrote {out_file}")

    # The ledger is a second, independent output: it answers "what did I sell
    # and what did it make", which the snapshot above structurally cannot. A
    # failure here must never cost us the snapshot that already succeeded.
    try:
        write_crypto_ledger(context, headers, captured, ccy, out_dir)
    except Exception as exc:
        print(f"  warning: could not export the crypto ledger ({exc})", file=sys.stderr)

    # Third output, same rule: the bank statement and the savings interest.
    if not args.no_transactions:
        try:
            write_transactions(
                context, headers, captured, ccy, out_dir,
                full=args.full, since=since, debug_dir=DEBUG_DIR if args.debug else None,
            )
        except Exception as exc:
            print(f"  warning: could not export the transactions ({exc})", file=sys.stderr)

    for c in cash:
        print(f"  cash   {c['kind']:<8} {c['currency']} {c['amount']:>12.2f}  (€{c['amountEur']:.2f})")
    for p in positions:
        px = f"{p['lastPrice']:,.8g} {p['priceCurrency']}" if p["lastPrice"] is not None else "?"
        val = f"€{p['valueEur']:.2f}" if p["valueEur"] is not None else "unvalued"
        pl = f"  P/L €{p['plEur']:+.2f}" if p.get("plEur") is not None else ""
        today = f"  today €{p['todayPlEur']:+.2f}" if p.get("todayPlEur") is not None else ""
        print(f"  crypto {p['symbol']:<8} {p['quantity']:>12.8g} @ {px:<16} value {val}{pl}{today}")
    print(f"  total cash: €{total_cash_eur:.2f}")
    if not cash and not positions:
        print("  (nothing found — check --debug output)")

    # Must run before _hard_exit (os._exit skips everything pending); a
    # history failure must never fail an export that already succeeded.
    try:
        record_snapshot(out_dir)
    except Exception as exc:
        print(f"  warning: could not update history.json ({exc})", file=sys.stderr)

    close_quietly(context)
    _hard_exit()


def _hard_exit() -> None:
    """Exit without running Playwright/interpreter shutdown, which can itself
    hang after the browser is closed. Output is already flushed and written."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(
            f"Revolut export failed: {exc}\n"
            f"  If the session expired, rerun with --login. If Revolut changed\n"
            f"  their internal API, rerun with --debug and update CAPTURE_PATTERNS."
        )
