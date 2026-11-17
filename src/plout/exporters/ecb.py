"""
Local copy of the ECB euro reference rates, the fallback when frankfurter is down.

The ECB publishes the whole series since 1999 as one zipped CSV
(`eurofxref-hist.zip`): one row per publication day, one column per currency,
each value being units of that currency per euro. It is kept under
data/ecb/eurofxref-hist.csv and refreshed when a requested day is newer than
the copy.

A day without a publication (weekends, TARGET holidays) takes the last rate
published before it, which is what frankfurter does too.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from bisect import bisect_right
from pathlib import Path

import requests

ECB_HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


class EcbHistory:
    def __init__(self, path: Path):
        self.path = path
        self._rows: dict[str, list[tuple[str, float]]] | None = None
        self._refreshed = False

    # The parsed series: currency → [(day, units per euro)] in ascending order.
    def _load(self) -> dict[str, list[tuple[str, float]]]:
        if self._rows is not None:
            return self._rows
        rows: dict[str, list[tuple[str, float]]] = {}
        if self.path.exists():
            rows = parse_csv(self.path.read_text())
        self._rows = rows
        return rows

    def latest_day(self) -> str | None:
        rows = self._load()
        return max((series[-1][0] for series in rows.values() if series), default=None)

    def refresh(self) -> bool:
        """Download the series again. Best-effort: False if it fails."""
        if self._refreshed:
            return False
        self._refreshed = True
        try:
            # requests, not urllib: it ships its own CA bundle, and the ECB's
            # chain does not verify against the system OpenSSL store on macOS.
            resp = requests.get(ECB_HIST_URL, headers={"User-Agent": "plout"}, timeout=60)
            resp.raise_for_status()
            payload = resp.content
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                name = next(n for n in archive.namelist() if n.endswith(".csv"))
                text = archive.read(name).decode("utf-8")
        except Exception as exc:
            print(f"  warning: could not download the ECB series ({exc}).", file=sys.stderr)
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text)
        self._rows = parse_csv(text)
        return True

    def rate(self, currency: str, day: str) -> tuple[float, str] | None:
        """(EUR per unit of `currency`, publication day) on or before `day`."""
        currency = currency.upper()
        latest = self.latest_day()
        if latest is None or latest < day:
            self.refresh()
        found = lookup(self._load(), currency, day)
        if found is None:
            return None
        per_euro, published = found
        return 1.0 / per_euro, published


def parse_csv(text: str) -> dict[str, list[tuple[str, float]]]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return {}
    currencies = [h.strip().upper() for h in header[1:]]
    series: dict[str, list[tuple[str, float]]] = {c: [] for c in currencies if c}
    for row in reader:
        if not row or not row[0].strip():
            continue
        day = row[0].strip()
        for currency, raw in zip(currencies, row[1:]):
            raw = raw.strip()
            if not currency or not raw or raw == "N/A":
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if value > 0:
                series[currency].append((day, value))
    for values in series.values():
        values.sort()
    return series


def lookup(series: dict[str, list[tuple[str, float]]], currency: str, day: str):
    values = series.get(currency)
    if not values:
        return None
    index = bisect_right(values, (day, float("inf")))
    if index == 0:
        return None
    published, per_euro = values[index - 1]
    return per_euro, published
