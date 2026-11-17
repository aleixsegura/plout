"""
Renta Web box numbers, read from the AEAT's own field dictionary.

Every spring the AEAT publishes `diccionarioXSD_AAAA.properties`, the data
dictionary of that year's Modelo 100. Each line reads

    CLAVE=[rutaXML][tipo][casilla][etiqueta]

in ISO-8859-1. The XML path of a field stays put from one year to the next;
its box number is what moves. So the filing pack names fields by path here and
looks the box number up in the dictionary of the year being filed.

Pending loss balances have no path of their own per origin year — the same
element is reused and only the label says which year it is — so those are
found by label text instead.

A section only counts as verified when *every* one of its fields is found:
a half-numbered section would invite trusting the half that isn't there.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

DICTIONARY_URL = (
    "https://sede.agenciatributaria.gob.es/static_files/Sede/Disenyo_registro/"
    "DR_100_199/diccionarioXSD_{year}.properties"
)


@dataclass(frozen=True)
class Field:
    key: str
    path: str
    kind: str
    casilla: str
    label: str


def split_groups(text: str) -> list[str]:
    """The top-level `[...]` groups of a line, nested brackets kept inside.

    Labels quote other boxes — "( [0332] - [0333] - [0335] )" — so splitting on
    every `]` would cut a label in pieces. Track the depth instead.
    """
    groups: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "[":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == "]" and depth:
            depth -= 1
            if depth == 0:
                groups.append(text[start:index])
    return groups


def parse_dictionary(text: str) -> list[Field]:
    fields: list[Field] = []
    for line in text.splitlines():
        key, sep, rest = line.partition("=")
        if not sep or not rest.startswith("["):
            continue
        groups = split_groups(rest)
        if len(groups) < 4:
            continue
        path, kind, casilla, label = (g.strip() for g in groups[:4])
        fields.append(Field(key.strip(), path, kind, casilla, label))
    return fields


class Dictionary:
    def __init__(self, year: int, fields: list[Field]):
        self.year = year
        self.fields = fields
        self.by_path: dict[str, Field] = {}
        for f in fields:
            # A few paths repeat (the @titular attribute of every section);
            # those are never looked up, so the first one is as good as any.
            self.by_path.setdefault(f.path, f)

    @property
    def matches_year(self) -> bool:
        """True when the file really is the one for `year`."""
        marker = f"31/12/{self.year}"
        return any(marker in f.label for f in self.fields)

    def by_label(self, pattern: re.Pattern) -> Field | None:
        return next((f for f in self.fields if pattern.search(f.label)), None)


def load_dictionary(year: int | str, cache_dir: Path, download: bool = True) -> Dictionary | None:
    """The dictionary for `year`, from the local copy or the AEAT.

    None when it isn't published yet (the year is still open, or it is early
    spring) or cannot be fetched: the pack then keeps "verificar en pantalla".
    """
    year = int(year)
    path = cache_dir / f"diccionarioXSD_{year}.properties"
    text = None
    if path.exists():
        text = path.read_text(encoding="iso-8859-1")
    elif download:
        try:
            resp = requests.get(
                DICTIONARY_URL.format(year=year), headers={"User-Agent": "plout"}, timeout=60
            )
            resp.raise_for_status()
            raw = resp.content
            candidate = raw.decode("iso-8859-1")
            # A missing year answers with an HTML error page, not a 404 body
            # anyone would mistake for data — but check rather than assume.
            if "=[/" in candidate:
                cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                text = candidate
        except Exception as exc:
            print(
                f"  aviso: no se pudo descargar el diccionario de casillas de {year} ({exc}).",
                file=sys.stderr,
            )
    if not text:
        return None
    dictionary = Dictionary(year, parse_dictionary(text))
    if not dictionary.matches_year:
        print(
            f"  aviso: el diccionario de casillas no parece del ejercicio {year}; se ignora.",
            file=sys.stderr,
        )
        return None
    return dictionary


# ── What each pack field is, by XML path ───────────────────────────────────────

_ACC = "/DatosEconomicos/TomaDatosAmpliada/GPAcciones/EntidadAccion/"
_CRI = "/DatosEconomicos/TomaDatosAmpliada/GPOtrosCriptomonedas/ElementoCriptomoneda/"
_RCM = "/DatosEconomicos/TomaDatosAmpliada/RdtoCapitalMobiliario/RdtoCapitalMobiliarioAhorro/"

# section id → pack field label → XML path. Only fields with a box of their
# own on the form are listed; dates and the number of shares are typed on
# Renta Web's per-entity window and have no box in the dictionary.
FIELD_PATHS: dict[str, dict[str, str]] = {
    "acciones-cotizadas": {
        "Entidad emisora": _ACC + "G2B_DE",
        "Valor de transmisión": _ACC + "G2B_A/@valor",
        "Valor de adquisición": _ACC + "G2B_B",
    },
    "monedas-virtuales": {
        "Denominación de la moneda virtual": _CRI + "G2CRIDEN",
        "Valor de transmisión": _CRI + "G2DCRIE/@valor",
        "Valor de adquisición": _CRI + "G2DCRIF",
    },
    "dividendos": {
        "Ingresos íntegros": _RCM + "B13/@valor",
        "Retenciones (España)": "/DatosEconomicos/Resultados/CalculoImpuestoRes/RetencionesRes/RET2",
    },
    "doble-imposicion": {
        "Deducción por doble imposición internacional": (
            "/DatosEconomicos/Resultados/CalculoImpuestoRes/CuotaAutoliquidacionRes/CLDED1"
        ),
    },
}


def pending_label(block: str, origin: int, year: int, cross: bool) -> re.Pattern:
    """The label of a pending-balance box for one origin year and block."""
    tail = rf",? pendientes de compensación a 1 de enero de {year}, a integrar en la base imponible del ahorro"
    if block == "gp":
        head = "Resto de saldos" if cross else "Saldos"
        body = rf"{head} netos negativos de ganancias y pérdidas patrimoniales de {origin}"
    else:
        body = (
            rf"Resto de saldos netos negativos de rendimientos de capital mobiliario de {origin}"
            if cross
            else rf"Saldos netos negativos de rendimientos del capital mobiliario, de {origin}"
        )
    return re.compile("^" + body + tail)


def annotate_pack(pack: dict, dictionary: Dictionary | None) -> None:
    """Put the box number on every pack field the dictionary knows.

    Sets `casilla` and `xmlPath` on each field, `verified` on each section, and
    rewrites the section's `casillaHint` once every field has been found.
    """
    year = int(pack["fiscalYear"])
    pack["casillasSource"] = (
        DICTIONARY_URL.format(year=year) if dictionary is not None else None
    )
    for section in pack["sections"]:
        section["verified"] = False
        if dictionary is None:
            continue
        found_all = True
        any_field = False
        paths = FIELD_PATHS.get(section["id"], {})
        for entry in section["entries"] + section.get("parcelEntries", []):
            for f in entry["fields"]:
                match = None
                if section["id"] == "saldos-pendientes":
                    pattern = pending_label(f["block"], f["origin"], year, f["cross"])
                    match = dictionary.by_label(pattern)
                elif f["label"] in paths:
                    match = dictionary.by_path.get(paths[f["label"]])
                else:
                    continue
                any_field = True
                if match is None:
                    found_all = False
                    continue
                f["casilla"] = match.casilla
                f["xmlPath"] = match.path
        if any_field and found_all:
            section["verified"] = True
            boxes = sorted(
                {
                    f["casilla"]
                    for e in section["entries"]
                    for f in e["fields"]
                    if f.get("casilla")
                }
            )
            section["casillaHint"] = (
                f"casillas {', '.join(boxes)} según el diccionario de la AEAT de {year}"
            )
