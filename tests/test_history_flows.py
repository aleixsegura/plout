#!/usr/bin/env python3
"""Tests for the running totals in history.json and the DEGIRO deposit rules.

The Holdings page subtracts these totals from each day's value to separate
investment return from money moved in, so a misclassified line shows up there
as fake profit or loss: an FX leg read as a deposit, a staking reward read as
money in, a sale read as a loss. Run them after touching plout.history's
*_at helpers or plout.exporters.deposits.classify:

    .venv/bin/python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.exporters.deposits import build_movements, classify
from plout.history import (
    backfill,
    degiro_net_deposits_at,
    revolut_interest_at,
    revolut_net_invested_at,
)


class ClassifyTest(unittest.TestCase):
    def test_bank_transfers(self):
        self.assertEqual(classify("Ingreso"), "deposit")
        self.assertEqual(classify("flatex Deposit"), "deposit")
        self.assertEqual(classify("Depósito"), "deposit")
        self.assertEqual(classify("Retirada"), "withdrawal")
        self.assertEqual(classify("Processed Flatex Withdrawal"), "withdrawal")

    def test_internal_movements_are_not_deposits(self):
        for desc in (
            "Ingreso Cambio de Divisa",
            "Retirada Cambio de Divisa",
            "Degiro Cash Sweep Transfer",
            "Transferir desde su Cuenta de Efectivo en flatexDEGIRO Bank: 500,00 EUR",
            "Transferir a su Cuenta de Efectivo en flatexDEGIRO Bank: 500,00 EUR",
        ):
            self.assertIsNone(classify(desc), desc)

    def test_other_movements(self):
        for desc in ("Compra 3 Apple Inc@180 USD", "Dividendo", "Costes de transacción", None, ""):
            self.assertIsNone(classify(desc), desc)

    def test_build_movements_dedupes_and_sorts(self):
        raw = [
            {"id": 2, "date": "2026-08-01T10:00:00+02:00", "description": "Ingreso", "currency": "EUR", "change": 500},
            {"id": 1, "date": "2026-07-01T10:00:00+02:00", "description": "Ingreso", "currency": "EUR", "change": 1000},
            {"id": 1, "date": "2026-07-01T10:00:00+02:00", "description": "Ingreso", "currency": "EUR", "change": 1000},
            {"id": 3, "date": "2026-08-02T10:00:00+02:00", "description": "Retirada", "currency": "EUR", "change": -200},
            {"id": 4, "date": "2026-08-03T10:00:00+02:00", "description": "Ingreso Cambio de Divisa", "currency": "EUR", "change": 90},
        ]
        out = build_movements(raw)
        self.assertEqual([m["amountEur"] for m in out], [1000, 500, -200])


class RunningTotalsTest(unittest.TestCase):
    DEPOSITS = {
        "movements": [
            {"date": "2026-07-01T10:00:00+02:00", "amountEur": 1000},
            {"date": "2026-08-01T10:00:00+02:00", "amountEur": 500},
            {"date": "2026-08-02T10:00:00+02:00", "amountEur": -200},
        ]
    }

    def test_net_deposits_up_to_instant(self):
        self.assertEqual(degiro_net_deposits_at(self.DEPOSITS, "2026-07-31T22:00:00+00:00"), 1000)
        self.assertEqual(degiro_net_deposits_at(self.DEPOSITS, "2026-08-05T00:00:00+00:00"), 1300)
        # 08:00 UTC is 10:00 in Madrid: the deposit has landed by then.
        self.assertEqual(degiro_net_deposits_at(self.DEPOSITS, "2026-08-01T08:00:00+00:00"), 1500)
        self.assertIsNone(degiro_net_deposits_at(None, "2026-08-05T00:00:00+00:00"))

    def test_revolut_net_invested(self):
        trades = {
            "trades": [
                {"date": "2026-07-01T10:00:00+02:00", "side": "buy", "grossAmount": 1000, "feeAmount": 5},
                {"date": "2026-09-01T10:00:00+02:00", "side": "sell", "grossAmount": 600, "feeAmount": 3},
            ],
            "deposits": [
                {"date": "2026-06-01T10:00:00+02:00", "valueEur": 250},
                # Older exports never valued deposits: they add nothing.
                {"date": "2026-06-02T10:00:00+02:00"},
            ],
            "rewards": [{"date": "2026-07-02T10:00:00+02:00", "quantity": 0.1}],
        }
        self.assertEqual(revolut_net_invested_at(trades, "2026-08-01T00:00:00+00:00"), 1255)
        self.assertEqual(revolut_net_invested_at(trades, "2026-10-01T00:00:00+00:00"), 658)

    def test_revolut_interest_by_local_date(self):
        tx = {"interest": {"daily": [
            {"date": "2026-07-01", "amount": 0.5},
            {"date": "2026-07-02", "amount": 0.5},
            {"date": "2026-07-03", "amount": 0.5},
        ]}}
        self.assertEqual(revolut_interest_at(tx, "2026-07-02T12:00:00+00:00"), 1.0)

    def test_backfill_only_fills_missing(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "deposits.json").write_text(json.dumps(self.DEPOSITS))
            history = {"days": [
                {"date": "2026-07-15", "degiro": {"updatedAt": "2026-07-15T10:00:00+00:00"}, "revolut": None},
                {"date": "2026-08-15", "degiro": {"updatedAt": "2026-08-15T10:00:00+00:00", "netDepositsEur": 9999}, "revolut": None},
            ]}
            self.assertEqual(backfill(history, data), 1)
            self.assertEqual(history["days"][0]["degiro"]["netDepositsEur"], 1000)
            self.assertEqual(history["days"][1]["degiro"]["netDepositsEur"], 9999)


if __name__ == "__main__":
    unittest.main()
