#!/usr/bin/env python3
"""Tests for the IRPF rules that came over from Renta Bróker.

Each class pins one rule from docs/mejoras-fiscales-desde-renta-broker.md with
the figures of its own test table, so a change that moves a euro shows up
here first:

    .venv/bin/python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.exporters import ecb  # noqa: E402
from plout.exporters.renta_casillas import (  # noqa: E402
    Dictionary,
    annotate_pack,
    parse_dictionary,
    split_groups,
)
from plout.exporters.tax_rules import (  # noqa: E402
    Pending,
    average_rate_pct,
    cents,
    creditable_tax,
    dividend_country,
    double_tax_deduction,
    integrate,
    savings_tax,
)
from plout.exporters.taxes import (  # noqa: E402
    Asset,
    Lot,
    Sale,
    build_assets,
    build_renta_pack,
    build_years,
    check_degiro_fx,
    dividends_for_year,
    flag_two_month_rule,
    match_fifo,
    queue_key,
    render_renta_markdown,
)

EMPTY_LEDGER = {"trades": [], "deposits": [], "rewards": []}
EMPTY_MANUAL = {"disposals": [], "rewards": [], "lots": [], "pendingLosses": [], "dividendCountries": {}}


# ── 1. Doble imposición ────────────────────────────────────────────────────────


class TreatyCap(unittest.TestCase):
    CASES = [
        # (country, gross, withheld, creditable, excess)
        ("CH", "100", "35", "15.00", "20.00"),   # Nestlé
        ("US", "100", "15", "15.00", "0.00"),    # Apple with W-8BEN
        ("US", "100", "30", "15.00", "15.00"),   # Apple without W-8BEN
        ("GB", "100", "0", "0.00", "0.00"),      # Shell
        ("DK", "100", "27", "27.00", "0.00"),    # Acme: no convention
    ]

    def test_document_cases(self):
        for country, gross, withheld, creditable, excess in self.CASES:
            credit = creditable_tax(D(gross), D(withheld), country, 2025)
            self.assertEqual(credit.creditable, D(creditable), country)
            self.assertEqual(credit.excess, D(excess), country)

    def test_unknown_country_credits_everything_but_says_so(self):
        credit = creditable_tax(D(100), D(30), None, 2025)
        self.assertEqual(credit.creditable, D("30.00"))
        self.assertFalse(credit.known)

    def test_deduction_capped_at_average_rate(self):
        # 43,56 creditable on 161,34 of foreign income at a 19 % average rate.
        self.assertEqual(double_tax_deduction(D("43.56"), D("161.34"), D("19.00")), D("30.65"))
        self.assertEqual(double_tax_deduction(D("10"), D("161.34"), D("19.00")), D("10.00"))

    def test_depositary_receipts_are_doubtful(self):
        self.assertEqual(dividend_country("US6701002056", "Acme ADR"), ("US", True))
        self.assertEqual(dividend_country("CH0038863350", "Nestlé"), ("CH", False))
        self.assertEqual(dividend_country("US6701002056", "Acme ADR", "dk"), ("DK", False))

    def test_dividends_block_splits_creditable_and_excess(self):
        payments = [
            {"date": "2025-04-01", "symbol": "NESN", "productId": 1, "currency": "CHF",
             "gross": 100.0, "tax": -35.0, "grossEur": 0, "taxEur": 0},
            {"date": "2025-05-01", "symbol": "ACME", "name": "Acme ADR", "productId": 2,
             "currency": "EUR", "gross": 100.0, "tax": -27.0},
        ]
        block = dividends_for_year(
            payments, "2025",
            isins={"1": "CH0038863350", "2": "US6701002056"},
            countries={"ACME": "DK"},
            fx=lambda currency, day: (1.0, "ecb", day),
        )
        self.assertEqual(block["creditableEur"], 42.0)       # 15 + 27
        self.assertEqual(block["excessEur"], 20.0)           # Swiss excess only
        self.assertEqual(block["excessByCountry"], [{"country": "CH", "amountEur": 20.0, "treatyRatePct": 15.0}])
        self.assertEqual([n["id"] for n in block["notes"]], ["exceso-retencion"])

    def test_spanish_withholding_is_not_a_foreign_credit(self):
        payments = [{"date": "2025-04-01", "symbol": "SAN", "productId": 3, "currency": "EUR",
                     "gross": 100.0, "tax": -19.0}]
        block = dividends_for_year(payments, "2025", isins={"3": "ES0113900J37"})
        self.assertEqual(block["spanishWithholdingEur"], 19.0)
        self.assertEqual(block["creditableEur"], 0.0)
        self.assertEqual(block["foreignGrossEur"], 0.0)

    def test_no_official_rate_marks_the_dividend_incomplete(self):
        payments = [{"date": "2025-04-01", "symbol": "AAPL", "productId": 4, "currency": "USD",
                     "gross": 10.0, "tax": -1.5, "grossEur": 9.2, "taxEur": -1.38}]
        block = dividends_for_year(payments, "2025", isins={"4": "US0378331005"},
                                   fx=lambda currency, day: None)
        self.assertTrue(block["incomplete"])
        self.assertEqual(block["grossEur"], 9.2)
        self.assertIn("sin-tipo-oficial", [n["id"] for n in block["notes"]])


# ── 2. FIFO por ISIN ───────────────────────────────────────────────────────────


def degiro_trade(day, side, quantity, total, product="1", isin="NL0010273215"):
    return {
        "id": f"{day}-{side}", "platform": "DEGIRO", "date": day, "side": side,
        "productId": product, "symbol": "ASML", "name": "ASML Holding", "isin": isin,
        "assetType": "stock", "currency": "EUR", "quantity": quantity, "price": abs(total) / quantity,
        "totalEur": total, "totalPlusFeesEur": total, "feesEur": 0.0, "transfered": False,
    }


class FifoByIsin(unittest.TestCase):
    def test_queue_key(self):
        self.assertEqual(queue_key({"isin": " nl0010273215 ", "platform": "DEGIRO"}), "isin:NL0010273215")
        self.assertEqual(queue_key({"platform": "DEGIRO", "productId": "42", "symbol": "X"}), "degiro:42")

    def test_two_brokers_one_queue(self):
        """10 ASML on DEGIRO at 700, 10 bought elsewhere at 800, 15 sold at 900:
        the cost is 10 × 700 + 5 × 800 = 11.000 €, not 15 × 800."""
        degiro = [degiro_trade("2024-02-05", "buy", 10, -7000.0)]
        manual = dict(EMPTY_MANUAL)
        manual["lots"] = [{"symbol": "ASML", "isin": "NL0010273215", "assetType": "stock",
                           "platform": "IBKR", "date": "2025-02-03", "quantity": 10,
                           "acquisitionValueEur": 8000.0}]
        manual["disposals"] = [{"symbol": "ASML", "isin": "NL0010273215", "assetType": "stock",
                                "platform": "IBKR", "date": "2026-04-06", "quantity": 15,
                                "grossProceeds": 13500.0, "currency": "EUR"}]
        assets, _w, _r = build_assets(degiro, [], manual, EMPTY_LEDGER)
        self.assertEqual(list(assets), ["isin:NL0010273215"])
        asset = assets["isin:NL0010273215"]
        self.assertEqual(asset.platform, "DEGIRO + IBKR")
        (disposal,), _ = match_fifo(asset)
        self.assertEqual(disposal["acquisitionValueEur"], 11000.0)
        self.assertEqual(disposal["resultEur"], 2500.0)
        self.assertFalse(disposal["incomplete"])
        self.assertEqual([p["platform"] for p in disposal["parcels"]], ["DEGIRO", "IBKR"])

    def test_two_exchanges_one_queue(self):
        degiro = [
            degiro_trade("2024-02-05", "buy", 10, -7000.0, product="1"),
            degiro_trade("2025-02-03", "buy", 10, -8000.0, product="2"),
            degiro_trade("2026-04-06", "sell", 15, 13500.0, product="2"),
        ]
        assets, _w, _r = build_assets(degiro, [], EMPTY_MANUAL, EMPTY_LEDGER)
        self.assertEqual(len(assets), 1)
        (disposal,), _ = match_fifo(next(iter(assets.values())))
        self.assertEqual(disposal["acquisitionValueEur"], 11000.0)


# ── 3. Compensación de pérdidas ────────────────────────────────────────────────


class LossOffsetting(unittest.TestCase):
    def test_negative_gains_against_income_up_to_25_percent(self):
        r = integrate(2025, D(-1000), D(2000), [])
        self.assertEqual((r.gains, r.income), (D(0), D(1500)))
        self.assertEqual(r.cross_offset, D(500))
        self.assertEqual([(p.origin, p.block, p.amount) for p in r.carried], [(2025, "gp", D(500))])

    def test_negative_income_against_gains(self):
        r = integrate(2025, D(4000), D(-2000), [])
        self.assertEqual((r.gains, r.income), (D(3000), D(0)))
        self.assertEqual([(p.block, p.amount) for p in r.carried], [("rcm", D(1000))])

    def test_fourth_year_is_the_last(self):
        """2022 losses in 2026: usable one last time, the rest expires."""
        r = integrate(2026, D(3000), D(0), [Pending(2022, "gp", D(4000))])
        self.assertEqual(r.gains, D(0))
        self.assertEqual([(p.origin, p.amount) for p in r.expired], [(2022, D(1000))])
        self.assertEqual(r.carried, [])

    def test_older_than_four_years_is_gone_before_use(self):
        r = integrate(2026, D(3000), D(0), [Pending(2021, "gp", D(500))])
        self.assertEqual(r.gains, D(3000))
        self.assertEqual([(p.origin, p.amount) for p in r.expired], [(2021, D(500))])

    def test_oldest_first(self):
        r = integrate(2025, D(1000), D(0), [Pending(2024, "gp", D(300)), Pending(2023, "gp", D(900))])
        self.assertEqual(r.gains, D(0))
        self.assertEqual([a["origin"] for a in r.applied], [2023, 2024])
        self.assertEqual([(p.origin, p.amount) for p in r.carried], [(2024, D(200))])

    def test_pending_gp_spills_into_income_sharing_the_25_percent(self):
        # This year's −200 already takes 200 of the 250 cap on income, so the
        # 2023 balance can only use the 50 left; the other 350 carries on.
        r = integrate(2025, D(-200), D(1000), [Pending(2023, "gp", D(400))])
        self.assertEqual((r.gains, r.income), (D(0), D(750)))
        self.assertEqual(r.applied, [{"origin": 2023, "block": "gp", "ownEur": D(0), "otherEur": D(50)}])
        self.assertEqual([(p.origin, p.amount) for p in r.carried], [(2023, D(350))])

    def test_years_feed_each_other(self):
        """A loss computed for 2024 is offset against 2025's gains."""
        asset = Asset(key="X", symbol="X", name="X", asset_type="stock", platform="DEGIRO", currency="EUR")
        asset.lots += [Lot(day="2024-01-10", quantity=10, cost_eur=1000, source="t"),
                       Lot(day="2025-01-10", quantity=10, cost_eur=1000, source="t")]
        asset.sales += [Sale(day="2024-06-10", quantity=10, proceeds_eur=600, fee_eur=0, platform="DEGIRO"),
                        Sale(day="2025-06-10", quantity=10, proceeds_eur=1500, fee_eur=0, platform="DEGIRO")]
        with tempfile.TemporaryDirectory() as out:
            years = {y["year"]: y for y in build_years({"X": asset}, Path(out), [], [], [])}
        self.assertEqual(years["2024"]["savings"]["pendingCarried"],
                         [{"originYear": 2024, "block": "gp", "amountEur": 400.0}])
        self.assertEqual(years["2025"]["savings"]["baseEur"], 100.0)
        self.assertEqual(years["2025"]["capitalGains"]["estimatedTaxEur"], 19.0)

    def test_manual_pending_overrides_from_its_year(self):
        with tempfile.TemporaryDirectory() as out:
            asset = Asset(key="X", symbol="X", name="X", asset_type="stock", platform="DEGIRO", currency="EUR")
            asset.lots.append(Lot(day="2025-01-10", quantity=10, cost_eur=1000, source="t"))
            asset.sales.append(Sale(day="2025-06-10", quantity=10, proceeds_eur=2000, fee_eur=0, platform="DEGIRO"))
            manual = dict(EMPTY_MANUAL)
            manual["pendingLosses"] = [{"originYear": 2023, "block": "gp", "amountEur": 300, "pendingAt": 2025}]
            (year,) = build_years({"X": asset}, Path(out), [], [], [], manual)
        self.assertEqual(year["savings"]["baseEur"], 700.0)
        self.assertEqual(year["savings"]["pendingApplied"][0]["ownBlockEur"], 300.0)


# ── 4. Escala por año ──────────────────────────────────────────────────────────


class SavingsScale(unittest.TestCase):
    def test_top_rate_moved_in_2025(self):
        self.assertEqual(savings_tax(D(400_000), 2024), D("99880.00"))
        self.assertEqual(savings_tax(D(400_000), 2025), D("101880.00"))

    def test_no_rules_no_number(self):
        self.assertIsNone(savings_tax(D(1000), 2030))

    def test_average_rate(self):
        self.assertEqual(average_rate_pct(D("396.56"), D("2087.16")), D("19.00"))


# ── 5. Ventas incompletas ──────────────────────────────────────────────────────


def stock(lots, sales, listed=True):
    asset = Asset(key="T", symbol="TEST", name="Test SA", asset_type="stock",
                  platform="DEGIRO", currency="EUR", isin="ES0000000001", listed=listed)
    asset.lots = [Lot(day=d, quantity=q, cost_eur=c, source="degiro") for d, q, c in lots]
    asset.sales = [Sale(day=d, quantity=q, proceeds_eur=p, fee_eur=0, platform="DEGIRO") for d, q, p in sales]
    return asset


def year_for(asset):
    with tempfile.TemporaryDirectory() as out:
        (year,) = [y for y in build_years({"T": asset}, Path(out), [], [], []) if y["capitalGains"]["byAsset"]]
    return year


class IncompleteSales(unittest.TestCase):
    def test_uncovered_sale_blocks_its_section(self):
        asset = stock([("2025-01-10", 5, 500)], [("2025-06-10", 8, 1200)])
        year = year_for(asset)
        disposal = year["capitalGains"]["byAsset"][0]["disposals"][0]
        self.assertTrue(disposal["incomplete"])
        self.assertEqual(disposal["missingQuantity"], 3.0)
        pack = build_renta_pack(year)
        self.assertEqual(pack["blockedSections"], ["acciones-cotizadas"])
        entry = pack["sections"][0]["entries"][0]
        acquisition = {f["label"]: f for f in entry["fields"]}["Valor de adquisición"]
        self.assertEqual(acquisition["format"], "missing")
        self.assertEqual(acquisition["value"], "Faltan compras de 3 acciones")
        md = render_renta_markdown(pack)
        self.assertIn("No rellenes este apartado", md)
        self.assertIn("⛔ **Faltan compras de 3 acciones**", md)

    def test_precision_dust_is_not_a_missing_purchase(self):
        asset = Asset(key="C", symbol="ETH", name="ETH", asset_type="crypto", platform="Revolut", currency="EUR")
        asset.lots.append(Lot(day="2026-01-10", quantity=D("2.969820865"), cost_eur=6000, source="bybit"))
        asset.sales.append(Sale(day="2026-09-11", quantity=D("2.96982087"), proceeds_eur=9000, fee_eur=0,
                                platform="Revolut"))
        (disposal,), warnings = match_fifo(asset)
        self.assertFalse(disposal["incomplete"])
        self.assertEqual(disposal["dustQuantity"], 5e-9)
        self.assertEqual(warnings, [])


# ── 6. Decimal ─────────────────────────────────────────────────────────────────


class Rounding(unittest.TestCase):
    def test_half_up(self):
        self.assertEqual(cents("0.125"), D("0.13"))
        self.assertEqual(cents("2.675"), D("2.68"))  # float 2.675 is 2.67499…

    def test_acquisition_rounded_once_from_exact_lots(self):
        """Three lots of 0,335 €: rounding each first gives 3 × 0,34 = 1,02 €;
        the exact sum is 1,005 €, declared as 1,01 €."""
        asset = stock([("2025-01-01", 1, "0.335"), ("2025-01-02", 1, "0.335"), ("2025-01-03", 1, "0.335")],
                      [("2025-06-01", 3, 5)])
        (disposal,), _ = match_fifo(asset)
        self.assertEqual([p["acquisitionValueEur"] for p in disposal["parcels"]], [0.34, 0.34, 0.34])
        self.assertEqual(disposal["acquisitionValueEur"], 1.01)
        self.assertEqual(disposal["resultEur"], 3.99)

    def test_listed_window_is_two_months_unlisted_a_year(self):
        lots = [("2025-01-10", 10, 1000), ("2025-09-01", 10, 1000)]
        sales = [("2025-04-10", 10, 500)]
        listed = stock(lots, sales)
        (d1,), _ = match_fifo(listed)
        flag_two_month_rule(listed, [d1])
        self.assertFalse(d1["deferred"])
        unlisted = stock(lots, sales, listed=False)
        (d2,), _ = match_fifo(unlisted)
        notes = flag_two_month_rule(unlisted, [d2])
        self.assertTrue(d2["deferred"])
        self.assertEqual(d2["deferralWindowMonths"], 12)
        self.assertIn("regla del año", notes[0][1]["message"])


# ── 7. Tipo de cambio ──────────────────────────────────────────────────────────

ECB_CSV = """Date,USD,JPY,CHF,
2026-01-06,1.1000,170.00,0.9300,
2026-01-02,1.0000,N/A,0.9000,
"""


class EcbFallback(unittest.TestCase):
    def test_parse_and_previous_publication(self):
        series = ecb.parse_csv(ECB_CSV)
        self.assertEqual(ecb.lookup(series, "USD", "2026-01-05"), (1.0, "2026-01-02"))
        self.assertEqual(ecb.lookup(series, "USD", "2026-01-06"), (1.1, "2026-01-06"))
        self.assertIsNone(ecb.lookup(series, "JPY", "2026-01-05"))
        self.assertIsNone(ecb.lookup(series, "USD", "2025-12-31"))

    def test_history_rate_in_eur_per_unit(self):
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / "hist.csv"
            path.write_text(ECB_CSV)
            history = ecb.EcbHistory(path)
            history._refreshed = True  # no download in tests
            rate, day = history.rate("CHF", "2026-01-03")
            self.assertAlmostEqual(rate, 1 / 0.9)
            self.assertEqual(day, "2026-01-02")

    def test_misread_broker_conversion_falls_back_to_ecb(self):
        trades = [
            {"symbol": "AAPL", "date": "2026-01-05", "currency": "USD", "price": 200.0, "quantity": 10,
             "totalEur": -1850.0, "totalPlusFeesEur": -1852.0},  # 0.925 vs ECB 0.92: fine
            {"symbol": "AAPL", "date": "2026-01-05", "currency": "USD", "price": 200.0, "quantity": 10,
             "totalEur": -2000.0, "totalPlusFeesEur": -2002.0},  # 1.0: 8.7 % off
        ]
        warnings = check_degiro_fx(trades, fx=lambda c, d: (0.92, "ecb", d))
        self.assertEqual(len(warnings), 1)
        self.assertEqual(trades[0]["totalEur"], -1850.0)
        self.assertEqual(trades[1]["totalEur"], -1840.0)
        self.assertEqual(trades[1]["totalPlusFeesEur"], -1842.0)


# ── 9. Anexo ───────────────────────────────────────────────────────────────────


class Annex(unittest.TestCase):
    def test_one_block_per_sale_with_its_lots(self):
        asset = stock([("2024-02-05", 10, 7000), ("2025-02-03", 10, 8000)], [("2026-04-06", 15, 13500)])
        asset.lots[0].price, asset.lots[0].currency = D(700), "EUR"
        year = year_for(asset)
        pack = build_renta_pack(year)
        (sale,) = pack["annex"]["sales"]
        self.assertEqual([lot["quantity"] for lot in sale["lots"]], [10.0, 5.0])
        self.assertEqual(sale["lots"][1]["lotQuantity"], 10.0)
        md = render_renta_markdown(pack)
        self.assertIn("## Anexo — justificación de cada venta", md)
        self.assertEqual(md.count("Art. 37.2 LIRPF"), 1)
        self.assertIn("5 de 10 compradas el 03/02/2025", md)


# ── 10. Casillas ───────────────────────────────────────────────────────────────

DICTIONARY = """\
ECIVIL=[/DatosIdentificativos/Declarante/ECIVIL][X][*06-09][Estado Civil (a 31/12/2025)]
G2B_DE=[/DatosEconomicos/TomaDatosAmpliada/GPAcciones/EntidadAccion/G2B_DE][X][0327][Denominación de los valores transmitidos (entidad emisora)]
G2B_A=[/DatosEconomicos/TomaDatosAmpliada/GPAcciones/EntidadAccion/G2B_A/@valor][P102][0328][Importe global de las transmisiones efectuadas en 2025]
G2B_B=[/DatosEconomicos/TomaDatosAmpliada/GPAcciones/EntidadAccion/G2B_B][P102][0331][Valor de adquisición global de los valores transmitidos]
G2B_D=[/x/G2B_D][P102][0336][Ganancias patrimoniales reducidas no exentas ( [0332] - [0333] - [0335] )]
HSALDO44B=[/r/HSALDO44B][P102][0441][Saldos netos negativos de ganancias y pérdidas patrimoniales de 2023, pendientes de compensación a 1 de enero de 2025, a integrar en la base imponible del ahorro (Cumplimente el anexo C.3)]
HSALDO57B=[/r/HSALDO57B][P102][0455][Resto de saldos netos negativos de ganancias y pérdidas patrimoniales de 2023, pendientes de compensación a 1 de enero de 2025, a integrar en la base imponible del ahorro, con el límite del 25 por 100 de [0429] (*) (saldo pendiente no compensado en la casilla [0441]) (Cumplimente el anexo C.3)]
"""


class Casillas(unittest.TestCase):
    def test_nested_brackets_stay_in_the_label(self):
        groups = split_groups("[/x][P102][0336][Ganancias ( [0332] - [0333] )]")
        self.assertEqual(groups[3], "Ganancias ( [0332] - [0333] )")
        fields = parse_dictionary(DICTIONARY)
        self.assertEqual(fields[4].casilla, "0336")

    def test_sections_verified_only_when_every_field_is_found(self):
        dictionary = Dictionary(2025, parse_dictionary(DICTIONARY))
        self.assertTrue(dictionary.matches_year)
        asset = stock([("2025-01-10", 10, 1000)], [("2025-06-10", 10, 1500)])
        pack = build_renta_pack(year_for(asset))
        pack["fiscalYear"] = 2025
        pack["sections"].append({
            "id": "saldos-pendientes", "entries": [{"id": "s", "fields": [
                {"label": "own", "value": 1.0, "format": "eur", "block": "gp", "origin": 2023, "cross": False},
                {"label": "cross", "value": 1.0, "format": "eur", "block": "gp", "origin": 2023, "cross": True},
            ]}],
        })
        annotate_pack(pack, dictionary)
        acciones, saldos = pack["sections"][0], pack["sections"][-1]
        self.assertTrue(acciones["verified"])
        boxes = {f["label"]: f.get("casilla") for f in acciones["entries"][0]["fields"]}
        self.assertEqual(boxes["Valor de transmisión"], "0328")
        self.assertEqual(boxes["Valor de adquisición"], "0331")
        self.assertEqual(boxes["Fecha de transmisión"], None)
        self.assertIn("0328", acciones["casillaHint"])
        self.assertTrue(saldos["verified"])
        self.assertEqual([f["casilla"] for f in saldos["entries"][0]["fields"]], ["0441", "0455"])

    def test_wrong_year_is_rejected(self):
        self.assertFalse(Dictionary(2026, parse_dictionary(DICTIONARY)).matches_year)


if __name__ == "__main__":
    unittest.main()
