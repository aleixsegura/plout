#!/usr/bin/env python3
"""Tests for the Revolut account-history export (plout.exporters.revolut).

They pin the classification rules that decide what counts as spending — a
transfer to your own broker or savings account booked as an expense would
inflate every monthly total — and the month/interest aggregation the web app
charts. Run them after touching classify_transaction, build_transactions,
build_months or build_interest:

    .venv/bin/python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.exporters.revolut import (
    Currencies,
    build_interest,
    build_months,
    build_transactions,
    classify_transaction,
)

CURRENT = "pocket-current"
SAVINGS = "pocket-savings"
REVX = "pocket-revx"
OWN = {CURRENT, SAVINGS, REVX}
NAMES = {"jane doe smith", "doe smith jane"}
CCY = Currencies([[{"isoCode": "EUR", "exponent": 2, "isCrypto": False}]])

DAY = 1_720_000_000_000  # ms epoch, a day in July 2024


def leg(
    type_,
    amount,
    *,
    account=CURRENT,
    account_type="CURRENT",
    key="transaction.description.generic.name",
    description="",
    category="transfers",
    state="COMPLETED",
    id_="tx-1",
    leg_id=None,
    ts=DAY,
    fee=0,
    balance=None,
    **extra,
):
    row = {
        "id": id_,
        "legId": leg_id or f"{id_}-0000",
        "type": type_,
        "state": state,
        "amount": amount,
        "fee": fee,
        "currency": "EUR",
        "category": category,
        "tag": category,
        "description": description,
        "localisedDescription": {"key": key, "params": []},
        "account": {"id": account, "type": account_type},
        "startedDate": ts,
        **extra,
    }
    if balance is not None:
        row["balance"] = balance
    return row


def classify(row):
    return classify_transaction(row, own_pocket_ids=OWN, own_names=NAMES)


class ClassificationTest(unittest.TestCase):
    def test_card_payment_is_an_expense(self):
        row = leg("CARD_PAYMENT", -1900, key="transaction.description.card.payment.to.merchant",
                  category="restaurants", merchant={"name": "Bar"})
        self.assertEqual(classify(row), "expense")

    def test_zero_amount_card_hold_is_skipped(self):
        row = leg("CARD_PAYMENT", 0, key="transaction.description.card.payment.to.merchant",
                  description="Google *temporary Hold")
        self.assertIsNone(classify(row))

    def test_pending_is_skipped(self):
        self.assertIsNone(classify(leg("CARD_PAYMENT", -500, state="PENDING")))

    def test_card_refund(self):
        self.assertEqual(classify(leg("CARD_PAYMENT", 500)), "refund")
        self.assertEqual(classify(leg("CARD_REFUND", 500)), "refund")

    def test_bizum_in_is_income_and_out_is_expense(self):
        self.assertEqual(
            classify(leg("TOPUP", 2200, key="transaction.description.top.up.from.apm.bizum",
                         category="topup")),
            "income",
        )
        self.assertEqual(classify(leg("TRANSFER", -1500, description="Bizum payment to: Alex M.")),
                         "expense")

    def test_transfer_to_own_broker_is_internal(self):
        self.assertEqual(classify(leg("TRANSFER", -50000, description="To Stichting Degiro")),
                         "transfer")

    def test_transfer_to_and_from_own_bank_account_is_internal(self):
        self.assertEqual(classify(leg("TRANSFER", -100000, description="To Jane Doe Smith")),
                         "transfer")
        self.assertEqual(
            classify(leg("TOPUP", 250000, description="Payment from Jane Doe Smith",
                         category="topup", payerName="JANE DOE SMITH")),
            "transfer",
        )

    def test_boosted_and_revx_moves_are_internal(self):
        self.assertEqual(
            classify(leg("TRANSFER", -200000, key="transaction.description.arrow.personal.custom",
                         description="To Boosted account")),
            "transfer",
        )
        self.assertEqual(
            classify(leg("REVX_TRANSFER", -20000, key="transaction.description.arrow.personal.revx",
                         category="crypto")),
            "transfer",
        )

    def test_interest(self):
        self.assertEqual(classify(leg("INTEREST", 42, account=SAVINGS, category="")), "interest")
        self.assertEqual(
            classify(leg("TOPUP", 42, account=SAVINGS,
                         key="transaction.description.savings.interest.paid")),
            "interest",
        )

    def test_boosted_interest_row_as_revolut_sends_it(self):
        row = leg("TRANSFER", 150, account=SAVINGS, account_type="SAVINGS_EXTERNAL",
                  key="vaults.saving_accounts.interest.gained", category="interest",
                  description="Interest earned - Boosted account")
        self.assertEqual(classify(row), "interest")

    def test_plan_fee_charge_is_a_fee(self):
        row = leg("CHARGE", 0, fee=299, key="transaction.description.fee.plan.subscription",
                  description="Premium plan fee", category="general")
        self.assertEqual(classify(row), "fee")
        txs, _ = build_transactions([row], [], CCY, current_pocket_id=CURRENT, own_pocket_ids=OWN)
        self.assertEqual([(t["kind"], t["amount"], t["fee"], t["category"]) for t in txs],
                         [("fee", -2.99, 0.0, "fees")])

    def test_unknown_type_is_other(self):
        self.assertEqual(classify(leg("MYSTERY", -100)), "other")


class BuildTest(unittest.TestCase):
    def test_far_leg_of_internal_move_is_dropped(self):
        near = leg("TRANSFER", -200000, key="transaction.description.arrow.personal.custom",
                   id_="mv", leg_id="mv-0000")
        far = leg("TRANSFER", 200000, key="transaction.description.arrow.personal.custom",
                  id_="mv", leg_id="mv-0001", account=SAVINGS, account_type="SAVINGS_EXTERNAL")
        txs, ignored = build_transactions([near, far], [], CCY, current_pocket_id=CURRENT,
                                          own_pocket_ids=OWN)
        self.assertEqual([t["kind"] for t in txs], ["transfer"])
        self.assertEqual(txs[0]["amount"], -2000.0)
        self.assertEqual(ignored, {})

    def test_fee_becomes_its_own_row(self):
        row = leg("TRANSFER", -10000, description="Bizum payment to: X", fee=50)
        txs, _ = build_transactions([row], [], CCY, current_pocket_id=CURRENT, own_pocket_ids=OWN)
        kinds = sorted((t["kind"], t["amount"]) for t in txs)
        self.assertEqual(kinds, [("expense", -100.0), ("fee", -0.5)])

    def test_savings_rows_keep_only_interest(self):
        interest = leg("INTEREST", 42, account=SAVINGS, category="", id_="i1", balance=250075)
        deposit = leg("TRANSFER", 200000, account=SAVINGS, id_="d1",
                      key="transaction.description.arrow.personal.custom")
        txs, ignored = build_transactions([], [interest, deposit], CCY, current_pocket_id=CURRENT,
                                          own_pocket_ids=OWN)
        self.assertEqual([(t["kind"], t["account"], t["balance"]) for t in txs],
                         [("interest", "savings", 2500.75)])
        self.assertEqual(ignored, {"savings:TRANSFER:not-interest": 1})

    def test_months_and_interest(self):
        rows = [
            leg("CARD_PAYMENT", -1900, category="restaurants", id_="a", ts=DAY),
            leg("CARD_PAYMENT", 400, category="restaurants", id_="b", ts=DAY + 1),
            leg("CARD_PAYMENT", -2000, category="groceries", id_="c", ts=DAY + 2),
            leg("TOPUP", 10000, category="topup", id_="d", ts=DAY + 3,
                key="transaction.description.top.up.from.apm.bizum"),
            leg("TRANSFER", -5000, description="To Stichting Degiro", id_="e", ts=DAY + 4),
            leg("TRANSFER", -1000, description="Bizum payment to: X", id_="f", ts=DAY + 5, fee=10),
        ]
        savings = [
            leg("INTEREST", 42, account=SAVINGS, category="", id_="i1", ts=DAY, balance=100000),
            leg("INTEREST", 43, account=SAVINGS, category="", id_="i2",
                ts=DAY + 24 * 3600 * 1000, balance=100042),
        ]
        txs, _ = build_transactions(rows, savings, CCY, current_pocket_id=CURRENT, own_pocket_ids=OWN)
        months = build_months(txs)
        self.assertEqual(len(months), 1)
        m = months[0]
        # 19 − 4 + 20 + 10 = 45 spent; the DEGIRO transfer and the fee stay out.
        self.assertEqual(m["expenses"], 45.0)
        self.assertEqual(m["income"], 100.0)
        self.assertEqual(m["fees"], 0.1)
        self.assertEqual(m["interest"], 0.85)
        self.assertEqual(m["net"], round(100 + 0.85 - 45 - 0.1, 2))
        self.assertEqual(m["byCategory"], {"groceries": 20.0, "restaurants": 15.0, "transfers": 10.0})
        self.assertEqual(m["count"], 5)  # 3 card rows + bizum in + bizum out

        interest = build_interest(txs)
        self.assertEqual(interest["total"], 0.85)
        self.assertEqual(len(interest["daily"]), 2)
        self.assertEqual(interest["daily"][0]["balanceEur"], 1000.0)
        self.assertEqual(interest["byMonth"][0]["amount"], 0.85)


if __name__ == "__main__":
    unittest.main()
