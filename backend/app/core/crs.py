"""Sistemas de referência, projeções e conversão de datum.

Regra de projeto: CRS ausente é bloqueio, nunca default silencioso. A diferença
entre SAD69 e SIRGAS 2000 chega a 70 m no Brasil — assumir o datum errado não
produz um erro pequeno, produz um laudo inválido que parece plausível.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pyproj import CRS, Geod, Transformer
from pyproj.exceptions import CRSError

from .numbers import format_br

Hemisphere = Literal["N", "S"]

SIRGAS2000 = "EPSG:4674"
WGS84 = "EPSG:4326"
SAD69 = "EPSG:4618"
CORREGO_ALEGRE = "EPSG:4225"

# Rótulos como aparecem nos documentos, normalizados para chave canônica.
DATUM_ALIASES: dict[str, str] = {
    "sirgas": SIRGAS2000,
    "sirgas2000": SIRGAS2000,
    "sirgas 2000": SIRGAS2000,
    "sirgas-2000": SIRGAS2000,
    "wgs84": WGS84,
    "wgs 84": WGS84,
    "wgs-84": WGS84,
    "sad69": SAD69,
    "sad 69": SAD69,
    "south american datum 1969": SAD69,
    "corrego alegre": CORREGO_ALEGRE,
    "córrego alegre": CORREGO_ALEGRE,
}

# code = 31960 + zona para o hemisfério sul (zonas 17S a 25S);
# code = 31954 + zona para o norte (zonas 11N a 22N).
# A consistência destas tabelas é verificada contra a base EPSG do pyproj em
# tests/test_crs.py — não confie nelas sem esse teste passando.
_UTM_EPSG: dict[str, dict[int, int]] = {
    "SIRGAS2000": {
        **{zone: 31960 + zone for zone in range(17, 26)},
        **{-zone: 31954 + zone for zone in range(11, 23)},
    },
    "SAD69": {zone: 29170 + zone for zone in range(18, 26)},
    "CORREGO_ALEGRE": {zone: 22500 + zone for zone in range(21, 26)},
    "WGS84": {
        **{zone: 32700 + zone for zone in range(1, 61)},
        **{-zone: 32600 + zone for zone in range(1, 61)},
    },
}

_GEOGRAPHIC_TO_FAMILY = {
    SIRGAS2000: "SIRGAS2000",
    WGS84: "WGS84",
    SAD69: "SAD69",
    CORREGO_ALEGRE: "CORREGO_ALEGRE",
}


class CRSUndetermined(Exception):
    """Levantada quando o CRS não pode ser inferido com segurança.

    Existe para forçar confirmação humana em vez de permitir que o pipeline
    siga com um palpite.
    """


@dataclass(frozen=True)
class CRSSpec:
    """Sistema de referência resolvido de um documento."""

    epsg: str
    datum_label: str
    utm_zone: int | None = None
    hemisphere: Hemisphere | None = None

    @property
    def is_projected(self) -> bool:
        return self.utm_zone is not None

    def describe(self) -> str:
        if self.utm_zone is None:
            return f"{self.datum_label} (geográficas, {self.epsg})"
        return (
            f"{self.datum_label} / UTM {self.utm_zone}{self.hemisphere} ({self.epsg})"
        )


def normalize_datum_label(label: str) -> str | None:
    """Resolve o rótulo de datum escrito no documento para um código EPSG."""
    if not label:
        return None
    key = " ".join(label.lower().split())
    if key in DATUM_ALIASES:
        return DATUM_ALIASES[key]
    for alias, epsg in DATUM_ALIASES.items():
        if alias in key:
            return epsg
    return None


def utm_epsg(geographic_epsg: str, zone: int, hemisphere: Hemisphere) -> str:
    family = _GEOGRAPHIC_TO_FAMILY.get(geographic_epsg)
    if family is None:
        raise CRSUndetermined(f"Datum não suportado para UTM: {geographic_epsg}")

    key = zone if hemisphere == "S" else -zone
    code = _UTM_EPSG[family].get(key)
    if code is None:
        raise CRSUndetermined(
            f"Sem código EPSG para {family} / UTM {zone}{hemisphere}. "
            "Fuso fora da cobertura esperada para o Brasil."
        )
    return f"EPSG:{code}"


def zone_from_longitude(longitude: float) -> int:
    return int((longitude + 180.0) // 6.0) + 1


def central_meridian(zone: int) -> float:
    return -183.0 + 6.0 * zone


def infer_hemisphere(northing: float) -> Hemisphere:
    """Deduz o hemisfério pela magnitude do Northing.

    No hemisfério sul a UTM usa falso norte de 10.000.000 m, então Northings
    ficam na casa dos milhões; no norte, partem de zero. A faixa intermediária
    é ambígua e vira exceção em vez de palpite.
    """
    if 5_000_000.0 <= northing <= 10_000_000.0:
        return "S"
    if 0.0 <= northing < 1_500_000.0:
        return "N"
    raise CRSUndetermined(
        f"Northing {format_br(northing, 3)} não permite inferir o hemisfério "
        "com segurança"
    )


def plausible_for_brazil(longitude: float, latitude: float) -> bool:
    """Sanidade grosseira: o ponto cai no retângulo que contém o Brasil?

    Usado como validação cruzada de qualquer coordenada extraída, sobretudo as
    que vieram de OCR ou de modelo de linguagem.
    """
    return -74.5 <= longitude <= -33.0 and -34.5 <= latitude <= 6.0


@lru_cache(maxsize=64)
def _transformer(source: str, target: str) -> Transformer:
    try:
        return Transformer.from_crs(
            CRS.from_user_input(source), CRS.from_user_input(target), always_xy=True
        )
    except CRSError as exc:  # pragma: no cover - configuração inválida
        raise CRSUndetermined(f"CRS inválido ({source} -> {target}): {exc}") from exc


def transform_point(
    x: float, y: float, source_epsg: str, target_epsg: str
) -> tuple[float, float]:
    """Converte um ponto entre CRS. Ordem sempre ``(x, y)`` = ``(lon/E, lat/N)``."""
    if source_epsg == target_epsg:
        return x, y
    out_x, out_y = _transformer(source_epsg, target_epsg).transform(x, y)
    return float(out_x), float(out_y)


def transform_points(
    points: list[tuple[float, float]], source_epsg: str, target_epsg: str
) -> list[tuple[float, float]]:
    if source_epsg == target_epsg:
        return list(points)
    transformer = _transformer(source_epsg, target_epsg)
    xs, ys = zip(*points, strict=True)
    out_xs, out_ys = transformer.transform(xs, ys)
    return [(float(x), float(y)) for x, y in zip(out_xs, out_ys, strict=True)]


def to_geographic(spec: CRSSpec, easting: float, northing: float) -> tuple[float, float]:
    """De coordenadas projetadas para ``(longitude, latitude)`` no mesmo datum."""
    geographic = _family_geographic(spec)
    return transform_point(easting, northing, spec.epsg, geographic)


def _family_geographic(spec: CRSSpec) -> str:
    for epsg, family in _GEOGRAPHIC_TO_FAMILY.items():
        if spec.datum_label == family:
            return epsg
    # spec.epsg pode já ser geográfico; pyproj resolve o caso trivial.
    crs = CRS.from_user_input(spec.epsg)
    geodetic = crs.geodetic_crs
    if geodetic is None:  # pragma: no cover
        raise CRSUndetermined(f"Sem CRS geográfico associado a {spec.epsg}")
    return f"EPSG:{geodetic.to_epsg()}"


_GEOD = Geod(ellps="GRS80")


def geodesic_inverse(
    lon1: float, lat1: float, lon2: float, lat2: float
) -> tuple[float, float, float]:
    """Azimute direto, azimute inverso e distância geodésica em metros.

    Usado quando o documento traz apenas coordenadas geográficas: distância
    sobre o elipsoide, sem passar por projeção e sem fator de escala.
    """
    forward, backward, distance = _GEOD.inv(lon1, lat1, lon2, lat2)
    return forward % 360.0, backward % 360.0, distance


def geodesic_area(points: list[tuple[float, float]]) -> float:
    """Área elipsoidal em m² de um anel em ``(longitude, latitude)``."""
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    area, _ = _GEOD.polygon_area_perimeter(lons, lats)
    return abs(area)
