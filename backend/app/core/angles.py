"""Ângulos: parsing de GMS/decimal, rumo para azimute, normalização.

Convenção interna do sistema: todo ângulo circula como grau decimal. Azimutes
sempre no intervalo ``[0, 360)``. A precisão da origem é preservada em
segundos de arco, porque é nessa unidade que as tolerâncias do domínio são
especificadas.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from .numbers import Convention, parse_number

ARCSEC = 1.0 / 3600.0
AngleFormat = Literal["dms", "decimal", "rumo"]
Quadrant = Literal["NE", "SE", "SW", "NW"]

# 23°45'12,34"  |  23 45 12.34  |  23º45'12"S
_DMS_RE = re.compile(
    r"""
    (?P<sign>[-+\u2212])?\s*
    (?P<deg>\d{1,3})\s*(?:[°ºo*]|\s)\s*
    (?P<min>\d{1,2})\s*(?:['′’´]|\s)?\s*
    (?:(?P<sec>\d{1,2}(?:[.,]\d+)?)\s*(?:["″”'']|\s)?)?
    \s*(?P<hemi>[NSEWLOnsewlo])?
    """,
    re.VERBOSE,
)

_RUMO_RE = re.compile(
    r"(?P<first>[NSns])\s*(?P<angle>[^NSEWLOnsewlo]+?)\s*(?P<second>[EWLOewlo])"
)


@dataclass(frozen=True)
class ParsedAngle:
    """Ângulo em grau decimal, com a precisão da origem em segundos de arco."""

    degrees: float
    arcsec_precision: float
    raw: str
    fmt: AngleFormat

    @property
    def rounding_halfwidth_deg(self) -> float:
        return 0.5 * self.arcsec_precision * ARCSEC


def parse_angle(raw: str, convention: Convention = "br") -> ParsedAngle | None:
    """Aceita GMS, grau decimal e rumo. Retorna ``None`` se não reconhecer."""
    if not raw:
        return None

    rumo = _parse_rumo(raw, convention)
    if rumo is not None:
        return rumo

    dms = _parse_dms(raw, convention)
    if dms is not None:
        return dms

    return _parse_decimal(raw, convention)


def _parse_dms(raw: str, convention: Convention) -> ParsedAngle | None:
    match = _DMS_RE.search(raw)
    if match is None:
        return None

    # Sem marcador de grau explícito, "12 34" pode ser qualquer coisa; exigimos
    # o símbolo para não capturar pares de números soltos como GMS.
    if not re.search(r"[°ºo*]", raw):
        return None

    degrees = float(match.group("deg"))
    minutes = float(match.group("min"))
    sec_raw = match.group("sec")

    if sec_raw is None:
        seconds, precision = 0.0, 60.0
    else:
        parsed = parse_number(sec_raw, convention)
        if parsed is None:
            return None
        seconds = parsed.value
        precision = 10.0**-parsed.decimals

    total = degrees + minutes / 60.0 + seconds / 3600.0

    if (match.group("sign") or "") in {"-", "\u2212"}:
        total = -total
    hemi = (match.group("hemi") or "").upper()
    if hemi in {"S", "W", "O"}:  # "O" de Oeste em documentos em português
        total = -total

    return ParsedAngle(
        degrees=total, arcsec_precision=precision, raw=raw.strip(), fmt="dms"
    )


def _parse_decimal(raw: str, convention: Convention) -> ParsedAngle | None:
    parsed = parse_number(raw, convention)
    if parsed is None:
        return None

    value = parsed.value
    if re.search(r"[SsWw]\s*$", raw.strip()) and value > 0:
        value = -value

    # d casas em grau decimal equivalem a 10**-d * 3600 segundos de arco.
    precision = (10.0**-parsed.decimals) * 3600.0
    return ParsedAngle(
        degrees=value, arcsec_precision=precision, raw=raw.strip(), fmt="decimal"
    )


def _parse_rumo(raw: str, convention: Convention) -> ParsedAngle | None:
    """Converte rumo (``N 45°30' E``) para azimute."""
    match = _RUMO_RE.search(raw)
    if match is None:
        return None

    inner = _parse_dms(match.group("angle"), convention) or _parse_decimal(
        match.group("angle"), convention
    )
    if inner is None:
        return None

    first = match.group("first").upper()
    second = "E" if match.group("second").upper() in {"E", "L"} else "W"
    quadrant = f"{first}{second}"
    azimuth = rumo_to_azimuth(abs(inner.degrees), quadrant)  # type: ignore[arg-type]

    return ParsedAngle(
        degrees=azimuth,
        arcsec_precision=inner.arcsec_precision,
        raw=raw.strip(),
        fmt="rumo",
    )


def rumo_to_azimuth(angle: float, quadrant: Quadrant) -> float:
    if quadrant == "NE":
        return normalize_azimuth(angle)
    if quadrant == "SE":
        return normalize_azimuth(180.0 - angle)
    if quadrant == "SW":
        return normalize_azimuth(180.0 + angle)
    return normalize_azimuth(360.0 - angle)


def azimuth_to_rumo(azimuth: float) -> tuple[float, Quadrant]:
    az = normalize_azimuth(azimuth)
    if az <= 90.0:
        return az, "NE"
    if az <= 180.0:
        return 180.0 - az, "SE"
    if az <= 270.0:
        return az - 180.0, "SW"
    return 360.0 - az, "NW"


def normalize_azimuth(degrees: float) -> float:
    return degrees % 360.0


def angular_difference(a: float, b: float) -> float:
    """Menor diferença com sinal entre dois azimutes, em ``(-180, 180]``.

    Existe para que a comparação entre 359,9° e 0,1° resulte em 0,2° e não em
    359,8°, que é o erro clássico ao comparar azimutes por subtração direta.
    """
    diff = (a - b + 180.0) % 360.0 - 180.0
    return 180.0 if diff == -180.0 else diff


def to_dms_string(degrees: float, seconds_decimals: int = 2) -> str:
    sign = "-" if degrees < 0 else ""
    total = abs(degrees)
    deg = int(total)
    minutes_full = (total - deg) * 60.0
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60.0

    # Arredondar segundos pode transbordar para minutos e graus.
    if round(seconds, seconds_decimals) >= 60.0:
        seconds = 0.0
        minutes += 1
    if minutes >= 60:
        minutes = 0
        deg += 1

    # Largura fixa com zero à esquerda: 5" precisa sair como 05", senão
    # 1°00'05" e 1°00'50" ficam indistinguíveis num relatório em coluna.
    width = 2 if seconds_decimals == 0 else 3 + seconds_decimals
    sec_text = f"{seconds:0{width}.{seconds_decimals}f}".replace(".", ",")
    return f'{sign}{deg}°{minutes:02d}\'{sec_text}"'


def arcsec_to_degrees(arcsec: float) -> float:
    return arcsec * ARCSEC


def degrees_to_arcsec(degrees: float) -> float:
    return degrees * 3600.0


def radians(degrees: float) -> float:
    return math.radians(degrees)
