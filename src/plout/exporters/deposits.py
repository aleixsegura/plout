"""
Export DEGIRO deposits and withdrawals to data/deposits.json.

Reuses degiro.py's login and dividends.py's cash-statement
fetch, then keeps only the money that crossed the account boundary — bank
transfers in (from any bank) and withdrawals out. The Holdings page needs
them dated: the account's return over a period is the change in value minus
what was deposited in it, and positions.json only carries today's total.

DEGIRO doesn't type these movements, so they are recognised by description.
The traps are lines that read like deposits but only move money inside the
account: each currency exchange is booked as an "Ingreso/Retirada Cambio de
Divisa" pair, and cash shuttles between the DEGIRO and flatex cash accounts
("Degiro Cash Sweep Transfer", "Transferir desde su Cuenta de Efectivo…").

As a check the movements are summed up to positions.json's `updatedAt` and
compared with the net deposits DEGIRO itself reported there. The file is only
written — and history.json's past days only backfilled from it — when the two
agree; otherwise the classified lines are printed so the rules can be fixed.

Usage:
  plout deposits [--from YYYY-MM-DD] [--output-dir DIR] [--debug] [--force]
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from plout.config import PROJECT_ROOT
from plout.exporters.degiro import connect
from plout.exporters.dividends import fetch_cash_movements, to_eur
from plout.history import backfill, degiro_net_deposits_at, load_history, save_history

ACCENTS = str.maketrans("áàäâãéèëêíìïîóòöôõúùüûçñ", "aaaaaeeeeiiiiooooouuuucn")

# Checked first: lines that look like deposits but move money within the
# account (FX legs, DEGIRO↔flatex sweeps, money-market fund parking).
INTERNAL_TOKENS = (
    "cambio de divisa", "currency exchange", "valuta",
    "sweep", "cuenta de efectivo", "cash account",
    "fondo", "money market",
)
DEPOSIT_TOKENS = ("ingreso", "deposit", "deposito", "einzahlung")
WITHDRAWAL_TOKENS = ("retirada", "withdrawal", "reintegro", "auszahlung")

# The check's tolerance: DEGIRO rounds its own total.
TOLERANCE_EUR = 1.0


def _normalize(text: str) -> str:
    return text.lower().translate(ACCENTS)


def classify(description: str | None) -> str | None:
    """Return "deposit", "withdrawal", or None for a cash-movement description."""
    if not description:
        return None
    plain = _normalize(description)
    if any(k in plain for k in INTERNAL_TOKENS):
        return None
    if any(k in plain for k in WITHDRAWAL_TOKENS):
        return "withdrawal"
    if any(k in plain for k in DEPOSIT_TOKENS):
        return "deposit"
    return None


def build_movements(movements: list[dict]) -> list[dict]:
    """Deposits (positive) and withdrawals (negative), oldest first, one per
    DEGIRO movement id."""
    seen: set = set()
    out = []
    for m in movements:
        kind = classify(m.get("description"))
        change = m.get("change")
        raw_date = m.get("date") or m.get("valueDate")
        if kind is None or change is None or not raw_date:
            continue
        key = m.get("id") or (raw_date, m.get("description"), change)
        if key in seen:
            continue
        seen.add(key)
        currency = m.get("currency") or "EUR"
        amount = float(change)
        out.append(
            {
                "date": str(raw_date),
                "kind": kind,
                "description": m.get("description") or "",
                "type": m.get("type"),
                "currency": currency,
                "amount": round(amount, 2),
                "amountEur": round(to_eur(amount, currency, str(raw_date)[:10]), 2),
            }
        )
    out.sort(key=lambda e: e["date"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--from",
        dest="from_date",
        default="2015-01-01",
        help="Earliest date to scan (YYYY-MM-DD, default 2015-01-01 — the check needs the whole history)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT),
        help="Directory containing the data/ folder (default: the project root)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print every cash-movement description with its classification and exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Write deposits.json and backfill history.json even when the check fails",
    )
    args = parser.parse_args()

    try:
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--from must be YYYY-MM-DD, got {args.from_date!r}")
    to_date = date.today()

    out_dir = Path(args.output_dir).resolve() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "deposits.json"

    api, _user_token = connect()
    raw = fetch_cash_movements(api, from_date, to_date)

    if args.debug:
        seen = sorted({(m.get("type") or "", m.get("description") or "") for m in raw})
        print(f"{len(raw)} cash movements; distinct (type, description) → classification:")
        for mtype, desc in seen:
            print(f"  [{mtype}] {desc}  →  {classify(desc) or '-'}")
        return

    movements = build_movements(raw)
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "DEGIRO via degiro-connector",
        "baseCurrency": "EUR",
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "totalEur": round(sum(m["amountEur"] for m in movements), 2),
        "movements": movements,
    }

    print(f"{len(movements)} deposits/withdrawals, net €{payload['totalEur']:,.2f}")
    for m in movements:
        print(f"  {m['date'][:10]}  {m['amountEur']:>11,.2f}  [{m['type']}] {m['description']}")

    # The check: DEGIRO's own net-deposits figure, as of the last `plout degiro` run.
    positions_file = out_dir / "positions.json"
    positions = json.loads(positions_file.read_text()) if positions_file.exists() else None
    expected = ((positions or {}).get("summary") or {}).get("netDepositsEur")
    if expected is None:
        print("\nNo net-deposits figure in positions.json to check against — run `plout degiro` first.")
        ok = False
    else:
        got = degiro_net_deposits_at(payload, positions.get("updatedAt"))
        ok = got is not None and abs(got - float(expected)) <= TOLERANCE_EUR
        print(
            f"\nCheck: movements up to {positions.get('updatedAt')} sum to €{got:,.2f}; "
            f"DEGIRO reports €{float(expected):,.2f} — {'OK' if ok else 'MISMATCH'}"
        )
    if not ok and not args.force:
        print(
            "Nothing written. Rerun with --debug to see how every description is classified,\n"
            "or with --force to write anyway.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {out_file}")

    history = load_history(out_dir)
    filled = backfill(history, out_dir)
    if history["days"]:
        save_history(history, out_dir)
    print(f"Backfilled {filled} field(s) in history.json")


if __name__ == "__main__":
    main()
