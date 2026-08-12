"""Critério de comparação: igualdade dos números como impressos.

Não há perfil de tolerância de negócio (0,02 m, 10″, etc.). A regra é:

* se o documento imprime ``100,50``, o valor comparado é ``100,50``;
* qualquer diferença **depois** de arredondar à precisão da fonte é divergência.

Quando não há precisão de origem (valores só recalculados), usa-se um limiar
mínimo de ponto flutuante — submilimétrico — só para não acusar ruído IEEE-754.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

# Ruído de float em grandezas lineares (0,1 mm) e angulares (~3e-5″).
LINEAR_EPS = 1e-4
ANGLE_EPS = 1e-8
FLOAT_EPS = LINEAR_EPS  # alias usado pelo motor de comparação


def decimals_from_halfwidth(halfwidth: float) -> int | None:
    """Converte a meia-largura de arredondamento no número de casas decimais."""
    if halfwidth is None or halfwidth <= 0:
        return None
    try:
        return max(0, int(round(-math.log10(2.0 * halfwidth))))
    except (ValueError, OverflowError):
        return None


def values_equal(a: float, b: float, *halfwidths: float) -> bool:
    """True se ``a`` e ``b`` são o mesmo número na precisão das fontes."""
    widths = [h for h in halfwidths if h and h > 0]
    if widths:
        decimals_list = [
            d for h in widths if (d := decimals_from_halfwidth(h)) is not None
        ]
        if decimals_list:
            decimals = max(decimals_list)
            return round(a, decimals) == round(b, decimals)
    return abs(a - b) <= LINEAR_EPS


@dataclass(frozen=True)
class ToleranceProfile:
    """Configuração residual da comparação.

    O nome histórico permanece porque o banco ainda grava ``profile_name``.
    Os campos de limite de negócio estão zerados de propósito.
    """

    key: str = "exato"
    name: str = "igualdade exata"
    coordinate_m: float = 0.0
    distance_m: float = 0.0
    distance_ppm: float = 0.0
    azimuth_arcsec: float = 0.0
    angle_arcsec: float = 0.0
    area_relative: float = 0.0
    area_m2: float = 0.0
    perimeter_m: float = 0.0
    min_closure_precision: float = float("inf")
    low_confidence: float = 0.80
    apply_scale_factor: bool = True

    def for_coordinate(self, *halfwidths: float) -> float:
        return _quantum(halfwidths, LINEAR_EPS)

    def for_distance(self, magnitude: float, *halfwidths: float) -> float:
        return _quantum(halfwidths, LINEAR_EPS)

    def for_azimuth(self, *halfwidths_deg: float) -> float:
        return _quantum(halfwidths_deg, ANGLE_EPS)

    def for_angle(self, *halfwidths_deg: float) -> float:
        return _quantum(halfwidths_deg, ANGLE_EPS)

    def for_area(self, magnitude: float, *halfwidths: float) -> float:
        return _quantum(halfwidths, LINEAR_EPS)

    def for_perimeter(self, *halfwidths: float) -> float:
        return _quantum(halfwidths, LINEAR_EPS)

    def exceeds(self, delta: float, tolerance: float = 0.0) -> bool:
        """Compatível com o motor: usa o quantum como limiar de igualdade.

        Preferir ``values_differ`` quando os dois valores estão disponíveis —
        arredondar e comparar é a definição correta de “número igual”.
        """
        threshold = tolerance if tolerance > 0 else LINEAR_EPS
        return abs(delta) > threshold

    def values_differ(self, a: float, b: float, *halfwidths: float) -> bool:
        return not values_equal(a, b, *halfwidths)

    def relaxed(self, factor: float) -> ToleranceProfile:
        return replace(
            self,
            key=f"{self.key}-x{factor:g}",
            name=f"{self.name} x{factor:g}",
        )


def _quantum(halfwidths: tuple[float, ...] | list[float], fallback: float) -> float:
    """Quantum de exibição (meia-largura) ou limiar de float se ausente."""
    widths = [h for h in halfwidths if h and h > 0]
    if not widths:
        return fallback
    return max(widths)


PROFILES: dict[str, ToleranceProfile] = {
    "exato": ToleranceProfile(),
    "padrao": ToleranceProfile(key="padrao", name="igualdade exata"),
    "rigoroso": ToleranceProfile(key="rigoroso", name="igualdade exata"),
    "urbano": ToleranceProfile(key="urbano", name="igualdade exata"),
    "rural": ToleranceProfile(key="rural", name="igualdade exata"),
}

DEFAULT_PROFILE_KEY = "exato"
