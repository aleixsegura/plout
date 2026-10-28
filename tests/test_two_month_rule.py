#!/usr/bin/env python3
"""Tests for the two-month repurchase rule (art. 33.5 f LIRPF).

These exist because both bugs they pin down changed the tax actually owed:
a loss wrongly blocked is tax paid a year early (or never reclaimed), and a
loss wrongly computed is a correction letter. Run them after touching
match_fifo or flag_two_month_rule:

    .venv/bin/python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.exporters.taxes import Asset, Lot, Sale, build_years, flag_two_month_rule, match_fifo


def asset_with(lots, sales, asset_type="stock"):
    """An Asset holding `lots` (day, qty, cost) and `sales` (day, qty, proceeds)."""
    return Asset(
        key="test",
        symbol="TEST",
        name="Test",
        asset_type=asset_type,
        platform="DEGIRO",
        currency="EUR",
        lots=[Lot(day=d, quantity=q, cost_eur=c, source="test") for d, q, c in lots],
        sales=[
            Sale(day=d, quantity=q, proceeds_eur=p, fee_eur=0.0, platform="DEGIRO")
            for d, q, p in sales
        ],
    )


def run(asset):
    disposals, _warnings = match_fifo(asset)
    notes = flag_two_month_rule(asset, disposals)
    return disposals, notes


class SelfRepurchase(unittest.TestCase):
    """A lot the sale itself consumed is not a repurchase of that sale."""

    def test_bought_one_day_sold_the_next_is_not_deferred(self):
        # Regression case: shares bought one day and sold the next at a loss,
        # with no prior position. Nothing remains, so nothing is deferred.
        asset = asset_with(
            lots=[("2026-03-09", 10, 1500.0)],
            sales=[("2026-03-10", 10, 1250.0)],
        )
        (disposal,), notes = run(asset)

        self.assertLess(disposal["resultEur"], 0)
        self.assertFalse(disposal["deferred"])
        self.assertEqual(disposal["deferredLossEur"], 0.0)
        self.assertEqual(disposal["computableResultEur"], disposal["resultEur"])
        self.assertEqual(notes, [])

    def test_only_the_unconsumed_part_of_a_lot_counts(self):
        # 100 bought inside the window, 60 sold at a loss: the 40 left over are
        # a genuine repurchase, the 60 sold are not.
        asset = asset_with(
            lots=[("2026-06-01", 100, 10000.0)],
            sales=[("2026-06-20", 60, 3000.0)],
        )
        (disposal,), _notes = run(asset)

        self.assertEqual(disposal["deferredQuantity"], 40.0)
        # 40 of the 60 sold → two thirds of the loss deferred.
        self.assertAlmostEqual(
            disposal["deferredLossEur"], round(disposal["resultEur"] * 40 / 60, 2), places=2
        )

    def test_same_day_gain_also_consumes_the_lot(self):
        # Regression case: 120 bought and all 120 sold the same
        # day, as a 100 order at a loss and a 20 order at a gain. The 20 are
        # sold too, so nothing was repurchased — before the fix they looked
        # spare, because only the loss-making orders counted as consumption.
        asset = asset_with(
            lots=[("2026-03-04", 120, 600.0)],
            sales=[("2026-03-04", 100, 450.0), ("2026-03-04", 20, 130.0)],
        )
        (loss, gain), notes = run(asset)

        self.assertLess(loss["resultEur"], 0)
        self.assertFalse(loss["deferred"])
        self.assertEqual(loss["deferredLossEur"], 0.0)
        self.assertEqual(loss["computableResultEur"], loss["resultEur"])
        self.assertFalse(gain["deferred"])
        self.assertEqual(notes, [])

    def test_a_repurchase_sold_again_the_same_year_does_not_defer(self):
        # Buy back inside the window, then close that position again before
        # 31 December: the deferral would reintegrate in the very same year,
        # so the loss is computable now and no note should appear.
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-05", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0), ("2026-11-20", 100, 6500.0)],
        )
        (loss, _gain), notes = run(asset)

        self.assertLess(loss["resultEur"], 0)
        self.assertFalse(loss["deferred"])
        self.assertEqual(loss["computableResultEur"], loss["resultEur"])
        self.assertEqual(notes, [])

    def test_half_the_repurchase_sold_again_defers_the_other_half(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-05", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0), ("2026-11-20", 50, 3250.0)],
        )
        (loss, _gain), _notes = run(asset)

        self.assertEqual(loss["deferredQuantity"], 50.0)
        self.assertEqual(loss["deferredLossEur"], round(loss["resultEur"] * 0.5, 2))

    def test_a_repurchase_sold_in_a_later_year_still_defers(self):
        # The cut-off is 31 December of the loss year, not "whenever the file
        # is regenerated": selling the repurchase in 2027 must not rewrite the
        # 2026 return that was already filed on it.
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-05", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0), ("2027-03-20", 100, 6500.0)],
        )
        (loss, _later), notes = run(asset)

        self.assertTrue(loss["deferred"])
        self.assertEqual(loss["deferredLossEur"], loss["resultEur"])
        self.assertEqual(notes[0][0], "2026")


class ProportionalDeferral(unittest.TestCase):
    """A partial repurchase blocks a proportional share of the loss."""

    def test_partial_repurchase_defers_pro_rata(self):
        # Regression case, simplified: 85 sold at a loss, 25 bought back
        # twelve days later. 25/85 of the loss is blocked, the rest is not.
        asset = asset_with(
            lots=[("2025-05-12", 85, 5000.0), ("2026-10-24", 25, 500.0)],
            sales=[("2026-10-12", 85, 2550.0)],
        )
        (disposal,), notes = run(asset)

        self.assertEqual(disposal["resultEur"], -2450.0)
        self.assertEqual(disposal["deferredQuantity"], 25.0)
        self.assertEqual(disposal["deferredLossEur"], round(-2450.0 * 25 / 85, 2))
        self.assertEqual(
            disposal["computableResultEur"],
            round(disposal["resultEur"] - disposal["deferredLossEur"], 2),
        )
        # The two halves must always add back up to the whole loss.
        self.assertAlmostEqual(
            disposal["deferredLossEur"] + disposal["computableResultEur"],
            disposal["resultEur"],
            places=2,
        )
        self.assertIn("no son computables", notes[0][1]["message"])

    def test_full_repurchase_defers_everything(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-05", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0)],
        )
        (disposal,), notes = run(asset)

        self.assertEqual(disposal["deferredLossEur"], disposal["resultEur"])
        self.assertEqual(disposal["computableResultEur"], 0.0)
        self.assertIn("no es computable", notes[0][1]["message"])

    def test_same_day_sales_share_the_repurchase_pro_rata(self):
        # Regression shape: three sell orders the same day, with different
        # FIFO costs, and one later repurchase of 25 of the 85 sold. Each order
        # must defer the same 25/85 fraction — if the repurchase were poured
        # into the orders in list order, the tax owed would depend on the order
        # the broker happened to return them in.
        asset = asset_with(
            lots=[
                ("2025-05-12", 5, 490.0),
                ("2025-05-13", 30, 1410.0),
                ("2025-05-14", 50, 1755.0),
                ("2026-10-24", 25, 500.0),
            ],
            sales=[
                ("2026-10-12", 5, 150.0),
                ("2026-10-12", 30, 900.0),
                ("2026-10-12", 50, 1500.0),
            ],
        )
        disposals, _notes = run(asset)

        fraction = 25 / 85
        for disposal in disposals:
            self.assertAlmostEqual(
                disposal["deferredLossEur"],
                round(disposal["resultEur"] * fraction, 2),
                places=2,
            )
        self.assertAlmostEqual(
            sum(d["deferredQuantity"] for d in disposals), 25.0, places=6
        )

    def test_same_day_allocation_ignores_sale_order(self):
        # Same trade set, orders listed the other way round: identical totals.
        def totals(sales):
            asset = asset_with(
                lots=[
                    ("2025-05-12", 5, 490.0),
                    ("2025-05-13", 30, 1410.0),
                    ("2025-05-14", 50, 1755.0),
                    ("2026-10-24", 25, 500.0),
                ],
                sales=sales,
            )
            disposals, _notes = run(asset)
            return round(sum(d["deferredLossEur"] for d in disposals), 2)

        forward = [("2026-10-12", 5, 150.0), ("2026-10-12", 30, 900.0), ("2026-10-12", 50, 1500.0)]
        self.assertEqual(totals(forward), totals(list(reversed(forward))))

    def test_repurchase_budget_is_shared_across_sales(self):
        # Two sales of 50, one repurchase of 25: 25 shares' worth of loss is
        # blocked in total, not 25 per sale.
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-20", 25, 1000.0)],
            sales=[("2026-06-01", 50, 3000.0), ("2026-06-10", 50, 3000.0)],
        )
        disposals, _notes = run(asset)

        self.assertEqual(sum(d["deferredQuantity"] for d in disposals), 25.0)
        self.assertEqual(disposals[0]["deferredQuantity"], 25.0)
        self.assertEqual(disposals[1]["deferredQuantity"], 0.0)


class Reintegration(unittest.TestCase):
    """Art. 33.5 f postpones a loss; it does not delete it. Selling the shares
    that blocked it hands it back in the year of that sale."""

    def test_selling_the_repurchase_next_year_releases_the_whole_loss(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-05", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0), ("2027-03-20", 100, 6500.0)],
        )
        (loss, later), notes = run(asset)

        self.assertEqual(loss["deferredLossEur"], loss["resultEur"])
        self.assertEqual(later["reintegratedLossEur"], loss["deferredLossEur"])
        self.assertEqual(later["reintegratedFrom"][0]["date"], "2026-06-01")
        self.assertEqual(later["reintegratedFrom"][0]["quantity"], 100.0)
        # The release is its own loss, not part of what the 2027 sale made.
        self.assertEqual(later["computableResultEur"], later["resultEur"])
        self.assertEqual([year for year, _n in notes], ["2026", "2027"])

    def test_selling_half_the_repurchase_releases_half(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-05", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0), ("2027-03-20", 50, 3250.0)],
        )
        (loss, later), _notes = run(asset)

        self.assertEqual(later["reintegratedLossEur"], round(loss["deferredLossEur"] / 2, 2))

    def test_a_release_never_happens_twice(self):
        # Sold in three bites across two later years: the pieces add up to the
        # deferred loss exactly, and nothing is handed back after that.
        asset = asset_with(
            lots=[("2025-01-10", 90, 9000.0), ("2026-06-05", 90, 5400.0)],
            sales=[
                ("2026-06-01", 90, 5400.0),
                ("2027-03-20", 30, 1800.0),
                ("2027-09-10", 30, 1900.0),
                ("2028-02-02", 30, 2000.0),
            ],
        )
        disposals, _notes = run(asset)
        loss = disposals[0]
        released = round(sum(d["reintegratedLossEur"] for d in disposals[1:]), 2)

        self.assertEqual(released, loss["deferredLossEur"])
        self.assertEqual(
            [d["reintegratedLossEur"] for d in disposals[1:]],
            [round(loss["deferredLossEur"] / 3, 2)] * 3,
        )

    def test_a_partial_deferral_releases_only_what_it_blocked(self):
        # The same shape carried forward: 25 of 85 blocked in 2026, those 25
        # sold in 2027. Only the 25/85 slice comes back.
        asset = asset_with(
            lots=[("2025-05-12", 85, 5000.0), ("2026-10-24", 25, 500.0)],
            sales=[("2026-10-12", 85, 2550.0), ("2027-01-15", 25, 600.0)],
        )
        (loss, later), _notes = run(asset)

        self.assertEqual(loss["deferredLossEur"], round(-2450.0 * 25 / 85, 2))
        self.assertEqual(later["reintegratedLossEur"], loss["deferredLossEur"])

    def test_nothing_is_released_while_the_repurchase_is_still_held(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-06-05", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0)],
        )
        (loss,), _notes = run(asset)

        self.assertEqual(loss["reintegratedLossEur"], 0.0)
        self.assertEqual(loss["reintegratedFrom"], [])

    def test_every_disposal_carries_the_release_fields(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 5000.0)],
            sales=[("2026-06-01", 40, 3000.0)],
        )
        (disposal,), _notes = run(asset)

        self.assertEqual(disposal["reintegratedLossEur"], 0.0)
        self.assertEqual(disposal["reintegratedFrom"], [])


def years_for(asset):
    """The exported year blocks for one asset, as build_years writes them."""
    with tempfile.TemporaryDirectory() as out:
        years = build_years({"test": asset}, Path(out), [], [], [])
    return {y["year"]: y for y in years}


class YearlyExport(unittest.TestCase):
    """A line sold across two years is the shape reintegration creates, and it
    used to be the one shape the export could not write."""

    def test_a_line_sold_in_two_years_stays_valid_json(self):
        asset = asset_with(
            lots=[("2025-05-12", 85, 5000.0), ("2026-10-24", 25, 500.0)],
            sales=[("2026-10-12", 85, 2550.0), ("2027-01-15", 25, 600.0)],
        )
        years = years_for(asset)

        # allow_nan=False is the point: float("inf") serialises as a bare
        # `Infinity`, which the browser's JSON.parse rejects outright — the
        # page then silently falls back to "no tax data yet".
        json.dumps(years, allow_nan=False)
        self.assertEqual(sorted(years), ["2026", "2027"])

    def test_each_year_reports_the_position_as_that_year_closed(self):
        asset = asset_with(
            lots=[("2025-05-12", 85, 5000.0), ("2026-10-24", 25, 500.0)],
            sales=[("2026-10-12", 85, 2550.0), ("2027-01-15", 25, 600.0)],
        )
        years = years_for(asset)
        row = lambda year: years[year]["capitalGains"]["byAsset"][0]

        # 25 bought back were still held at the end of 2026, none at the end
        # of 2027 — so only 2027 is the full exit.
        self.assertEqual(row("2026")["remainingQuantity"], 25.0)
        self.assertFalse(row("2026")["fullExit"])
        self.assertEqual(row("2027")["remainingQuantity"], 0.0)
        self.assertTrue(row("2027")["fullExit"])

    def test_the_released_loss_lands_in_the_year_that_released_it(self):
        asset = asset_with(
            lots=[("2025-05-12", 85, 5000.0), ("2026-10-24", 25, 500.0)],
            sales=[("2026-10-12", 85, 2550.0), ("2027-01-15", 25, 600.0)],
        )
        years = years_for(asset)
        blocked = years["2026"]["capitalGains"]["deferredLossEur"]

        self.assertEqual(blocked, round(-2450.0 * 25 / 85, 2))
        self.assertEqual(years["2026"]["capitalGains"]["reintegratedLossEur"], 0.0)
        self.assertEqual(years["2027"]["capitalGains"]["reintegratedLossEur"], blocked)
        # And it moves the year's bottom line, which is the whole point.
        gains = years["2027"]["capitalGains"]
        self.assertEqual(
            gains["netEur"],
            round(gains["totalGainEur"] + gains["totalLossEur"] + blocked, 2),
        )


class UncoveredSlices(unittest.TestCase):
    def test_float_noise_is_not_a_missing_lot(self):
        # A staking shape: one sale covered by ~70 staking-dust lots. Adding
        # them up lands a few 1e-9 short of the quantity sold, which is float
        # noise, not a sale with no purchase behind it.
        lots = [(f"2026-07-{day:02d}", 0.0001 + day * 1e-7, 0.2) for day in range(1, 31)]
        sold = sum(q for _d, q, _c in lots)
        asset = asset_with(lots=lots, sales=[("2026-08-01", sold, 90.0)])
        (disposal,), _notes = run(asset)

        self.assertEqual([p["source"] for p in disposal["parcels"]].count("uncovered"), 0)

    def test_a_real_uncovered_slice_is_still_reported(self):
        asset = asset_with(
            lots=[("2026-01-10", 1.0, 1000.0)],
            sales=[("2026-08-01", 1.5, 2000.0)],
        )
        (disposal,), warnings = match_fifo(asset)

        self.assertEqual(warnings[0][1]["id"], "lote-descubierto")
        uncovered = [p for p in disposal["parcels"] if p["source"] == "uncovered"]
        self.assertEqual(len(uncovered), 1)
        self.assertAlmostEqual(uncovered[0]["quantity"], 0.5, places=9)
        self.assertEqual(uncovered[0]["acquisitionValueEur"], 0.0)


class WindowAndScope(unittest.TestCase):
    def test_repurchase_outside_the_window_does_not_defer(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-09-15", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0)],
        )
        (disposal,), notes = run(asset)

        self.assertFalse(disposal["deferred"])
        self.assertEqual(notes, [])

    def test_repurchase_before_the_sale_also_defers(self):
        # The window runs two months either side, not just after.
        asset = asset_with(
            lots=[("2025-01-10", 100, 10000.0), ("2026-05-20", 100, 6000.0)],
            sales=[("2026-06-01", 100, 6000.0)],
        )
        (disposal,), _notes = run(asset)

        self.assertTrue(disposal["deferred"])
        self.assertEqual(disposal["deferredQuantity"], 100.0)

    def test_a_gain_is_never_deferred(self):
        asset = asset_with(
            lots=[("2025-01-10", 100, 5000.0), ("2026-06-05", 100, 9000.0)],
            sales=[("2026-06-01", 100, 9000.0)],
        )
        (disposal,), notes = run(asset)

        self.assertGreater(disposal["resultEur"], 0)
        self.assertFalse(disposal["deferred"])
        self.assertEqual(notes, [])

    def test_crypto_is_flagged_for_review_not_deferred(self):
        asset = asset_with(
            lots=[("2025-01-10", 2.0, 6000.0), ("2026-06-05", 2.0, 3000.0)],
            sales=[("2026-06-01", 2.0, 3000.0)],
            asset_type="crypto",
        )
        (disposal,), notes = run(asset)

        self.assertEqual(disposal["review"], "two-month-rule")
        self.assertFalse(disposal["deferred"])
        self.assertEqual(disposal["computableResultEur"], disposal["resultEur"])
        self.assertEqual(notes[0][1]["id"], "regla-dos-meses-cripto")

    def test_crypto_bought_and_sold_within_the_window_is_not_flagged(self):
        # The self-repurchase fix applies to crypto too: no phantom review.
        asset = asset_with(
            lots=[("2026-06-01", 2.0, 6000.0)],
            sales=[("2026-06-20", 2.0, 3000.0)],
            asset_type="crypto",
        )
        (disposal,), notes = run(asset)

        self.assertIsNone(disposal.get("review"))
        self.assertEqual(notes, [])


class Invariants(unittest.TestCase):
    def test_every_disposal_carries_the_deferral_fields(self):
        # Consumers read these unconditionally; a missing key would be a crash
        # or, worse, a silent .get() default.
        asset = asset_with(
            lots=[("2025-01-10", 100, 5000.0)],
            sales=[("2026-06-01", 40, 3000.0)],
        )
        (disposal,), _notes = run(asset)

        for key in ("deferred", "deferredQuantity", "deferredLossEur", "computableResultEur"):
            self.assertIn(key, disposal)

    def test_deferred_never_exceeds_the_loss(self):
        # More bought back than sold must still only block what was sold.
        asset = asset_with(
            lots=[("2025-01-10", 50, 5000.0), ("2026-06-05", 500, 10000.0)],
            sales=[("2026-06-01", 50, 3000.0)],
        )
        (disposal,), _notes = run(asset)

        self.assertEqual(disposal["deferredQuantity"], 50.0)
        self.assertEqual(disposal["deferredLossEur"], disposal["resultEur"])


if __name__ == "__main__":
    unittest.main()
