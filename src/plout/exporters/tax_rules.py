"""
Year-by-year IRPF rules for `taxes.py`, and the pure calculations built on them.

Every figure here is kept per fiscal year with the official source it came
from. A year that is not listed has no rules, and callers say so instead of
estimating: "no rules for 2027" is more useful than a wrong number.

Nothing in this module touches the network or the filesystem, so it can be
tested with plain numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

D = Decimal
ZERO = D(0)
CENT = D("0.01")


def dec(value) -> Decimal:
    """A Decimal from anything the exporters carry around.

    Floats go through `str` so 0.1 becomes Decimal("0.1") and not the binary
    approximation `Decimal(0.1)` would give.
    """
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def cents(value) -> Decimal:
    """Commercial rounding to the cent, the one the AEAT uses.

    Python's default is ROUND_HALF_EVEN (banker's rounding), which turns
    0,125 into 0,12 where Renta Web writes 0,13.
    """
    return dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def money(value) -> float:
    """A cent-rounded float, for JSON."""
    return float(cents(value))


# ── Escala del ahorro ──────────────────────────────────────────────────────────

# (ceiling, rate) pairs; the last ceiling is None. The brackets themselves have
# not moved since 2016 apart from the new top ones.
_SCALE_2016 = ((D(6_000), D("0.19")), (D(50_000), D("0.21")), (None, D("0.23")))
_SCALE_2021 = (
    (D(6_000), D("0.19")),
    (D(50_000), D("0.21")),
    (D(200_000), D("0.23")),
    (None, D("0.26")),
)
_SCALE_2023 = (
    (D(6_000), D("0.19")),
    (D(50_000), D("0.21")),
    (D(200_000), D("0.23")),
    (D(300_000), D("0.27")),
    (None, D("0.28")),
)
_SCALE_2025 = (
    (D(6_000), D("0.19")),
    (D(50_000), D("0.21")),
    (D(200_000), D("0.23")),
    (D(300_000), D("0.27")),
    (None, D("0.30")),
)

_LEY_26_2014 = "https://www.boe.es/buscar/act.php?id=BOE-A-2014-12327"
_LEY_11_2020 = "https://www.boe.es/buscar/act.php?id=BOE-A-2020-17339"
_LEY_38_2022 = "https://www.boe.es/buscar/act.php?id=BOE-A-2022-22684"
_LEY_7_2024 = "https://www.boe.es/buscar/act.php?id=BOE-A-2024-26694"

SAVINGS_SCALES: dict[int, tuple[tuple, str]] = {
    **{y: (_SCALE_2016, _LEY_26_2014) for y in range(2016, 2021)},
    2021: (_SCALE_2021, _LEY_11_2020),
    2022: (_SCALE_2021, _LEY_11_2020),
    2023: (_SCALE_2023, _LEY_38_2022),
    2024: (_SCALE_2023, _LEY_38_2022),
    2025: (_SCALE_2025, _LEY_7_2024),
    # Ley 7/2024 has no end date and nothing has amended the scale since.
    2026: (_SCALE_2025, _LEY_7_2024),
}


def savings_scale(year: int | str) -> tuple | None:
    entry = SAVINGS_SCALES.get(int(year))
    return entry[0] if entry else None


def savings_tax(base, year: int | str) -> Decimal | None:
    """Cuota on a savings base with the scale of `year`; None if unknown."""
    scale = savings_scale(year)
    if scale is None:
        return None
    base = dec(base)
    if base <= 0:
        return ZERO
    tax = ZERO
    lower = ZERO
    for ceiling, rate in scale:
        top = base if ceiling is None else min(base, ceiling)
        if top > lower:
            tax += (top - lower) * rate
            lower = top
        if ceiling is not None and base <= ceiling:
            break
    return cents(tax)


def average_rate_pct(cuota, base) -> Decimal:
    """Tipo medio del ahorro, in percent with two decimals (art. 80 LIRPF)."""
    base = dec(base)
    if base <= 0:
        return ZERO
    return cents(dec(cuota) / base * 100)


# ── Modelo 720 / 721 thresholds ────────────────────────────────────────────────

_RD_1065_2007 = "https://www.boe.es/buscar/act.php?id=BOE-A-2007-15984"
# The 720 exists since the 2012 return; the 721 since the 2023 one.
MODELO_720_THRESHOLDS = {y: (D(50_000), _RD_1065_2007) for y in range(2012, 2027)}
MODELO_721_THRESHOLDS = {y: (D(50_000), _RD_1065_2007) for y in range(2023, 2027)}


def modelo_threshold(table: dict, year: int | str) -> float | None:
    entry = table.get(int(year))
    return float(entry[0]) if entry else None


# ── Doble imposición internacional (art. 80 LIRPF) ─────────────────────────────

# Maximum withholding each convention allows on portfolio dividends. From the
# DGT's table of treaty limits and Hacienda's list of conventions in force;
# Japan's 5 % comes from the 2018 convention, in force since 2021, which is why
# the table starts there.
_TREATY_PORTFOLIO = {
    **dict.fromkeys(("JP", "SG", "CY", "MT"), D("0.05")),
    **dict.fromkeys(("CN", "GR", "HK", "IL", "MX", "GB", "LV"), D("0.10")),
    **dict.fromkeys(
        (
            "US", "DE", "FR", "NL", "IE", "LU", "CH", "BE", "AT", "IT", "PT", "CA",
            "AU", "BR", "KR", "FI", "HU", "IN", "NO", "NZ", "PL", "CZ", "ZA", "SE",
            "IS", "EE", "LT", "SI", "AE",
        ),
        D("0.15"),
    ),
}
TREATY_SOURCE = (
    "https://www.hacienda.gob.es/es-ES/Normativa%20y%20doctrina/Normativa/CDI/Paginas/CDI_Alfa.aspx"
)
# Denmark denounced its convention in 2008: whatever it withholds is deductible
# (still subject to the average-rate cap).
_NO_TREATY = frozenset({"DK"})

TREATY_RATES: dict[int, dict[str, Decimal]] = {y: _TREATY_PORTFOLIO for y in range(2021, 2027)}


@dataclass
class Credit:
    country: str | None
    rate: Decimal | None        # treaty rate applied, None when there is no cap
    creditable: Decimal         # deducible en España
    excess: Decimal             # a reclamar en el país de origen
    known: bool                 # False: unknown country or no rules for the year


def creditable_tax(gross_eur, foreign_tax_eur, country: str | None, year: int | str) -> Credit:
    """(deducible en España, exceso a reclamar en origen) for one dividend.

    The deduction is the smaller of what was actually withheld and what the
    convention lets the source country withhold. The rest is not Spain's to
    give back: it is reclaimed from the source country.
    """
    gross = dec(gross_eur)
    withheld = abs(dec(foreign_tax_eur))
    country = (country or "").upper() or None
    rates = TREATY_RATES.get(int(year))
    if country in _NO_TREATY:
        return Credit(country, None, cents(withheld), ZERO, True)
    if rates is None or country is None or country not in rates:
        return Credit(country, None, cents(withheld), ZERO, False)
    rate = rates[country]
    cap = cents(gross * rate)
    credit = min(cents(withheld), cap)
    return Credit(country, rate, credit, cents(withheld) - credit, True)


def double_tax_deduction(creditable_total, foreign_income_in_base, avg_rate_pct) -> Decimal:
    """The deduction can't exceed what Spain charges on that same income."""
    limit = cents(dec(foreign_income_in_base) * dec(avg_rate_pct) / 100)
    return min(cents(creditable_total), limit)


# Depositary receipts carry the ISIN of the bank that issues them, not of the
# company that pays the dividend.
_DEPOSITARY_HINTS = ("ADR", "ADS", "GDR", "DEPOSITARY", "DEPOSITORY")


def dividend_country(
    isin: str | None,
    name: str | None = None,
    override: str | None = None,
) -> tuple[str | None, bool]:
    """(country, doubtful) for a dividend.

    The country is the paying company's. It is usually the ISIN prefix, but a
    depositary receipt's ISIN belongs to its issuer, so those are flagged as
    doubtful until the user sets the country by hand.
    """
    if override:
        return override.strip().upper(), False
    isin = (isin or "").strip().upper()
    country = isin[:2] if len(isin) >= 2 and isin[:2].isalpha() else None
    words = set((name or "").upper().replace(".", " ").replace("-", " ").split())
    doubtful = country is None or bool(words & set(_DEPOSITARY_HINTS))
    return country, doubtful


# ── Integración y compensación (art. 49 LIRPF) ─────────────────────────────────

CROSS_LIMIT = D("0.25")
CARRY_YEARS = 4


@dataclass
class Pending:
    origin: int
    block: str          # "gp" (ganancias y pérdidas) | "rcm" (capital mobiliario)
    amount: Decimal     # positive: the negative balance still to offset


@dataclass
class Integration:
    gains: Decimal                  # saldo de ganancias y pérdidas en la base
    income: Decimal                 # saldo de rendimientos en la base
    cross_offset: Decimal           # this year's negative block against the other
    applied: list[dict] = field(default_factory=list)
    carried: list[Pending] = field(default_factory=list)
    expired: list[Pending] = field(default_factory=list)


def integrate(
    year: int,
    gains,
    income,
    pending: list[Pending],
    cross: Decimal = CROSS_LIMIT,
    years: int = CARRY_YEARS,
) -> Integration:
    """Base del ahorro under art. 49 LIRPF.

    1. The year's two blocks net separately. A negative one offsets the other
       up to 25 % of the other's positive balance.
    2. Balances from up to four earlier years go next, oldest first: against
       their own block, then what is left against the other one, sharing the
       same 25 % limit with step 1.
    3. Whatever is still negative carries forward; a balance in its fourth
       year that still is not used up expires.
    """
    g, i = cents(gains), cents(income)
    cap_on_income = cents(i * cross) if i > 0 else ZERO
    cap_on_gains = cents(g * cross) if g > 0 else ZERO
    cross_offset = ZERO
    if g < 0 < i:
        used = min(-g, cap_on_income)
        g += used
        i -= used
        cap_on_income -= used
        cross_offset = used
    elif i < 0 < g:
        used = min(-i, cap_on_gains)
        i += used
        g -= used
        cap_on_gains -= used
        cross_offset = used

    applied: list[dict] = []
    carried: list[Pending] = []
    expired: list[Pending] = []
    for item in sorted((p for p in pending if p.origin < year), key=lambda p: (p.origin, p.block)):
        amount = cents(item.amount)
        if amount <= 0:
            continue
        age = year - item.origin
        if age > years:
            expired.append(Pending(item.origin, item.block, amount))
            continue
        if item.block == "gp":
            own = min(amount, max(g, ZERO))
            g -= own
            amount -= own
            other = min(amount, max(i, ZERO), cap_on_income)
            i -= other
            cap_on_income -= other
            amount -= other
        else:
            own = min(amount, max(i, ZERO))
            i -= own
            amount -= own
            other = min(amount, max(g, ZERO), cap_on_gains)
            g -= other
            cap_on_gains -= other
            amount -= other
        if own or other:
            applied.append(
                {"origin": item.origin, "block": item.block, "ownEur": own, "otherEur": other}
            )
        if amount > 0:
            (expired if age >= years else carried).append(Pending(item.origin, item.block, amount))

    if g < 0:
        carried.append(Pending(year, "gp", -g))
    if i < 0:
        carried.append(Pending(year, "rcm", -i))
    return Integration(max(g, ZERO), max(i, ZERO), cross_offset, applied, carried, expired)
