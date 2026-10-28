"""
Export DEGIRO fees and commissions to data/fees.json.

Reuses degiro.py's login and dividends.py's cash-statement
fetch, then keeps only the *cost* movements — what DEGIRO actually charged the
account, as opposed to the trades and dividends themselves.

Four categories are tracked (see FEE_CATEGORIES):
  transaction   — per-trade commission ("Costes de transacción")
  connectivity  — yearly exchange connectivity fee, per market
  currency      — AutoFX / currency-conversion commission
  other         — anything else DEGIRO books as a cost (custody, stamp duty…)

Charges are negative (money out), matching the withholding-tax convention in
dividends.py. Each carries its native amount plus an EUR figure
converted at the charge date, so the web app can total everything in EUR.

Note this deliberately does *not* read per-trade fees from the transactions
history: the cash statement is the ground truth for what left the account, and
mixing the two sources would double-count the same commission.

Usage:
  plout fees [--from YYYY-MM-DD] [--output-dir DIR] [--debug]
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from plout.config import PROJECT_ROOT
from plout.exporters.degiro import connect, fetch_product_meta
from plout.exporters.dividends import fetch_cash_movements, to_eur

# DEGIRO localises cash-movement descriptions to the account language, so every
# rule matches on keywords rather than exact strings. Keywords are lowercase and
# accent-free; descriptions are matched twice, as-is and with accents stripped,
# so "conexión"/"conexion" both hit.

# A movement only counts as a fee if its description says so. This gate is what
# keeps the plain FX conversion out: DEGIRO books each currency exchange as a
# "Cambio de Divisa (Débito)"/"(Crédito)" pair that nets to roughly zero and is
# a movement of money, not a charge on it — only the accompanying *comisión*
# line is. Same for buys/sells, deposits and withdrawals.
FEE_TOKENS = (
    "comision", "coste", "costes", "cost", "costs", "fee", "fees",
    "kosten", "tarifa", "cargo", "commission",
    "autofx",  # a fee by name, with no "commission" word attached
)
# Trading taxes: money the account loses per trade, so they belong in the total,
# but they are the state's, not DEGIRO's — they short-circuit to "other" so the
# "transaction" category stays a clean read of DEGIRO's own commission.
TAX_TOKENS = (
    "impuesto sobre transacciones financieras", "financial transaction tax",
    "stamp duty", "tasa tobin", "beursbelasting",
)
# Dividend withholding already has its own page; never double-count it here.
FEE_EXCLUSIONS = ("dividend", "dividendo", "dividende")

# Applied in order to a description already known to be a fee; first hit wins.
FEE_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "connectivity",
        (
            "conectividad", "conexion", "connectivity", "aansluitingskosten",
            "exchange connection", "connection fee",
        ),
    ),
    (
        "currency",
        (
            "autofx", "divisa", "currency conversion", "currency exchange",
            "fx fee", "valutakosten", "wisselkoers",
        ),
    ),
    (
        "transaction",
        ("transaccion", "transaction", "transactiekosten", "corretaje", "brokerage"),
    ),
]

ACCENTS = str.maketrans("áàäâãéèëêíìïîóòöôõúùüûçñ", "aaaaaeeeeiiiiooooouuuucn")


def _normalize(text: str) -> str:
    return text.lower().translate(ACCENTS)


def classify(description: str | None) -> str | None:
    """Return the fee category of a cash-movement description, or None when the
    movement is not a fee."""
    if not description:
        return None
    plain = _normalize(description)
    if any(k in plain for k in FEE_EXCLUSIONS):
        return None
    if any(k in plain for k in TAX_TOKENS):
        return "other"
    if not any(k in plain for k in FEE_TOKENS):
        return None

    for category, keywords in FEE_CATEGORIES:
        if any(k in plain for k in keywords):
            return category
    return "other"


def _day(movement: dict) -> str | None:
    """DEGIRO dates come as ISO strings; use valueDate (settlement) when set."""
    raw = movement.get("valueDate") or movement.get("date")
    return str(raw)[:10] if raw else None


def build_charges(movements: list[dict], meta: dict[str, dict]) -> list[dict]:
    charges = []
    for m in movements:
        category = classify(m.get("description"))
        if category is None:
            continue
        day = _day(m)
        amount = float(m.get("change") or 0.0)
        if day is None or amount == 0:
            continue
        # A refunded/rebated fee comes back as a positive movement; keep it, it
        # belongs in the total. Only the sign convention matters: costs < 0.
        currency = m.get("currency") or "EUR"
        product_id = m.get("productId")
        product = meta.get(str(product_id), {}) if product_id is not None else {}
        charges.append(
            {
                "date": day,
                "category": category,
                "description": m.get("description") or "",
                # Connectivity and FX fees are not tied to a product, so these
                # two are frequently null.
                "symbol": product.get("symbol"),
                "name": product.get("name"),
                "currency": currency,
                "amount": round(amount, 2),
                "amountEur": round(to_eur(amount, currency, day), 2),
            }
        )

    charges.sort(key=lambda c: c["date"], reverse=True)
    return charges


def summarize(charges: list[dict]) -> tuple[list[dict], list[dict]]:
    """(byCategory, byYear) totals — both in EUR, both sorted for display."""
    by_category: dict[str, list] = defaultdict(lambda: [0.0, 0])
    by_year: dict[str, dict] = defaultdict(
        lambda: {"amountEur": 0.0, "count": 0, "byCategory": defaultdict(float)}
    )

    for c in charges:
        amount = c["amountEur"]
        by_category[c["category"]][0] += amount
        by_category[c["category"]][1] += 1

        year = c["date"][:4]
        by_year[year]["amountEur"] += amount
        by_year[year]["count"] += 1
        by_year[year]["byCategory"][c["category"]] += amount

    categories = [
        {"category": name, "amountEur": round(total, 2), "count": count}
        for name, (total, count) in by_category.items()
    ]
    # Biggest cost first (amounts are negative, so ascending).
    categories.sort(key=lambda c: c["amountEur"])

    years = [
        {
            "year": year,
            "amountEur": round(y["amountEur"], 2),
            "count": y["count"],
            "byCategory": {k: round(v, 2) for k, v in y["byCategory"].items()},
        }
        for year, y in by_year.items()
    ]
    years.sort(key=lambda y: y["year"], reverse=True)
    return categories, years


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
        help="Print every cash-movement description with its category and exit",
    )
    args = parser.parse_args()

    try:
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--from must be YYYY-MM-DD, got {args.from_date!r}")
    to_date = date.today()

    out_dir = Path(args.output_dir).resolve() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "fees.json"

    api, _user_token = connect()
    movements = fetch_cash_movements(api, from_date, to_date)
    if args.debug:
        seen = sorted({(m.get("type"), m.get("description")) for m in movements})
        print(f"{len(movements)} cash movements; distinct (type, description):")
        for mtype, desc in seen:
            print(f"  [{classify(desc) or '—':<12}] [{mtype}] {desc}")
        return
    fee_ids = sorted(
        {
            str(m["productId"])
            for m in movements
            if classify(m.get("description")) and m.get("productId") is not None
        }
    )
    meta = fetch_product_meta(api, fee_ids)

    charges = build_charges(movements, meta)
    by_category, by_year = summarize(charges)
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "DEGIRO via degiro-connector",
        "baseCurrency": "EUR",
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "totalEur": round(sum(c["amountEur"] for c in charges), 2),
        "byCategory": by_category,
        "byYear": by_year,
        "charges": charges,
    }

    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(charges)} charges to {out_file}")
    for c in by_category:
        print(f"  {c['category']:<13} €{c['amountEur']:>10,.2f}  ({c['count']} charges)")
    print(f"  {'TOTAL':<13} €{payload['totalEur']:>10,.2f}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(
            f"DEGIRO fee export failed: {exc}\n"
            f"  If login worked for `plout degiro` it should work here too;\n"
            f"  DEGIRO may have changed their API — try updating degiro-connector."
        )
