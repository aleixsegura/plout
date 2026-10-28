"""
Daily portfolio-value history, appended by the broker exporters.

`record_snapshot()` reads whichever of positions.json (DEGIRO) and
revolut.json exist in the data/ directory and upserts an entry for the
current local date into data/history.json — one entry per day, so
re-running an exporter the same day just refreshes that day's numbers. When
only one exporter ran, the other source's last-known state on disk is still
folded in; its `updatedAt` records how stale it is.

The web app charts this file (served at /history.json via Vite's publicDir),
so `days` is kept sorted ascending by date with unique dates.

Besides values, each day carries the running totals the Holdings page needs to
tell investment return from money moved in (salary into the savings account,
transfers into DEGIRO): DEGIRO's net deposits, the Boosted interest credited so
far, and the EUR put into Revolut positions. Only differences between days are
ever used, so a running total that starts late is fine. Days recorded before a
field existed are filled in by `backfill()` from the exports that hold the
dated movements (deposits.json, revolut_trades.json, revolut_transactions.json).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from plout.config import PROJECT_ROOT


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception as exc:
        print(f"  warning: could not read {path.name} ({exc}); skipping it.", file=sys.stderr)
        return None


def _instant(raw: str | None) -> datetime | None:
    """An ISO timestamp as an aware datetime (naive ones are taken as UTC)."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local_date(raw: str | None) -> str | None:
    dt = _instant(raw)
    return dt.astimezone().date().isoformat() if dt else None


def degiro_net_deposits_at(deposits_file: dict | None, as_of: str | None) -> float | None:
    """Net EUR deposited into DEGIRO up to the instant `as_of`, from the
    movements deposits.py wrote; None when they aren't available."""
    if deposits_file is None or not isinstance(deposits_file.get("movements"), list):
        return None
    cutoff = _instant(as_of)
    if cutoff is None:
        return None
    total = 0.0
    for m in deposits_file["movements"]:
        at = _instant(m.get("date"))
        if at is not None and at <= cutoff:
            total += float(m.get("amountEur") or 0)
    return round(total, 2)


def revolut_net_invested_at(trades_file: dict | None, as_of: str | None) -> float | None:
    """EUR put into Revolut positions up to `as_of`: buys (fee included) minus
    sell proceeds (fee deducted) plus coins transferred in at their arrival
    value. Staking rewards are left out — they are return, not money in."""
    if trades_file is None:
        return None
    cutoff = _instant(as_of)
    if cutoff is None:
        return None
    total = 0.0
    for t in trades_file.get("trades", []):
        at = _instant(t.get("date"))
        if at is None or at > cutoff:
            continue
        gross = float(t.get("grossAmount") or 0)
        fee = float(t.get("feeAmount") or 0)
        total += gross + fee if t.get("side") == "buy" else -(gross - fee)
    for d in trades_file.get("deposits", []):
        at = _instant(d.get("date"))
        if at is not None and at <= cutoff:
            total += float(d.get("valueEur") or 0)
    return round(total, 2)


def revolut_interest_at(transactions_file: dict | None, as_of: str | None) -> float | None:
    """Boosted interest credited up to the local date of `as_of`."""
    if transactions_file is None:
        return None
    day = _local_date(as_of)
    if day is None:
        return None
    daily = (transactions_file.get("interest") or {}).get("daily") or []
    return round(sum(float(r.get("amount") or 0) for r in daily if (r.get("date") or "") <= day), 2)


def _degiro_entry(positions_file: dict | None) -> dict | None:
    if positions_file is None:
        return None
    summary = positions_file.get("summary") or {}
    cash = summary.get("cashEur")
    deposits = summary.get("netDepositsEur")
    return {
        "updatedAt": positions_file.get("updatedAt"),
        "valueEur": round(float(summary.get("totalValueEur") or 0), 2),
        "cashEur": round(float(cash), 2) if cash is not None else None,
        "netDepositsEur": round(float(deposits), 2) if deposits is not None else None,
    }


def _revolut_entry(
    revolut_file: dict | None, trades_file: dict | None, transactions_file: dict | None
) -> dict | None:
    if revolut_file is None:
        return None
    crypto = sum(
        p["valueEur"] for p in revolut_file.get("positions", []) if p.get("valueEur") is not None
    )
    by_kind = {"current": 0.0, "savings": 0.0}
    for c in revolut_file.get("cash", []):
        kind = c.get("kind")
        if kind in by_kind and c.get("amountEur") is not None:
            by_kind[kind] += c["amountEur"]
    updated_at = revolut_file.get("updatedAt")
    return {
        "updatedAt": updated_at,
        "cryptoValueEur": round(crypto, 2),
        "cashCurrentEur": round(by_kind["current"], 2),
        "cashSavingsEur": round(by_kind["savings"], 2),
        "netInvestedEur": revolut_net_invested_at(trades_file, updated_at),
        "interestEur": revolut_interest_at(transactions_file, updated_at),
    }


def backfill(history: dict, data_dir: Path) -> int:
    """Fill the running totals that days recorded before them lack. Only
    missing values are filled — a figure a snapshot recorded live wins.
    Returns how many fields were filled."""
    deposits = _read_json(data_dir / "deposits.json")
    trades = _read_json(data_dir / "revolut_trades.json")
    transactions = _read_json(data_dir / "revolut_transactions.json")
    filled = 0
    for day in history.get("days", []):
        degiro = day.get("degiro")
        if degiro and degiro.get("netDepositsEur") is None:
            value = degiro_net_deposits_at(deposits, degiro.get("updatedAt"))
            if value is not None:
                degiro["netDepositsEur"] = value
                filled += 1
        revolut = day.get("revolut")
        if not revolut:
            continue
        for key, compute, source in (
            ("netInvestedEur", revolut_net_invested_at, trades),
            ("interestEur", revolut_interest_at, transactions),
        ):
            if revolut.get(key) is None:
                value = compute(source, revolut.get("updatedAt"))
                if value is not None:
                    revolut[key] = value
                    filled += 1
    return filled


def load_history(data_dir: Path) -> dict:
    history_file = data_dir / "history.json"
    history = _read_json(history_file)
    if history is None or not isinstance(history.get("days"), list):
        if history_file.exists():
            print(f"  warning: {history_file.name} was unreadable; starting fresh.", file=sys.stderr)
        history = {"baseCurrency": "EUR", "days": []}
    return history


def save_history(history: dict, data_dir: Path) -> Path:
    history_file = data_dir / "history.json"
    history_file.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n")
    return history_file


def record_snapshot(data_dir: Path) -> Path:
    """Upsert today's (local date) portfolio snapshot in history.json."""
    degiro = _degiro_entry(_read_json(data_dir / "positions.json"))
    revolut = _revolut_entry(
        _read_json(data_dir / "revolut.json"),
        _read_json(data_dir / "revolut_trades.json"),
        _read_json(data_dir / "revolut_transactions.json"),
    )

    portfolio_value = (degiro["valueEur"] if degiro else 0.0) + (
        revolut["cryptoValueEur"] if revolut else 0.0
    )
    cash = ((degiro["cashEur"] or 0.0) if degiro else 0.0) + (
        revolut["cashCurrentEur"] + revolut["cashSavingsEur"] if revolut else 0.0
    )
    day = {
        "date": datetime.now().astimezone().date().isoformat(),
        "degiro": degiro,
        "revolut": revolut,
        "portfolioValueEur": round(portfolio_value, 2),
        "cashEur": round(cash, 2),
        "totalEur": round(portfolio_value + cash, 2),
    }

    history = load_history(data_dir)
    history["days"] = sorted(
        [d for d in history["days"] if d.get("date") != day["date"]] + [day],
        key=lambda d: d.get("date") or "",
    )
    backfill(history, data_dir)
    return save_history(history, data_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill the running totals of past days in history.json, without recording a new snapshot."
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT),
        help="Directory containing the data/ folder (default: the project root)",
    )
    args = parser.parse_args()
    data = Path(args.output_dir).resolve() / "data"
    hist = load_history(data)
    n = backfill(hist, data)
    save_history(hist, data)
    print(f"Filled {n} field(s) across {len(hist['days'])} day(s) in {data / 'history.json'}")


if __name__ == "__main__":
    main()
