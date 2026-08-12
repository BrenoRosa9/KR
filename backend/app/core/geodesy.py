"""Cálculos de geometria plana sobre coordenadas projetadas (E, N em metros).

Este módulo é propositalmente puro: só stdlib, nenhuma dependência de pyproj ou
banco. Tudo aqui é determinístico e testável isoladamente, porque é sobre estes
números que um laudo será defendido. Cálculos elipsoidais e conversão de datum
ficam em :mod:`app.core.crs`.

Convenção: ponto é ``(easting, northing)``. Azimute é medido do norte de grade,
sentido horário, em grau decimal.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

Point = tuple[float, float]

# GRS80 / SIRGAS 2000. Diferença para WGS84 é irrelevante nesta escala.
_A = 6378137.0
_F = 1.0 / 298.257222101
_E2 = 2 * _F - _F * _F
UTM_K0 = 0.9996


def grid_distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def grid_azimuth(a: Point, b: Point) -> float:
    """Azimute de grade de ``a`` para ``b``, em ``[0, 360)``.

    Note a ordem dos argumentos de ``atan2``: azimute é medido a partir do
    norte (eixo N) no sentido horário, o inverso da convenção matemática.
    """
    azimuth = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))
    return azimuth % 360.0


def signed_area(ring: Sequence[Point]) -> float:
    """Área com sinal pela fórmula do laço. Positiva se anti-horário."""
    if len(ring) < 3:
        return 0.0
    total = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def polygon_area(ring: Sequence[Point]) -> float:
    return abs(signed_area(ring))


def is_counterclockwise(ring: Sequence[Point]) -> bool:
    return signed_area(ring) > 0


def perimeter(ring: Sequence[Point]) -> float:
    if len(ring) < 2:
        return 0.0
    return sum(
        grid_distance(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))
    )


def segment_lengths(ring: Sequence[Point]) -> list[float]:
    """Comprimento de cada lado, fechando o anel do último para o primeiro."""
    return [
        grid_distance(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))
    ]


def segment_azimuths(ring: Sequence[Point]) -> list[float]:
    return [
        grid_azimuth(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))
    ]


def interior_angles(ring: Sequence[Point]) -> list[float]:
    """Ângulos internos em cada vértice, em grau decimal.

    A orientação do anel decide o sentido da subtração; sem isso, um polígono
    desenhado no sentido anti-horário devolveria os ângulos externos. A soma
    resultante deve ser ``(n-2) * 180`` para um polígono simples, o que os
    testes verificam.
    """
    n = len(ring)
    if n < 3:
        return []

    ccw = is_counterclockwise(ring)
    angles: list[float] = []
    for i in range(n):
        previous = ring[(i - 1) % n]
        following = ring[(i + 1) % n]
        to_previous = grid_azimuth(ring[i], previous)
        to_following = grid_azimuth(ring[i], following)
        delta = (to_following - to_previous) if ccw else (to_previous - to_following)
        angles.append(delta % 360.0)
    return angles


@dataclass(frozen=True)
class ClosureResult:
    """Erro de fechamento de uma poligonal percorrida por azimute e distância."""

    delta_easting: float
    delta_northing: float
    linear_error: float
    total_length: float

    @property
    def precision_denominator(self) -> float:
        """O ``X`` em ``1:X``. Infinito quando o fechamento é exato.

        O corte em um micrometro não é estético: reconstruir um anel a partir de
        suas próprias coordenadas deixa resíduo de ponto flutuante da ordem de
        1e-14 m, e sem o corte a precisão sairia como 1:20000000000000 em vez de
        exata.
        """
        if self.linear_error < 1e-6:
            return math.inf
        return self.total_length / self.linear_error

    @property
    def error_azimuth(self) -> float:
        return grid_azimuth((0.0, 0.0), (self.delta_easting, self.delta_northing))


def traverse(start: Point, segments: Sequence[tuple[float, float]]) -> list[Point]:
    """Caminha uma poligonal a partir de ``start``.

    ``segments`` é uma sequência de ``(azimute, distância)``. Devolve os pontos
    calculados, incluindo o ponto de partida — o último ponto é onde a poligonal
    efetivamente fechou, que raramente coincide com o inicial.
    """
    points = [start]
    easting, northing = start
    for azimuth, distance in segments:
        rad = math.radians(azimuth)
        easting += distance * math.sin(rad)
        northing += distance * math.cos(rad)
        points.append((easting, northing))
    return points


def traverse_closure(
    start: Point, segments: Sequence[tuple[float, float]]
) -> ClosureResult:
    """Erro de fechamento a partir dos azimutes e distâncias *declarados*.

    É este cálculo que revela inconsistência interna de um memorial: se os
    azimutes e distâncias impressos não voltam ao ponto de partida, o documento
    é inconsistente consigo mesmo, independentemente do outro documento.
    """
    points = traverse(start, segments)
    end = points[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    total = sum(distance for _, distance in segments)
    return ClosureResult(
        delta_easting=dx,
        delta_northing=dy,
        linear_error=math.hypot(dx, dy),
        total_length=total,
    )


def ring_closure(ring: Sequence[Point]) -> ClosureResult:
    """Fechamento reconstruído a partir das coordenadas dos vértices.

    Por construção o erro é zero: coordenadas sempre fecham. Serve como
    contraprova do :func:`traverse_closure`, que usa os valores declarados.
    """
    segments = list(zip(segment_azimuths(ring), segment_lengths(ring), strict=True))
    return traverse_closure(ring[0], segments)


def meridian_radius(latitude_deg: float) -> float:
    """Raio de curvatura médio geométrico na latitude dada."""
    lat = math.radians(latitude_deg)
    sin2 = math.sin(lat) ** 2
    w = math.sqrt(1 - _E2 * sin2)
    meridional = _A * (1 - _E2) / (w**3)
    transverse = _A / w
    return math.sqrt(meridional * transverse)


def grid_scale_factor(
    easting: float, latitude_deg: float, k0: float = UTM_K0
) -> float:
    """Fator de escala pontual da projeção UTM.

    Vale ``k0`` no meridiano central e cresce afastando-se dele. É a razão pela
    qual distância de grade não é distância de campo: no limite do fuso a
    diferença chega a cerca de 1 m/km.
    """
    radius = meridian_radius(latitude_deg)
    x = (easting - 500000.0) / k0
    return k0 * (1.0 + (x * x) / (2.0 * radius * radius))


def elevation_factor(height_m: float, latitude_deg: float) -> float:
    """Fator de redução ao nível do elipsoide para uma altitude média."""
    radius = meridian_radius(latitude_deg)
    return radius / (radius + height_m)


def combined_factor(
    easting: float, latitude_deg: float, height_m: float = 0.0, k0: float = UTM_K0
) -> float:
    return grid_scale_factor(easting, latitude_deg, k0) * elevation_factor(
        height_m, latitude_deg
    )


def grid_to_ground(distance: float, factor: float) -> float:
    return distance / factor


def ground_to_grid(distance: float, factor: float) -> float:
    return distance * factor


def centroid(points: Sequence[Point]) -> Point:
    if not points:
        raise ValueError("centroid() exige ao menos um ponto")
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )
