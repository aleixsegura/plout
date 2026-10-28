#!/usr/bin/env python3
"""Tests for how the Renta pack turns FIFO parcels into form entries.

Two things are pinned down here, and both changed what gets typed into the
return: a sale is one entry (not one per FIFO parcel), and the staking rewards
behind a sale count as a single acquisition. Neither may move a euro. Run them
after touching _gain_entries or _merge_staking_parcels:

    .venv/bin/python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.exporters.taxes import Asset, Lot, Sale, _gain_entries, aggregate_asset_year, match_fifo


def fields_of(entry: dict) -> dict:
    return {f["label"]: f["value"] for f in entry["fields"]}


def eth_with_rewards(reward_days: int, buy_cost: float = 1000.0) -> Asset:
    """One bought lot, `reward_days` daily staking dust lots, everything sold."""
    asset = Asset(
        key="ETH", symbol="ETH", name="Ethereum", asset_type="crypto",
        platform="Revolut", currency="EUR",
    )
    asset.lots.append(Lot(day="2026-01-10", quantity=1.0, cost_eur=buy_cost, source="buy"))
    for i in range(reward_days):
        asset.lots.append(
            Lot(day=f"2026-06-{i + 1:02d}", quantity=0.0001, cost_eur=0.30, source="staking")
        )
    asset.sales.append(
        Sale(
            day="2026-09-11",
            quantity=1.0 + 0.0001 * reward_days,
            proceeds_eur=1400.0,
            fee_eur=1.2,
            platform="Revolut",
        )
    )
    return asset


def stock_sold_twice() -> Asset:
    """Three buys, two sell orders on different days: the shape that decides
    whether the transmission date survives the grouping."""
    asset = Asset(
        key="ACME", symbol="ACME", name="Acme Corp", asset_type="stock",
        platform="DEGIRO", currency="EUR", isin="US0000000001",
    )
    asset.lots.append(Lot(day="2026-01-10", quantity=10, cost_eur=1000.0, source="buy", fee_eur=1.0))
    asset.lots.append(Lot(day="2026-02-10", quantity=10, cost_eur=1200.0, source="buy", fee_eur=1.0))
    asset.lots.append(Lot(day="2026-03-10", quantity=10, cost_eur=900.0, source="buy", fee_eur=1.0))
    asset.sales.append(Sale(day="2026-05-20", quantity=15, proceeds_eur=1800.0, fee_eur=2.0, platform="DEGIRO"))
    asset.sales.append(Sale(day="2026-07-20", quantity=10, proceeds_eur=1100.0, fee_eur=2.0, platform="DEGIRO"))
    return asset


def row_for(asset: Asset) -> tuple[dict, list[dict]]:
    disposals, _warnings = match_fifo(asset)
    return aggregate_asset_year(asset, disposals, 0.0), disposals


class OneEntryPerSale(unittest.TestCase):
    def test_a_sale_is_one_entry_however_many_lots_funded_it(self):
        row, disposals = row_for(stock_sold_twice())
        entries, parcels = _gain_entries(row)
        self.assertEqual(len(entries), 2)          # two sell orders
        self.assertEqual(len(parcels), 4)          # 2 + 2 FIFO parcels
        self.assertEqual([e["id"] for e in entries], ["ACME-2026-05-20", "ACME-2026-07-20"])
        # The two levels must never share an id: both are numbered.
        self.assertEqual(
            [e["id"] for e in parcels],
            [
                "ACME-2026-05-20-lote-1", "ACME-2026-05-20-lote-2",
                "ACME-2026-07-20-lote-3", "ACME-2026-07-20-lote-4",
            ],
        )
        self.assertFalse({e["id"] for e in entries} & {e["id"] for e in parcels})

    def test_the_transmission_date_is_never_varias(self):
        """Merging acquisition dates is a shortcut the form contemplates;
        merging sale dates would blur the date that fixes the ejercicio."""
        row, _ = row_for(stock_sold_twice())
        entries, _parcels = _gain_entries(row)
        self.assertEqual(
            [fields_of(e)["Fecha de transmisión"] for e in entries],
            ["20/05/2026", "20/07/2026"],
        )

    def test_acquisition_date_is_kept_when_there_is_only_one(self):
        row, _ = row_for(stock_sold_twice())
        entries, _parcels = _gain_entries(row)
        self.assertEqual(fields_of(entries[0])["Fecha de adquisición"], "varias")
        self.assertEqual(fields_of(entries[0])["Nº de valores transmitidos"], 15)
        self.assertEqual(entries[0]["acquisitionDates"], ["10/01/2026", "10/02/2026"])
        # The second sale is funded by what is left of one lot plus another…
        self.assertEqual(fields_of(entries[1])["Fecha de adquisición"], "varias")

    def test_one_lot_one_date(self):
        asset = stock_sold_twice()
        asset.sales = [Sale(day="2026-05-20", quantity=5, proceeds_eur=600.0, fee_eur=1.0, platform="DEGIRO")]
        row, _ = row_for(asset)
        entries, _parcels = _gain_entries(row)
        self.assertEqual(fields_of(entries[0])["Fecha de adquisición"], "10/01/2026")
        self.assertEqual(entries[0]["acquisitionDates"], ["10/01/2026"])

    def test_the_entry_is_the_sale_to_the_cent(self):
        row, disposals = row_for(stock_sold_twice())
        entries, _parcels = _gain_entries(row)
        for entry, disposal in zip(entries, disposals):
            f = fields_of(entry)
            self.assertEqual(f["Valor de transmisión"], disposal["disposalValueEur"])
            self.assertEqual(f["Valor de adquisición"], disposal["acquisitionValueEur"])
            self.assertEqual(entry["resultEur"], disposal["resultEur"])
            self.assertEqual(entry["feesEur"]["sellEur"], disposal["sellFeesEur"])
            self.assertEqual(entry["feesEur"]["buyEur"], disposal["buyFeesEur"])

    def test_deferral_is_taken_whole_not_reassembled(self):
        """Per parcel the deferred loss has to be split pro rata; per sale it
        is simply the sale's own figure."""
        asset = stock_sold_twice()
        row, disposals = row_for(asset)
        disposals[0]["deferred"] = True
        disposals[0]["deferredLossEur"] = -120.0
        row = aggregate_asset_year(asset, disposals, 0.0)
        entries, parcels = _gain_entries(row)
        self.assertEqual(entries[0]["deferredLossEur"], -120.0)
        self.assertTrue(entries[0]["partiallyDeferred"])
        self.assertFalse(entries[1]["deferred"])
        deferred_parcels = [p for p in parcels if p["deferred"]]
        self.assertEqual(round(sum(p["deferredLossEur"] for p in deferred_parcels), 2), -120.0)

    def test_two_sales_on_the_same_day_get_distinct_ids(self):
        asset = stock_sold_twice()
        asset.sales[1] = Sale(day="2026-05-20", quantity=10, proceeds_eur=1100.0, fee_eur=2.0, platform="DEGIRO")
        row, _ = row_for(asset)
        entries, _parcels = _gain_entries(row)
        self.assertEqual([e["id"] for e in entries], ["ACME-2026-05-20", "ACME-2026-05-20-2"])


class MergeStakingParcels(unittest.TestCase):
    def test_rewards_become_one_parcel_line(self):
        row, _ = row_for(eth_with_rewards(20))
        _entries, parcels = _gain_entries(row)
        # The bought lot plus one merged line, not 21.
        self.assertEqual(len(parcels), 2)
        merged = [p for p in parcels if p.get("merged")]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["merged"]["count"], 20)
        self.assertEqual(merged[0]["merged"]["fromDate"], "01/06/2026")
        self.assertEqual(merged[0]["merged"]["toDate"], "20/06/2026")
        self.assertEqual(fields_of(merged[0])["Fecha de adquisición"], "varias")

    def test_the_sale_entry_says_the_rewards_are_in_there(self):
        row, _ = row_for(eth_with_rewards(20))
        entries, _parcels = _gain_entries(row)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["merged"]["count"], 20)
        # 21 lots funded the sale, but only 2 acquisition dates are worth naming.
        self.assertEqual(entries[0]["parcelCount"], 2)
        self.assertEqual(entries[0]["acquisitionDates"], ["10/01/2026"])

    def test_merging_moves_no_euro(self):
        """The parcel lines add up to exactly what they did before the merge.
        (They can sit a cent off the sale's own total — that is per-parcel
        rounding, and it predates this.)"""
        asset = eth_with_rewards(20)
        disposals, _warnings = match_fifo(asset)
        raw = disposals[0]["parcels"]
        _entries, parcels = _gain_entries(aggregate_asset_year(asset, disposals, 0.0))

        def total(label):
            return round(sum(fields_of(e)[label] for e in parcels), 2)

        self.assertEqual(total("Valor de transmisión"), round(sum(p["disposalValueEur"] for p in raw), 2))
        self.assertEqual(total("Valor de adquisición"), round(sum(p["acquisitionValueEur"] for p in raw), 2))
        self.assertEqual(
            round(sum(e["feesEur"]["sellEur"] for e in parcels), 2),
            round(sum(p["sellFeesEur"] for p in raw), 2),
        )

    def test_result_reads_as_transmision_minus_adquisicion(self):
        row, _ = row_for(eth_with_rewards(20))
        entries, parcels = _gain_entries(row)
        for entry in entries + parcels:
            f = fields_of(entry)
            self.assertEqual(
                entry["resultEur"],
                round(f["Valor de transmisión"] - f["Valor de adquisición"], 2),
                entry["id"],
            )

    def test_a_single_reward_is_left_alone(self):
        """Nothing to gain from merging one lot, and its real date is better."""
        row, _ = row_for(eth_with_rewards(1))
        entries, parcels = _gain_entries(row)
        self.assertEqual(len(parcels), 2)
        self.assertFalse(any(p.get("merged") for p in parcels))
        self.assertNotIn("merged", entries[0])
        self.assertEqual(entries[0]["acquisitionDates"], ["10/01/2026", "01/06/2026"])


if __name__ == "__main__":
    unittest.main()
