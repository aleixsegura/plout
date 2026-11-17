#!/usr/bin/env python3
"""Tests for Revolut crypto buys in the ledger and the blended average price.

A buy made in the regular Revolut app (an EXCHANGE, not a Revolut X trade)
must become a ledger trade — taxes.py turns it into a FIFO lot and history.py
counts it as money in — and must move the average buy price shown in
Holdings without anyone editing crypto_cost_basis.json.

    .venv/bin/python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.exporters import revolut
from plout.exporters.revolut import Currencies, blend_cost_eur, build_crypto_ledger, fold_cost_basis

CCY = Currencies([[
    {"isoCode": "EUR", "exponent": 2, "isCrypto": False},
    {"isoCode": "BTC", "exponent": 8, "isCrypto": True, "name": "Bitcoin"},
    {"isoCode": "ETH", "exponent": 8, "isCrypto": True, "name": "Ethereum"},
]])
DAY = 1_763_000_000_000  # ms epoch, 13 Nov 2025


def crypto_leg(type_, sats, *, counterpart=None, fee=0, id_="tx-1", state="COMPLETED"):
    row = {
        "id": id_,
        "legId": f"{id_}-btc",
        "groupKey": id_,
        "type": type_,
        "state": state,
        "currency": "BTC",
        "amount": sats,
        "fee": fee,
        "description": "Exchanged to BTC",
        "startedDate": DAY,
    }
    if counterpart is not None:
        row["counterpart"] = counterpart
    return row


def fiat_leg(type_, cents, *, fee=0, id_="tx-1"):
    return {
        "id": id_,
        "legId": f"{id_}-eur",
        "groupKey": id_,
        "type": type_,
        "state": "COMPLETED",
        "currency": "EUR",
        "amount": cents,
        "fee": fee,
        "startedDate": DAY,
    }


class ExchangeLedgerTest(unittest.TestCase):
    def test_app_buy_reads_fiat_from_counterpart(self):
        # 500 € for 0.0055 BTC, only the BTC pocket's leg visible.
        rows = [crypto_leg("EXCHANGE", 550_000, counterpart={"amount": -50_000, "currency": "EUR"})]
        ledger = build_crypto_ledger(rows, CCY)
        self.assertEqual(ledger["ignoredTypes"], {})
        (trade,) = ledger["trades"]
        self.assertEqual(trade["side"], "buy")
        self.assertEqual(trade["symbol"], "BTC")
        self.assertAlmostEqual(trade["quantity"], 0.0055)
        self.assertEqual(trade["currency"], "EUR")
        self.assertEqual(trade["grossAmount"], 500.0)
        self.assertEqual(trade["feeAmount"], 0.0)

    def test_app_buy_prefers_fiat_leg_and_its_fee(self):
        rows = [
            crypto_leg("EXCHANGE", 550_000, counterpart={"amount": -49_000, "currency": "EUR"}),
            fiat_leg("EXCHANGE", -49_000, fee=1_000),
        ]
        (trade,) = build_crypto_ledger(rows, CCY)["trades"]
        self.assertEqual(trade["grossAmount"], 490.0)
        self.assertEqual(trade["feeAmount"], 10.0)

    def test_fee_in_coins_is_valued_at_trade_price(self):
        rows = [crypto_leg("EXCHANGE", 500_000, fee=5_000,
                           counterpart={"amount": -50_000, "currency": "EUR"})]
        (trade,) = build_crypto_ledger(rows, CCY)["trades"]
        self.assertEqual(trade["feeAmount"], 5.0)  # 1% of the coins → 1% of 500 €

    def test_app_sell(self):
        rows = [crypto_leg("EXCHANGE", -550_000, counterpart={"amount": 50_000, "currency": "EUR"})]
        (trade,) = build_crypto_ledger(rows, CCY)["trades"]
        self.assertEqual(trade["side"], "sell")

    def test_without_fiat_amount_is_counted_not_invented(self):
        ledger = build_crypto_ledger([crypto_leg("EXCHANGE", 550_000)], CCY)
        self.assertEqual(ledger["trades"], [])
        self.assertEqual(ledger["ignoredTypes"], {"EXCHANGE:incomplete": 1})

    def test_revolut_x_still_needs_its_fiat_leg(self):
        # Revolut X's counterpart is the net amount: never use it as gross.
        rows = [crypto_leg("REVX_EXCHANGE", 550_000, counterpart={"amount": -50_000, "currency": "EUR"})]
        ledger = build_crypto_ledger(rows, CCY)
        self.assertEqual(ledger["trades"], [])
        self.assertEqual(ledger["ignoredTypes"], {"REVX_EXCHANGE:incomplete": 1})

    def test_cancelled_exchange_is_skipped(self):
        rows = [crypto_leg("EXCHANGE", 550_000, state="DECLINED",
                           counterpart={"amount": -50_000, "currency": "EUR"})]
        ledger = build_crypto_ledger(rows, CCY)
        self.assertEqual(ledger["trades"], [])
        self.assertEqual(ledger["ignoredTypes"], {"EXCHANGE:not-completed": 1})


def trade(side, qty, eur, *, date="2026-11-10T10:00:00+01:00", fee=0.0, symbol="BTC"):
    return {"side": side, "symbol": symbol, "quantity": qty, "currency": "EUR",
            "grossAmount": eur, "feeAmount": fee, "date": date}


EUR = lambda _c: 1.0


class BlendTest(unittest.TestCase):
    def test_buy_moves_the_average(self):
        # 0.1 BTC at 60k (6000 €) + 0.01 BTC for 500 € → 6500 € for 0.11 BTC.
        cost = blend_cost_eur(0.11, 60_000, [trade("buy", 0.01, 500)], EUR)
        self.assertAlmostEqual(cost, 6_500)

    def test_buy_fee_is_part_of_the_cost(self):
        cost = blend_cost_eur(0.11, 60_000, [trade("buy", 0.01, 495, fee=5)], EUR)
        self.assertAlmostEqual(cost, 6_500)

    def test_sell_keeps_the_average(self):
        trades = [trade("buy", 0.1, 8_000, date="2026-11-05"), trade("sell", 0.05, 5_000, date="2026-11-06")]
        # pool 0.1 @ 60k → +0.1 for 8000 € (avg 70k) → sell half: 0.15 @ 70k.
        cost = blend_cost_eur(0.15, 60_000, trades, EUR)
        self.assertAlmostEqual(cost, 0.15 * 70_000)

    def test_no_trades_is_the_manual_average(self):
        self.assertAlmostEqual(blend_cost_eur(0.2, 60_000, [], EUR), 12_000)


class FoldTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(revolut, "spot_price", lambda base, quote="EUR": 1.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def position(self):
        return {"symbol": "BTC", "quantity": 0.11, "valueEur": 7_000.0}

    def test_trades_after_as_of_are_blended(self):
        pos = self.position()
        basis = {"BTC": {"avgPrice": 60_000, "currency": "EUR", "asOf": "2026-11-02"}}
        fold_cost_basis([pos], basis, [trade("buy", 0.01, 500)])
        self.assertEqual(pos["costEur"], 6_500.0)
        self.assertAlmostEqual(pos["avgPrice"], 6_500 / 0.11, places=0)
        self.assertEqual(pos["plEur"], 500.0)

    def test_trades_on_or_before_as_of_are_already_in_the_average(self):
        pos = self.position()
        basis = {"BTC": {"avgPrice": 60_000, "currency": "EUR", "asOf": "2026-11-10"}}
        fold_cost_basis([pos], basis, [trade("buy", 0.01, 500)])
        self.assertEqual(pos["costEur"], 6_600.0)
        self.assertEqual(pos["avgPrice"], 60_000)

    def test_without_as_of_the_manual_average_stands(self):
        pos = self.position()
        fold_cost_basis([pos], {"BTC": {"avgPrice": 60_000}}, [trade("buy", 0.01, 500)])
        self.assertEqual(pos["costEur"], 6_600.0)

    def test_other_symbols_trades_are_ignored(self):
        pos = self.position()
        basis = {"BTC": {"avgPrice": 60_000, "asOf": "2026-11-02"}}
        fold_cost_basis([pos], basis, [trade("buy", 1, 2_000, symbol="ETH")])
        self.assertEqual(pos["costEur"], 6_600.0)


if __name__ == "__main__":
    unittest.main()
