"""Generates synthetic demo fixtures into demo-data/, mirroring the shape of
data/*.json without touching real data. Ad-hoc script for a live demo /
issue #13 (synthetic fixtures) — not wired into the regular export pipeline.
"""
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo-data"
OUT.mkdir(exist_ok=True)

TODAY = date(2026, 9, 15)
FX = 1.08  # EUR->USD: 1 EUR = 1.08 USD


def usd_to_eur(x):
    return round(x / FX, 2)


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def ts(d: date, hour=9, minute=30) -> str:
    return datetime(d.year, d.month, d.day, hour, minute).isoformat()


def w(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Positions (DEGIRO)
# ---------------------------------------------------------------------------
POSITIONS_RAW = [
    dict(id="1001", symbol="AAPL", name="Apple Inc", productType="STOCK",
         quantity=20, avgPrice=165.30, currency="USD", lastPrice=231.50,
         exchange="NASDAQ", fx=1.11),
    dict(id="1002", symbol="MSFT", name="Microsoft Corp", productType="STOCK",
         quantity=12, avgPrice=310.00, currency="USD", lastPrice=421.80,
         exchange="NASDAQ", fx=1.10),
    dict(id="1003", symbol="NVDA", name="NVIDIA Corp", productType="STOCK",
         quantity=35, avgPrice=45.20, currency="USD", lastPrice=118.90,
         exchange="NASDAQ", fx=1.09),
    dict(id="1004", symbol="ASML", name="ASML Holding NV", productType="STOCK",
         quantity=8, avgPrice=620.00, currency="EUR", lastPrice=712.40,
         exchange="Euronext Amsterdam", fx=None),
    dict(id="1005", symbol="VWCE.DE", name="Vanguard FTSE All-World ETF", productType="ETF",
         quantity=70, avgPrice=98.40, currency="EUR", lastPrice=118.75,
         exchange="Xetra", fx=None),
    dict(id="1006", symbol="O", name="Realty Income Corp", productType="STOCK",
         quantity=45, avgPrice=54.10, currency="USD", lastPrice=58.30,
         exchange="NYSE", fx=1.12),
]

positions = []
total_value_eur = 0.0
total_pl_eur = 0.0
total_today_pl_eur = 0.0
for i, p in enumerate(POSITIONS_RAW):
    rate = p["fx"] or FX
    cost_native = p["quantity"] * p["avgPrice"]
    value_native = p["quantity"] * p["lastPrice"]
    cost_eur = round(cost_native / rate, 2) if p["currency"] == "USD" else round(cost_native, 2)
    value_eur = round(value_native / rate, 2) if p["currency"] == "USD" else round(value_native, 2)
    pl_eur = round(value_eur - cost_eur, 2)
    today_pl_eur = round(value_eur * random.uniform(-0.012, 0.018), 2)
    realized_pl_eur = round(random.uniform(-50, 900), 2) if i % 2 == 0 else 0.0
    positions.append(dict(
        id=p["id"], symbol=p["symbol"], name=p["name"], productType=p["productType"],
        quantity=p["quantity"], avgPrice=p["avgPrice"], currency=p["currency"],
        lastPrice=p["lastPrice"], valueEur=value_eur, costEur=cost_eur, plEur=pl_eur,
        todayPlEur=today_pl_eur, realizedPlEur=realized_pl_eur,
        averageFxRate=p["fx"], exchange=p["exchange"],
    ))
    total_value_eur += value_eur
    total_pl_eur += pl_eur
    total_today_pl_eur += today_pl_eur

degiro_cash_eur = 1650.40
positions_file = dict(
    updatedAt=ts(TODAY),
    source="degiro",
    baseCurrency="EUR",
    summary=dict(
        totalValueEur=round(total_value_eur, 2),
        totalPlEur=round(total_pl_eur, 2),
        totalTodayPlEur=round(total_today_pl_eur, 2),
        cashEur=degiro_cash_eur,
        cashBreakdown=[dict(currency="EUR", amount=degiro_cash_eur, amountEur=degiro_cash_eur)],
        netLiquidationEur=round(total_value_eur + degiro_cash_eur, 2),
        netDepositsEur=22000.0,
        accountPlEur=round(total_pl_eur + 612.30, 2),
    ),
    positions=positions,
)
w("positions.json", positions_file)

# ---------------------------------------------------------------------------
# Revolut (cash + crypto)
# ---------------------------------------------------------------------------
btc_qty, btc_price, btc_avg = 0.07, 96500.0, 42000.0
eth_qty, eth_price, eth_avg = 1.1, 3350.0, 2100.0

def crypto_position(symbol, name, qty, price, avg_price):
    value_eur = usd_to_eur(qty * price)
    cost_eur = usd_to_eur(qty * avg_price)
    return dict(
        symbol=symbol, name=name, quantity=qty, currency=symbol,
        lastPrice=price, priceCurrency="USD", lastPriceEur=usd_to_eur(price),
        valueEur=value_eur, avgPrice=avg_price, avgPriceCurrency="USD",
        costEur=cost_eur, plEur=round(value_eur - cost_eur, 2),
        todayPlEur=round(value_eur * random.uniform(-0.02, 0.03), 2),
    )

revolut_positions = [
    crypto_position("BTC", "Bitcoin", btc_qty, btc_price, btc_avg),
    crypto_position("ETH", "Ethereum", eth_qty, eth_price, eth_avg),
]
# Net worth the demo dashboard shows (positions + crypto + all cash). The
# Revolut current account takes whatever is left, so the total is exact.
TARGET_NET_WORTH_EUR = 54839.64
SAVINGS_EUR = 8000.0
CURRENT_EUR = round(
    TARGET_NET_WORTH_EUR - round(total_value_eur, 2) - degiro_cash_eur - SAVINGS_EUR
    - sum(p["valueEur"] for p in revolut_positions),
    2,
)
assert CURRENT_EUR > 0, "positions alone exceed TARGET_NET_WORTH_EUR"
revolut_cash = [
    dict(id="cur-eur", name="Current EUR", kind="current", currency="EUR",
         amount=CURRENT_EUR, amountEur=CURRENT_EUR),
    dict(id="sav-eur", name="Savings EUR", kind="savings", currency="EUR",
         amount=SAVINGS_EUR, amountEur=SAVINGS_EUR, interestRate=3.2, aer=3.25,
         earnedInTotalEur=214.87),
]
total_cash_eur = round(sum(c["amountEur"] for c in revolut_cash), 2)
revolut_file = dict(
    updatedAt=ts(TODAY),
    source="revolut",
    baseCurrency="EUR",
    totalCashEur=total_cash_eur,
    totalTodayPlEur=round(sum(p["todayPlEur"] for p in revolut_positions), 2),
    cash=revolut_cash,
    positions=revolut_positions,
)
w("revolut.json", revolut_file)

# ---------------------------------------------------------------------------
# History (daily snapshots)
# ---------------------------------------------------------------------------
DAYS = 180
start = TODAY - timedelta(days=DAYS - 1)
base_portfolio = total_value_eur * 0.82
crypto_base = usd_to_eur(btc_qty * btc_price * 0.8 + eth_qty * eth_price * 0.8)
cash_base = degiro_cash_eur + total_cash_eur

history_days = []
drift = 0.00075
vol = 0.011
value = base_portfolio
crypto_value = crypto_base
for i in range(DAYS):
    d = start + timedelta(days=i)
    value *= (1 + drift + random.uniform(-vol, vol))
    crypto_value *= (1 + drift * 1.6 + random.uniform(-vol * 2.4, vol * 2.4))
    cash = round(cash_base * (0.85 + 0.15 * i / DAYS) + random.uniform(-80, 80), 2)
    degiro_val = round(value, 2)
    portfolio_value_eur = round(degiro_val + crypto_value, 2)
    history_days.append(dict(
        date=iso(d),
        degiro=dict(updatedAt=ts(d, 18, 0), valueEur=degiro_val,
                     cashEur=round(degiro_cash_eur * (0.7 + 0.3 * i / DAYS), 2)),
        revolut=dict(updatedAt=ts(d, 18, 5), cryptoValueEur=round(crypto_value, 2),
                      cashCurrentEur=round(CURRENT_EUR * (0.8 + 0.2 * i / DAYS), 2),
                      cashSavingsEur=round(SAVINGS_EUR * (0.7 + 0.3 * i / DAYS), 2)),
        portfolioValueEur=portfolio_value_eur,
        cashEur=cash,
        totalEur=round(portfolio_value_eur + cash, 2),
    ))
# Bend each series so it lands exactly on the live positions/revolut totals.
# The correction is spread geometrically over the whole range (day i gets
# ratio**(i/(DAYS-1))), so the curve keeps its random-walk shape and the
# endpoint matches the dashboard KPIs without a visible jump at the end.
target_degiro = total_value_eur
target_crypto = round(revolut_positions[0]["valueEur"] + revolut_positions[1]["valueEur"], 2)
target_cash = round(degiro_cash_eur + total_cash_eur, 2)
last = history_days[-1]
ratios = dict(
    degiro=target_degiro / last["degiro"]["valueEur"],
    crypto=target_crypto / last["revolut"]["cryptoValueEur"],
    cash=target_cash / last["cashEur"],
)
for i, day in enumerate(history_days):
    k = i / (DAYS - 1)
    day["degiro"]["valueEur"] = round(day["degiro"]["valueEur"] * ratios["degiro"] ** k, 2)
    day["revolut"]["cryptoValueEur"] = round(day["revolut"]["cryptoValueEur"] * ratios["crypto"] ** k, 2)
    day["cashEur"] = round(day["cashEur"] * ratios["cash"] ** k, 2)
    day["portfolioValueEur"] = round(day["degiro"]["valueEur"] + day["revolut"]["cryptoValueEur"], 2)
    day["totalEur"] = round(day["portfolioValueEur"] + day["cashEur"], 2)
# Running totals for the Holdings return chart (no random draws, so every
# other demo file stays byte-identical): a 500 € DEGIRO deposit at each month
# start landing on the live 22,000 €, no crypto trades, and Boosted interest
# at 2.5 % a year on the day's savings balance.
months_left = lambda d: (TODAY.year - d.year) * 12 + TODAY.month - d.month
interest = 0.0
for i, day in enumerate(history_days):
    d = start + timedelta(days=i)
    day["degiro"]["netDepositsEur"] = 22000.0 - 500.0 * months_left(d)
    interest += day["revolut"]["cashSavingsEur"] * 0.025 / 365
    day["revolut"]["netInvestedEur"] = 0.0
    day["revolut"]["interestEur"] = round(interest, 2)

w("history.json", dict(baseCurrency="EUR", days=history_days))

# ---------------------------------------------------------------------------
# Dividends
# ---------------------------------------------------------------------------
dividend_sources = [
    ("AAPL", "Apple Inc", "USD", 40, 0.26),
    ("MSFT", "Microsoft Corp", "USD", 25, 0.83),
    ("O", "Realty Income Corp", "USD", 80, 0.2635),
    ("VWCE.DE", "Vanguard FTSE All-World ETF", "EUR", 120, 0.42),
]
payments = []
d = TODAY - timedelta(days=365)
pid = 5001
while d <= TODAY:
    for sym, name, cur, qty, per_share in dividend_sources:
        # roughly quarterly per symbol, offset by hash of symbol
        offset = sum(ord(c) for c in sym) % 90
        if (d - (TODAY - timedelta(days=365))).days % 91 == offset % 91:
            gross = round(qty * per_share * random.uniform(0.92, 1.08), 2)
            tax = round(gross * 0.15, 2) if cur == "USD" else round(gross * 0.19, 2)
            net = round(gross - tax, 2)
            entry = dict(date=iso(d), productId=pid, symbol=sym, name=name,
                         currency=cur, gross=gross, tax=-tax, net=net)
            if cur == "USD":
                entry["grossEur"] = usd_to_eur(gross)
                entry["taxEur"] = -usd_to_eur(tax)
                entry["netEur"] = usd_to_eur(net)
            else:
                entry["grossEur"] = gross
                entry["taxEur"] = -tax
                entry["netEur"] = net
            payments.append(entry)
            pid += 1
    d += timedelta(days=1)

upcoming = [
    dict(payDate=iso(TODAY + timedelta(days=12)), name="Apple Inc",
         description="Dividendo trimestral", currency="USD", amount=10.40,
         amountEur=usd_to_eur(10.40)),
    dict(payDate=iso(TODAY + timedelta(days=28)), name="Realty Income Corp",
         description="Dividendo mensual", currency="USD", amount=21.08,
         amountEur=usd_to_eur(21.08)),
]

w("dividends.json", dict(
    updatedAt=ts(TODAY), source="degiro",
    fromDate=iso(TODAY - timedelta(days=365)), toDate=iso(TODAY),
    payments=sorted(payments, key=lambda p: p["date"]), upcoming=upcoming,
))

# ---------------------------------------------------------------------------
# Market caps
# ---------------------------------------------------------------------------
MARKET_CAPS = {
    "AAPL": (3510000000000, "USD"), "MSFT": (3130000000000, "USD"),
    "NVDA": (2910000000000, "USD"), "ASML": (312000000000, "EUR"),
    "ASML.AS": (312000000000, "EUR"), "O": (49800000000, "USD"),
    "BTC": (1900000000000, "USD"), "ETH": (402000000000, "USD"),
}
entries = {
    sym: dict(marketCap=cap, currency=cur, source="yahoo", at=ts(TODAY))
    for sym, (cap, cur) in MARKET_CAPS.items()
}
w("marketcaps.json", dict(updatedAt=ts(TODAY), source="yahoo", unresolved=[], entries=entries))

# ---------------------------------------------------------------------------
# Taxes
# ---------------------------------------------------------------------------
def parcel(qty, acq_date, acq_val, buy_fees, disp_val, sell_fees, source="degiro", note=None):
    return dict(quantity=qty, acquisitionDate=acq_date, acquisitionValueEur=acq_val,
                buyFeesEur=buy_fees, disposalValueEur=disp_val, sellFeesEur=sell_fees,
                resultEur=round(disp_val - acq_val, 2), source=source, note=note,
                fxRate=None, fxSource=None)

def disposal(date_, qty, platform, acq_val, buy_fees, disp_val, sell_fees,
             deferred=False, deferred_because=None, deferred_qty=0, deferred_loss=0.0,
             reintegrated_loss=0.0, reintegrated_from=None, parcels=None):
    result = round(disp_val - acq_val, 2)
    computable = round(result - deferred_loss, 2)
    return dict(
        date=date_, quantity=qty, platform=platform, ref=None, note=None,
        acquisitionValueEur=acq_val, buyFeesEur=buy_fees, disposalValueEur=disp_val,
        sellFeesEur=sell_fees, resultEur=result, deferred=deferred,
        deferredBecause=deferred_because or [], deferredQuantity=deferred_qty,
        deferredLossEur=deferred_loss, computableResultEur=computable,
        reintegratedLossEur=reintegrated_loss, reintegratedFrom=reintegrated_from or [],
        parcels=parcels or [parcel(qty, date_, acq_val, buy_fees, disp_val, sell_fees)],
    )

def asset(key, symbol, name, asset_type, platform, currency, qty, remaining,
          acq_date, acq_val, disp_date, disp_val, buy_fees, sell_fees, disposals):
    result = round(disp_val - acq_val, 2)
    deferred_total = round(sum(d["deferredLossEur"] for d in disposals), 2)
    reint_total = round(sum(d["reintegratedLossEur"] for d in disposals), 2)
    return dict(
        key=key, symbol=symbol, name=name, assetType=asset_type, platform=platform,
        isin=None, currency=currency, quantity=qty, remainingQuantity=remaining,
        fullExit=remaining == 0, acquisitionDate=acq_date, acquisitionDates=[acq_date],
        acquisitionValueEur=acq_val, disposalDate=disp_date, disposalDates=[disp_date],
        disposalValueEur=disp_val, buyFeesEur=buy_fees, sellFeesEur=sell_fees,
        resultEur=result, computableResultEur=round(result - deferred_total, 2),
        reintegratedLossEur=reint_total, deferredLossEur=deferred_total,
        deferred=deferred_total > 0, review=False, disposals=disposals,
    )

# 2025 (closed year)
aapl_disp = disposal("2025-06-18", 15, "degiro", 2100.00, 4.5, 3050.80, 5.2)
nvda_disp = disposal(
    "2025-03-04", 20, "degiro", 1850.00, 3.0, 1420.00, 3.0,
    deferred=True, deferred_because=["regla-dos-meses"], deferred_qty=20,
    deferred_loss=430.0,
)
btc_disp = disposal("2025-11-22", 0.05, "bybit", 1620.00, 0.0, 2380.00, 0.0, parcels=[
    parcel(0.05, "2024-08-10", 1620.00, 0.0, 2380.00, 0.0, source="bybit"),
])

assets_2025 = [
    asset("aapl-2025", "AAPL", "Apple Inc", "stock", "degiro", "USD", 15, 0,
          "2023-01-12", 2100.00, "2025-06-18", 3050.80, 4.5, 5.2, [aapl_disp]),
    asset("nvda-2025", "NVDA", "NVIDIA Corp", "stock", "degiro", "USD", 20, 40,
          "2025-01-20", 1850.00, "2025-03-04", 1420.00, 3.0, 3.0, [nvda_disp]),
    asset("btc-2025", "BTC", "Bitcoin", "crypto", "bybit", "USD", 0.05, 0.135,
          "2024-08-10", 1620.00, "2025-11-22", 2380.00, 0.0, 0.0, [btc_disp]),
]

div_payments_2025 = [
    dict(date="2025-02-14", symbol="AAPL", name="Apple Inc", currency="USD",
         grossEur=95.20, withholdingEur=14.28, netEur=80.92),
    dict(date="2025-05-16", symbol="O", name="Realty Income Corp", currency="USD",
         grossEur=196.40, withholdingEur=29.46, netEur=166.94),
    dict(date="2025-08-15", symbol="VWCE.DE", name="Vanguard FTSE All-World ETF", currency="EUR",
         grossEur=48.60, withholdingEur=9.23, netEur=39.37),
]

tax_year_2025 = dict(
    year="2025", filedIn="2026", open=False,
    capitalGains=dict(
        totalGainEur=round(aapl_disp["resultEur"] + btc_disp["resultEur"], 2),
        totalLossEur=abs(nvda_disp["resultEur"]),
        netEur=round(aapl_disp["computableResultEur"] + btc_disp["computableResultEur"]
                      + nvda_disp["computableResultEur"], 2),
        deferredLossEur=430.0, reintegratedLossEur=0.0,
        estimatedTaxEur=round(
            (aapl_disp["computableResultEur"] + btc_disp["computableResultEur"]
             + nvda_disp["computableResultEur"]) * 0.19, 2
        ),
        byAsset=assets_2025,
    ),
    investmentIncome=dict(
        dividends=dict(
            grossEur=round(sum(p["grossEur"] for p in div_payments_2025), 2),
            withholdingEur=round(sum(p["withholdingEur"] for p in div_payments_2025), 2),
            netEur=round(sum(p["netEur"] for p in div_payments_2025), 2),
            payments=div_payments_2025,
        ),
        staking=dict(grossEur=42.80, approximate=True, rewards=[
            dict(date="2025-09-30", platform="bybit", symbol="ETH", quantity=0.012,
                 valueEur=42.80, approximate=True, priceSource="coingecko"),
        ]),
    ),
    checks=[
        dict(id="modelo-721", level="info",
             params=dict(threshold=50000), message="Sin obligación de modelo 721 este año."),
    ],
    warnings=[
        dict(id="regla-dos-meses", level="warn",
             params=dict(symbol="NVDA", amountEur=430.0),
             message="Pérdida de 430,00 € en NVDA diferida por la regla de los dos meses."),
    ],
)

# 2026 (open year, partial)
aapl_disp_26 = disposal("2026-02-10", 10, "degiro", 1653.00, 3.0, 2015.40, 3.5)
assets_2026 = [
    asset("aapl-2026", "AAPL", "Apple Inc", "stock", "degiro", "USD", 10, 15,
          "2023-01-12", 1653.00, "2026-02-10", 2015.40, 3.0, 3.5, [aapl_disp_26]),
]
div_payments_2026 = [
    dict(date="2026-02-14", symbol="AAPL", name="Apple Inc", currency="USD",
         grossEur=101.40, withholdingEur=15.21, netEur=86.19),
    dict(date="2026-05-16", symbol="O", name="Realty Income Corp", currency="USD",
         grossEur=204.90, withholdingEur=30.74, netEur=174.16),
]
tax_year_2026 = dict(
    year="2026", filedIn="2027", open=True,
    capitalGains=dict(
        totalGainEur=aapl_disp_26["resultEur"], totalLossEur=0.0,
        netEur=aapl_disp_26["computableResultEur"], deferredLossEur=0.0,
        reintegratedLossEur=0.0,
        estimatedTaxEur=round(aapl_disp_26["computableResultEur"] * 0.19, 2),
        byAsset=assets_2026,
    ),
    investmentIncome=dict(
        dividends=dict(
            grossEur=round(sum(p["grossEur"] for p in div_payments_2026), 2),
            withholdingEur=round(sum(p["withholdingEur"] for p in div_payments_2026), 2),
            netEur=round(sum(p["netEur"] for p in div_payments_2026), 2),
            payments=div_payments_2026,
        ),
        staking=dict(grossEur=0.0, approximate=False, rewards=[]),
    ),
    checks=[], warnings=[],
)

trades = []
tid = 1
for a in assets_2025 + assets_2026:
    trades.append(dict(id=tid, platform=a["platform"], date=a["acquisitionDate"], side="buy",
                        symbol=a["symbol"], name=a["name"], assetType=a["assetType"],
                        currency=a["currency"], quantity=a["quantity"],
                        price=round(a["acquisitionValueEur"] / a["quantity"], 2),
                        totalEur=a["acquisitionValueEur"],
                        totalPlusFeesEur=round(a["acquisitionValueEur"] + a["buyFeesEur"], 2),
                        feesEur=a["buyFeesEur"]))
    tid += 1
    for dsp in a["disposals"]:
        trades.append(dict(id=tid, platform=a["platform"], date=dsp["date"], side="sell",
                            symbol=a["symbol"], name=a["name"], assetType=a["assetType"],
                            currency=a["currency"], quantity=dsp["quantity"],
                            price=round(dsp["disposalValueEur"] / dsp["quantity"], 2),
                            totalEur=dsp["disposalValueEur"],
                            totalPlusFeesEur=round(dsp["disposalValueEur"] - dsp["sellFeesEur"], 2),
                            feesEur=dsp["sellFeesEur"]))
        tid += 1

w("taxes.json", dict(
    updatedAt=ts(TODAY), source="degiro+bybit", baseCurrency="EUR",
    fromDate="2025-01-01", toDate=iso(TODAY),
    years=[tax_year_2026, tax_year_2025], trades=trades,
))

# ---------------------------------------------------------------------------
# Revolut transactions
# ---------------------------------------------------------------------------
CATEGORIES = ["groceries", "restaurants", "transport", "shopping", "utilities",
              "entertainment", "health", "travel"]
MERCHANTS = {
    "groceries": ["Mercadona", "Carrefour", "Lidl"],
    "restaurants": ["Cafeteria Central", "Sushi Bar", "La Taberna"],
    "transport": ["TMB", "Renfe", "Uber"],
    "shopping": ["Zara", "MediaMarkt", "Amazon"],
    "utilities": ["Endesa", "Vodafone", "Aigues de Barcelona"],
    "entertainment": ["Netflix", "Spotify", "Cinesa"],
    "health": ["Farmacia", "Dentista"],
    "travel": ["Vueling", "Booking.com"],
}

transactions = []
months_acc = {}
d = TODAY - timedelta(days=150)
txid = 1
while d <= TODAY:
    n_tx = random.randint(0, 3)
    for _ in range(n_tx):
        cat = random.choice(CATEGORIES)
        merchant = random.choice(MERCHANTS[cat])
        amount = -round(random.uniform(4, 95), 2)
        month_key = d.strftime("%Y-%m")
        m = months_acc.setdefault(month_key, dict(expenses=0.0, income=0.0, interest=0.0,
                                                    fees=0.0, count=0, byCategory={}))
        m["expenses"] += -amount
        m["count"] += 1
        m["byCategory"][cat] = round(m["byCategory"].get(cat, 0) + -amount, 2)
        transactions.append(dict(
            id=f"tx{txid}", legId=f"leg{txid}", ts=int(datetime(d.year, d.month, d.day, 12).timestamp() * 1000),
            date=ts(d, 12, random.randint(0, 59)), type="CARD_PAYMENT", kind="expense",
            category=cat, merchant=merchant, description=merchant, amount=amount,
            amountEur=amount, fee=0.0, currency="EUR", account="current",
            cardLastFour="4821",
        ))
        txid += 1
    if d.day == 28:
        month_key = d.strftime("%Y-%m")
        m = months_acc.setdefault(month_key, dict(expenses=0.0, income=0.0, interest=0.0,
                                                    fees=0.0, count=0, byCategory={}))
        m["income"] += 2650.0
        m["count"] += 1
        transactions.append(dict(
            id=f"tx{txid}", legId=f"leg{txid}", ts=int(datetime(d.year, d.month, d.day, 9).timestamp() * 1000),
            date=ts(d, 9, 0), type="TOPUP", kind="income", category="income",
            merchant=None, description="Nomina", amount=2650.0, amountEur=2650.0,
            fee=0.0, currency="EUR", account="current",
        ))
        txid += 1
    d += timedelta(days=1)

interest_daily = []
d = TODAY - timedelta(days=150)
bal = 11800.0
while d <= TODAY:
    amt = round(bal * (0.032 / 365), 4)
    bal += amt
    interest_daily.append(dict(date=iso(d), amount=round(amt, 2), balanceEur=round(bal, 2)))
    month_key = d.strftime("%Y-%m")
    m = months_acc.setdefault(month_key, dict(expenses=0.0, income=0.0, interest=0.0,
                                                fees=0.0, count=0, byCategory={}))
    m["interest"] = round(m["interest"] + amt, 2)
    d += timedelta(days=1)

months = []
for month_key, m in sorted(months_acc.items(), reverse=True):
    net = round(m["income"] + m["interest"] - m["expenses"] - m["fees"], 2)
    months.append(dict(month=month_key, expenses=round(m["expenses"], 2),
                        income=round(m["income"], 2), interest=round(m["interest"], 2),
                        fees=round(m["fees"], 2), net=net, count=m["count"],
                        byCategory=dict(sorted(m["byCategory"].items(), key=lambda kv: -kv[1]))))

w("revolut_transactions.json", dict(
    updatedAt=ts(TODAY), source="revolut", currency="EUR",
    fromDate=iso(TODAY - timedelta(days=150)), toDate=iso(TODAY), pageParam=None,
    accounts=dict(
        current=dict(id="rev-current", currency="EUR"),
        savings=dict(id="rev-savings", name="Savings EUR", since="2024-03-01",
                     balanceEur=SAVINGS_EUR, interestRate=3.2, aer=3.25, earnedInTotalEur=214.87),
    ),
    transactions=sorted(transactions, key=lambda t: -t["ts"]),
    months=months,
    interest=dict(daily=interest_daily,
                   byMonth=[dict(month=mk, amount=m["interest"]) for mk, m in sorted(months_acc.items())],
                   total=round(sum(i["amount"] for i in interest_daily), 2)),
    ignoredTypes={"CASHBACK": 3},
))

# ---------------------------------------------------------------------------
# Symbols (sector classification, see web/src/symbolConfig.ts)
# ---------------------------------------------------------------------------
w("symbols.json", dict(
    sectors={
        "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
        "ASML": "Technology",
        "O": "Financials",  # VWCE.DE (all-world ETF) deliberately left as Other
    },
))

# ---------------------------------------------------------------------------
# Price history (prices.py) — correlated random walks from a factor
# model: market + sector + idiosyncratic, so the Correlation page shows real
# structure (a tech cluster, crypto apart, the REIT and the ETF in between).
# Own RNG, so adding this didn't reshuffle the fixtures above.
# ---------------------------------------------------------------------------
price_rng = random.Random(7)
PRICE_DAYS = 3 * 365 + 30
# symbol: (market beta, sector, sector loading, idiosyncratic daily sd)
FACTOR_MODEL = {
    "AAPL": (1.0, "tech", 0.6, 0.012),
    "MSFT": (0.95, "tech", 0.6, 0.011),
    "NVDA": (1.4, "tech", 1.1, 0.022),
    "ASML": (1.1, "tech", 0.8, 0.016),
    "VWCE.DE": (0.9, "tech", 0.1, 0.003),
    "O": (0.5, "reit", 0.8, 0.010),
    "BTC": (0.4, "crypto", 1.0, 0.012),
    "ETH": (0.45, "crypto", 1.15, 0.018),
}
SECTOR_SD = {"tech": 0.008, "reit": 0.009, "crypto": 0.03}
last_eur = {
    **{p["symbol"]: p["valueEur"] / p["quantity"] for p in positions},
    **{p["symbol"]: p["lastPriceEur"] for p in revolut_positions},
}

log_level = {s: 0.0 for s in FACTOR_MODEL}
paths = {s: [] for s in FACTOR_MODEL}
for k in range(PRICE_DAYS, -1, -1):
    day = TODAY - timedelta(days=k)
    weekend = day.weekday() >= 5
    market = 0.0 if weekend else price_rng.gauss(0, 0.009)
    sector = {name: price_rng.gauss(0, sd) for name, sd in SECTOR_SD.items()}
    for symbol, (beta, sector_name, loading, idio) in FACTOR_MODEL.items():
        crypto = sector_name == "crypto"
        if weekend and not crypto:
            continue
        log_level[symbol] += beta * market + loading * sector[sector_name] + price_rng.gauss(0, idio)
        paths[symbol].append((iso(day), log_level[symbol]))

price_series = {}
for symbol, path in paths.items():
    # Anchor each walk so its last close is the position's current EUR price.
    shift = math.log(last_eur[symbol]) - path[-1][1]
    crypto = FACTOR_MODEL[symbol][1] == "crypto"
    price_series[symbol] = dict(
        source="coinbase" if crypto else "onvista",
        market="Coinbase" if crypto else next(p["exchange"] for p in POSITIONS_RAW if p["symbol"] == symbol),
        currency="EUR" if crypto else next(p["currency"] for p in POSITIONS_RAW if p["symbol"] == symbol),
        at=ts(TODAY),
        dates=[d for d, _ in path],
        closesEur=[round(math.exp(level + shift), 4) for _, level in path],
    )

(OUT / "prices.json").write_text(json.dumps(dict(
    updatedAt=ts(TODAY),
    source="onvista (DEGIRO products), Coinbase (crypto), ECB reference rates (FX)",
    baseCurrency="EUR",
    unresolved=[],
    series=price_series,
    # The return chart's index line: the demo's world-ETF walk under the real
    # benchmark's name (no new draws, so the series above stay identical).
    benchmark=dict(
        symbol="VALL",
        name="Vanguard FTSE Global All-Cap UCITS ETF (Acc)",
        isin="IE000VAHT5T0",
        proxy=dict(symbol="VWCE", name="Vanguard FTSE All-World UCITS ETF (Acc)", isin="IE00BK5BQT80"),
        splicedAt=iso(TODAY - timedelta(days=60)),
        dates=price_series["VWCE.DE"]["dates"],
        closesEur=price_series["VWCE.DE"]["closesEur"],
    ),
), ensure_ascii=False) + "\n")

# ---------------------------------------------------------------------------
# DEGIRO deposits (deposits.py) — the same schedule history.json's
# netDepositsEur follows: a lump sum a year before the history starts, then
# 500 € at each month start, summing to the positions' 22,000 €.
# ---------------------------------------------------------------------------
deposit_moves = [dict(
    date=ts(start - timedelta(days=365), 10, 0),
    kind="deposit",
    description="flatex Deposit",
    type="CASH_TRANSACTION",
    currency="EUR",
    amount=22000.0 - 500.0 * months_left(start),
    amountEur=22000.0 - 500.0 * months_left(start),
)]
for k in range(months_left(start) - 1, -1, -1):
    first = date(TODAY.year, TODAY.month, 1)
    m = (first.month - 1 - k) % 12 + 1
    y = first.year + (first.month - 1 - k) // 12
    deposit_moves.append(dict(
        date=ts(date(y, m, 1), 10, 0), kind="deposit", description="flatex Deposit",
        type="CASH_TRANSACTION", currency="EUR", amount=500.0, amountEur=500.0,
    ))
w("deposits.json", dict(
    updatedAt=ts(TODAY),
    source="DEGIRO via degiro-connector",
    baseCurrency="EUR",
    fromDate="2015-01-01",
    toDate=iso(TODAY),
    totalEur=round(sum(d["amountEur"] for d in deposit_moves), 2),
    movements=deposit_moves,
))

print(f"Wrote demo fixtures to {OUT}")
