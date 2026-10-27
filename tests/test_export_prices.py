#!/usr/bin/env python3
"""Tests for export_prices.py's offline parts: parsing, FX, symbol collection.

The correlation page trusts these series blindly — a close in the wrong
currency or on the wrong date shows up as a fake return spike, not an error.

    .venv/bin/python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from export_prices import (
    Target,
    collect_targets,
    is_fresh,
    parse_coinbase,
    parse_onvista_eod,
    pick_notation,
    to_eur,
)


def noon_utc(day: str) -> int:
    return int(datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


def quote(code: str, country: str, currency: str, notation: int) -> dict:
    return {
        "isoCurrency": currency,
        "market": {"codeExchange": code, "isoCountry": country, "idNotation": notation},
    }


class ParseTests(unittest.TestCase):
    def test_onvista_columns_become_dated_closes(self):
        payload = {
            "isoCurrency": "USD",
            "datetimeLast": [noon_utc("2024-01-03"), noon_utc("2024-01-02"), None],
            "last": [11.0, 10.0, 12.0],
        }
        dates, closes, currency = parse_onvista_eod(payload)
        self.assertEqual(dates, ["2024-01-02", "2024-01-03"])
        self.assertEqual(closes, [10.0, 11.0])
        self.assertEqual(currency, "USD")

    def test_onvista_skips_null_closes(self):
        payload = {"datetimeLast": [noon_utc("2024-01-02")], "last": [None]}
        self.assertEqual(parse_onvista_eod(payload)[:2], ([], []))

    def test_coinbase_candle_close_is_index_4(self):
        midnight = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp())
        self.assertEqual(
            parse_coinbase([[midnight, 1.0, 3.0, 2.0, 2.5, 100.0]]), {"2024-01-02": 2.5}
        )


class FxTests(unittest.TestCase):
    FX = {"USD": {"2024-01-02": 1.10, "2024-01-04": 1.25}, "GBP": {"2024-01-02": 0.5}}

    def test_eur_is_untouched(self):
        self.assertEqual(to_eur(["2024-01-02"], [10.0], "EUR", {}), [10.0])

    def test_divides_by_units_per_eur_and_forward_fills(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        out = to_eur(dates, [11.0, 11.0, 11.0, 12.5], "USD", self.FX)
        # 01-01 predates the first fixing, 01-03 has none: both borrow a neighbour.
        self.assertEqual(out, [10.0, 10.0, 10.0, 10.0])

    def test_pence_are_divided_by_100(self):
        self.assertEqual(to_eur(["2024-01-02"], [250.0], "GBX", self.FX), [5.0])

    def test_unknown_currency_is_none(self):
        self.assertIsNone(to_eur(["2024-01-02"], [1.0], "JPY", self.FX))


class NotationTests(unittest.TestCase):
    QUOTES = [
        quote("TRO", "DE", "EUR", 1),
        quote("GER", "DE", "EUR", 2),
        quote("NYS", "US", "USD", 3),
        quote("LSE", "GB", "USD", 4),
        quote("LSE", "GB", "GBP", 5),
    ]

    def notation(self, exchange, currency):
        picked = pick_notation(self.QUOTES, exchange, currency)
        return picked and picked["market"]["idNotation"]

    def test_us_exchange_prices_off_the_home_listing(self):
        self.assertEqual(self.notation("NSY", "USD"), 3)

    def test_tradegate_prices_off_xetra(self):
        self.assertEqual(self.notation("TDG", "EUR"), 2)

    def test_same_currency_listing_wins(self):
        self.assertEqual(self.notation("LSE", "GBP"), 5)

    def test_unknown_exchange_finds_nothing(self):
        self.assertIsNone(self.notation("ZZZ", "EUR"))


class CollectTests(unittest.TestCase):
    def test_collects_etfs_skips_currencies_and_backfills_isin(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "positions.json").write_text(json.dumps({"positions": [
                {"id": "1", "symbol": "AAA", "productType": "STOCK", "exchange": "nsy",
                 "currency": "USD", "isin": "US0000000001"},
                {"id": "2", "symbol": "EEE", "productType": "ETF", "exchange": "TDG",
                 "currency": "EUR"},
                {"id": "3", "symbol": "CCC", "productType": "CURRENCY"},
            ]}))
            (out / "taxes.json").write_text(json.dumps({"trades": [
                {"productId": "2", "isin": "IE0000000002"},
            ]}))
            (out / "revolut.json").write_text(json.dumps({"positions": [{"symbol": "BTC"}]}))

            targets = {t.symbol: t for t in collect_targets(out)}

        self.assertEqual(set(targets), {"AAA", "EEE", "BTC"})
        self.assertEqual(targets["AAA"].isin, "US0000000001")
        self.assertEqual(targets["AAA"].exchange, "NSY")
        self.assertEqual(targets["EEE"].isin, "IE0000000002")
        self.assertEqual(targets["BTC"].source, "coinbase")


class FreshTests(unittest.TestCase):
    def previous(self, hours_ago: float, symbols, unresolved=()):
        updated = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return {
            "updatedAt": updated.isoformat(),
            "series": {s: {} for s in symbols},
            "unresolved": list(unresolved),
        }

    def targets(self, *symbols):
        return [Target(symbol=s, source="onvista") for s in symbols]

    def test_recent_and_complete_is_fresh(self):
        self.assertTrue(is_fresh(self.previous(1, ["A"], ["B"]), self.targets("A", "B"), 12))

    def test_stale_file_is_not_fresh(self):
        self.assertFalse(is_fresh(self.previous(13, ["A"]), self.targets("A"), 12))

    def test_new_holding_forces_refresh(self):
        self.assertFalse(is_fresh(self.previous(1, ["A"]), self.targets("A", "B"), 12))


if __name__ == "__main__":
    unittest.main()
