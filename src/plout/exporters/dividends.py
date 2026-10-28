"""
Export DEGIRO dividend history to data/dividends.json.

Reuses degiro.py's login (connect) and product metadata lookup, then
pulls the account cash statement over a date range and keeps only the
dividend movements. DEGIRO records a dividend as a positive "Dividend" cash
movement plus (usually) a negative withholding-tax movement on the same
product and day, so we group those into a single payment with gross, tax and
net figures.

Each payment carries both its native amount (USD for US stocks, EUR for Xetra
ETFs) and an EUR figure converted at the payment date via Coinbase's public
USD-EUR spot (reused from revolut.py), so the web page can total in EUR.

Also pulls DEGIRO's own get_upcoming_payments (announced but not-yet-paid
dividends/coupons) into `upcoming` — the next pay dates — best-effort so a
failure there never loses the historical export.

Usage:
  plout dividends [--from YYYY-MM-DD] [--output-dir DIR] [--debug]
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from degiro_connector.trading.models.account import OverviewRequest

from plout.config import PROJECT_ROOT
from plout.exporters.degiro import connect, fetch_product_meta
from plout.exporters.revolut import crypto_price_eur, crypto_price_eur_at

# DEGIRO localises cash-movement descriptions to the account language, so match
# a dividend by keyword rather than by an exact string. Every dividend line
# (gross or tax) contains a dividend word; the tax line additionally contains a
# tax word ("Dividend Tax", "Impuesto sobre dividendos", "Dividendbelasting").
DIVIDEND_KEYWORDS = ("dividend", "dividendo", "dividende")
TAX_KEYWORDS = (
    "tax", "belasting", "withholding",
    "retencion", "retención", "impuesto", "impot", "impôt",
)


def classify(description: str | None) -> str | None:
    """Return "gross", "tax", or None for a cash-movement description."""
    if not description:
        return None
    d = description.lower()
    if not any(k in d for k in DIVIDEND_KEYWORDS):
        return None
    return "tax" if any(k in d for k in TAX_KEYWORDS) else "gross"


def to_eur(amount: float, currency: str, day: str) -> float:
    """Convert `amount` in `currency` to EUR at the payment date `day`.

    Reuses revolut.py's Coinbase spot (which also quotes fiat pairs like
    USD-EUR). Falls back to the current rate, then to the raw amount, so a
    pricing hiccup degrades gracefully instead of dropping the payment.
    """
    if not currency or currency == "EUR" or amount == 0:
        return amount
    rate = crypto_price_eur_at(currency, day) or crypto_price_eur(currency)
    return amount * rate if rate else amount


def _day(movement: dict) -> str | None:
    """DEGIRO dates come as ISO strings; use valueDate (settlement) when set."""
    raw = movement.get("valueDate") or movement.get("date")
    if not raw:
        return None
    # e.g. "2024-03-15T00:00:00+01:00" → "2024-03-15"
    return str(raw)[:10]


def fetch_cash_movements(api, from_date: date, to_date: date) -> list[dict]:
    overview = api.get_account_overview(
        overview_request=OverviewRequest(from_date=from_date, to_date=to_date),
        raw=True,
    )
    data = overview.get("data", overview) if isinstance(overview, dict) else {}
    return data.get("cashMovements", []) or []


def fetch_upcoming(api) -> list[dict]:
    """Announced-but-unpaid dividends/coupons (DEGIRO's own 'upcoming payments').

    Best-effort: the endpoint's shape is undocumented, so normalise defensively
    and return [] on any failure rather than sinking the historical export.
    """
    try:
        raw = api.get_upcoming_payments(raw=True)
    except Exception as exc:
        print(f"  warning: could not fetch upcoming payments ({exc})", file=sys.stderr)
        return []

    if isinstance(raw, dict):
        items = raw.get("data") or raw.get("upcomingPayments") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    upcoming = []
    for it in items:
        if not isinstance(it, dict):
            continue
        pay_date = str(it.get("payDate") or it.get("pay_date") or "")[:10]
        amount = it.get("amount")
        base = it.get("amountInBaseCurr", it.get("amount_in_base_curr"))
        upcoming.append(
            {
                "payDate": pay_date or None,
                "name": it.get("product") or it.get("description") or "",
                "description": it.get("description") or "",
                "currency": it.get("currency"),
                "amount": float(amount) if amount not in (None, "") else None,
                "amountEur": float(base) if base not in (None, "") else None,
            }
        )

    upcoming.sort(key=lambda u: u["payDate"] or "9999")
    return upcoming


def build_payments(movements: list[dict], meta: dict[str, dict]) -> list[dict]:
    # Group gross/tax lines by (product, settlement day). A single dividend can
    # produce several lines (e.g. partial tax corrections), so accumulate.
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"gross": 0.0, "tax": 0.0, "currency": None}
    )

    for m in movements:
        kind = classify(m.get("description"))
        if kind is None:
            continue
        product_id = m.get("productId")
        day = _day(m)
        if product_id is None or day is None:
            continue
        change = float(m.get("change") or 0.0)
        g = groups[(str(product_id), day)]
        g[kind] += change
        # Prefer the gross line's currency (tax may be booked separately).
        if g["currency"] is None or kind == "gross":
            g["currency"] = m.get("currency")

    payments = []
    for (product_id, day), g in groups.items():
        product = meta.get(product_id, {})
        gross = round(g["gross"], 2)
        tax = round(g["tax"], 2)
        net = round(gross + tax, 2)
        currency = g["currency"] or product.get("currency") or "EUR"
        payments.append(
            {
                "date": day,
                "productId": int(product_id) if product_id.isdigit() else product_id,
                "symbol": product.get("symbol") or product_id,
                "name": product.get("name") or "",
                "currency": currency,
                "gross": gross,
                "tax": tax,
                "net": net,
                "grossEur": round(to_eur(gross, currency, day), 2),
                "taxEur": round(to_eur(tax, currency, day), 2),
                "netEur": round(to_eur(net, currency, day), 2),
            }
        )

    payments.sort(key=lambda p: p["date"], reverse=True)
    return payments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--from",
        dest="from_date",
        default="2015-01-01",
        help="Earliest date to scan (YYYY-MM-DD, default 2015-01-01)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT),
        help="Directory containing the data/ folder (default: the project root)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print the distinct (type, description) cash-movement pairs and exit",
    )
    args = parser.parse_args()

    try:
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--from must be YYYY-MM-DD, got {args.from_date!r}")
    to_date = date.today()

    out_dir = Path(args.output_dir).resolve() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "dividends.json"

    # connect() also returns the vwd quote token, which only degiro.py needs.
    api, _user_token = connect()
    movements = fetch_cash_movements(api, from_date, to_date)
    if args.debug:
        seen = sorted({(m.get("type"), m.get("description")) for m in movements})
        print(f"{len(movements)} cash movements; distinct (type, description):")
        for mtype, desc in seen:
            print(f"  [{mtype}] {desc}")
        print("\nupcoming payments (raw):")
        print(json.dumps(api.get_upcoming_payments(raw=True), indent=2, ensure_ascii=False, default=str))
        return
    dividend_ids = sorted(
        {
            str(m["productId"])
            for m in movements
            if classify(m.get("description")) and m.get("productId") is not None
        }
    )
    meta = fetch_product_meta(api, dividend_ids)
    upcoming = fetch_upcoming(api)

    payments = build_payments(movements, meta)
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "DEGIRO via degiro-connector",
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "payments": payments,
        "upcoming": upcoming,
    }

    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(payments)} dividend payments ({len(upcoming)} upcoming) to {out_file}")

    totals: dict[str, float] = defaultdict(float)
    for p in payments:
        totals[p["currency"]] += p["net"]
    for ccy, net in sorted(totals.items()):
        print(f"  net {ccy} {net:,.2f}")
    print(f"  net EUR (converted) {sum(p['netEur'] for p in payments):,.2f}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(
            f"DEGIRO dividend export failed: {exc}\n"
            f"  If login worked for `plout degiro` it should work here too;\n"
            f"  DEGIRO may have changed their API — try updating degiro-connector."
        )
