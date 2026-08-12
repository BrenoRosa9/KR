"""Correspondência entre os vértices de dois documentos.

Este é o núcleo algorítmico do produto. Antes de comparar qualquer número é
preciso decidir qual vértice de A corresponde a qual de B, e as tentativas
ingênuas quebram em três situações reais: rótulos de esquemas diferentes,
polígonos descritos a partir de vértices iniciais diferentes, e orientações
opostas (horária vs anti-horária).

A ordem das estratégias importa. Código primeiro, porque é exato quando
existe. Geometria depois, porque é robusta a rótulo mas sensível a
deslocamento sistemático. Alinhamento cíclico por último, porque só funciona
quando as contagens de vértices coincidem, mas resolve o caso em que os dois
documentos não têm nem rótulo nem coordenada compatíveis.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from scipy.optimize import linear_sum_assignment

from .geodesy import Point, centroid, grid_azimuth, grid_distance, segment_lengths
from .numbers import format_br


class MatchMethod(StrEnum):
    CODE = "code"
    GEOMETRIC = "geometric"
    CYCLIC = "cyclic"
    NONE = "none"


class SystematicKind(StrEnum):
    TRANSLATION = "translation"
    ROTATION = "rotation"
    SCALE = "scale"


_CODE_CLEAN_RE = re.compile(r"[^A-Z0-9]")
_CODE_SPLIT_RE = re.compile(r"^(?P<prefix>[A-Z]*)(?P<number>\d+)$")


def normalize_code(code: str) -> str:
    """Normaliza rótulo de vértice para comparação.

    ``P-01``, ``P 01`` e ``P01`` viram ``P1``. Já ``V1`` continua distinto de
    ``P1``: prefixos diferentes provavelmente são esquemas de nomeação
    diferentes, e casá-los por engano é pior do que cair na estratégia
    geométrica.
    """
    if not code:
        return ""
    cleaned = _CODE_CLEAN_RE.sub("", code.upper())
    match = _CODE_SPLIT_RE.match(cleaned)
    if match is None:
        return cleaned
    number = match.group("number").lstrip("0") or "0"
    return f"{match.group('prefix')}{number}"


@dataclass(frozen=True)
class MatchPair:
    index_a: int
    index_b: int
    code_a: str
    code_b: str
    residual_m: float | None = None


@dataclass(frozen=True)
class SystematicOffset:
    """Padrão global detectado entre os dois conjuntos de vértices.

    Reportar um deslocamento sistemático em vez de N divergências individuais é
    a diferença entre um relatório acionável e uma lista de 47 linhas vermelhas
    idênticas. Deslocamento constante costuma ser datum ou origem trocada;
    rotação constante, norte magnético contra verdadeiro contra de grade;
    escala constante, distância de grade tratada como de campo.
    """

    kind: SystematicKind
    translation_e: float
    translation_n: float
    rotation_deg: float
    scale: float
    residual_rms_m: float
    magnitude: float
    azimuth_deg: float
    message: str


@dataclass
class MatchResult:
    pairs: list[MatchPair] = field(default_factory=list)
    unmatched_a: list[int] = field(default_factory=list)
    unmatched_b: list[int] = field(default_factory=list)
    method: MatchMethod = MatchMethod.NONE
    reversed_orientation: bool = False
    rotation_offset: int = 0
    systematic: SystematicOffset | None = None
    notes: list[str] = field(default_factory=list)


def match_by_code(codes_a: Sequence[str], codes_b: Sequence[str]) -> MatchResult:
    """Casa vértices por rótulo normalizado. Só aceita casamento injetivo."""
    normalized_a = [normalize_code(c) for c in codes_a]
    normalized_b = [normalize_code(c) for c in codes_b]

    # Rótulos repetidos dentro de um documento tornam o casamento ambíguo;
    # nesse caso é melhor não casar por código e deixar a geometria decidir.
    lookup: dict[str, int] = {}
    duplicated: set[str] = set()
    for index, code in enumerate(normalized_b):
        if not code:
            continue
        if code in lookup:
            duplicated.add(code)
        else:
            lookup[code] = index

    result = MatchResult(method=MatchMethod.CODE)
    used_b: set[int] = set()
    for index_a, code in enumerate(normalized_a):
        target = lookup.get(code)
        if not code or code in duplicated or target is None or target in used_b:
            result.unmatched_a.append(index_a)
            continue
        used_b.add(target)
        result.pairs.append(
            MatchPair(index_a, target, codes_a[index_a], codes_b[target])
        )
    result.unmatched_b = [i for i in range(len(codes_b)) if i not in used_b]

    if duplicated:
        result.notes.append(
            "Rótulos repetidos ignorados no casamento por código: "
            + ", ".join(sorted(duplicated))
        )
    return result


def match_geometric(
    points_a: Sequence[Point],
    points_b: Sequence[Point],
    max_residual_m: float = 5.0,
    remove_translation: bool = True,
    translation: Point | None = None,
) -> MatchResult:
    """Casamento ótimo por proximidade, via atribuição linear (Hungarian).

    ``remove_translation`` centra os dois conjuntos antes de medir distâncias.
    Sem isso, dois documentos em datums diferentes — deslocados uniformemente em
    60 m — não casariam nenhum vértice, quando na verdade a correspondência é
    óbvia e o deslocamento é o achado.

    ``translation`` permite impor um deslocamento já conhecido em vez de
    estimá-lo pelos centroides. É o que se usa ao completar um casamento
    parcial: o deslocamento vem dos vértices já casados por código, que são uma
    referência muito melhor do que o centroide de um punhado de sobras.
    """
    result = MatchResult(method=MatchMethod.GEOMETRIC)
    if not points_a or not points_b:
        result.unmatched_a = list(range(len(points_a)))
        result.unmatched_b = list(range(len(points_b)))
        return result

    array_a = np.asarray(points_a, dtype=float)
    array_b = np.asarray(points_b, dtype=float)

    if translation is not None:
        array_a = array_a + np.asarray(translation)
    elif remove_translation:
        shift = np.asarray(centroid(points_b)) - np.asarray(centroid(points_a))
        array_a = array_a + shift
        result.notes.append(
            f"Casamento geométrico após remover deslocamento médio de "
            f"{format_br(math.hypot(*shift), 3)} m entre os centroides."
        )

    cost = np.linalg.norm(array_a[:, None, :] - array_b[None, :, :], axis=2)
    rows, cols = linear_sum_assignment(cost)

    matched_a: set[int] = set()
    matched_b: set[int] = set()
    for index_a, index_b in zip(rows, cols, strict=True):
        residual = float(cost[index_a, index_b])
        if residual > max_residual_m:
            continue
        matched_a.add(int(index_a))
        matched_b.add(int(index_b))
        result.pairs.append(
            MatchPair(int(index_a), int(index_b), "", "", residual_m=residual)
        )

    result.pairs.sort(key=lambda pair: pair.index_a)
    result.unmatched_a = [i for i in range(len(points_a)) if i not in matched_a]
    result.unmatched_b = [i for i in range(len(points_b)) if i not in matched_b]
    return result


def align_cyclic(ring_a: Sequence[Point], ring_b: Sequence[Point]) -> MatchResult:
    """Alinha dois anéis testando todas as rotações e as duas orientações.

    Compara a *sequência de comprimentos de lado*, que é invariante a
    translação, rotação e escolha do vértice inicial. Resolve o caso em que os
    dois documentos descrevem o mesmo polígono começando de vértices diferentes
    ou percorrendo em sentidos opostos.
    """
    result = MatchResult(method=MatchMethod.CYCLIC)
    n = len(ring_a)
    if n < 3 or len(ring_b) != n:
        result.unmatched_a = list(range(len(ring_a)))
        result.unmatched_b = list(range(len(ring_b)))
        result.notes.append(
            "Alinhamento cíclico exige o mesmo número de vértices nos dois documentos."
        )
        return result

    lengths_a = segment_lengths(ring_a)
    lengths_b = segment_lengths(ring_b)
    best: tuple[float, int, bool] | None = None

    # Duas famílias de correspondência candidata, parametrizadas por `shift`:
    #   orientação preservada: a[i] <-> b[(i + shift) % n]
    #   orientação invertida:  a[i] <-> b[(shift - i) % n]
    # No caso invertido, o lado que sai de a[i] liga b[shift-i] a b[shift-i-1],
    # cujo índice de lado é (shift - i - 1) % n. Errar esse deslocamento de uma
    # posição alinha os comprimentos e desalinha os vértices, o que é pior do
    # que não alinhar nada.
    for flip in (False, True):
        for shift in range(n):
            cost = (
                sum(
                    abs(lengths_a[i] - lengths_b[_side_index(i, shift, n, flip)])
                    for i in range(n)
                )
                / n
            )
            if best is None or cost < best[0]:
                best = (cost, shift, flip)

    assert best is not None
    cost, shift, flip = best
    result.rotation_offset = shift
    result.reversed_orientation = flip
    result.notes.append(
        f"Alinhamento cíclico: deslocamento de {shift} vértice(s), "
        f"orientação {'invertida' if flip else 'preservada'}, "
        f"erro médio de {format_br(cost, 3)} m na sequência de lados."
    )

    for index_a in range(n):
        index_b = _vertex_index(index_a, shift, n, flip)
        result.pairs.append(MatchPair(index_a, index_b, "", ""))
    return result


def _complete_with_geometry(
    result: MatchResult,
    points_a: Sequence[Point],
    points_b: Sequence[Point],
    max_residual_m: float,
) -> None:
    """Casa por geometria os vértices que o código deixou de fora.

    O caso real é um único vértice renomeado entre as duas versões do documento.
    Desistir dele produziria dois achados estruturais falsos — "vértice sem
    correspondente" nos dois lados — quando a correspondência é geometricamente
    inequívoca. O deslocamento usado vem dos pares já casados por código.
    """
    if not (result.unmatched_a and result.unmatched_b):
        return

    if result.pairs:
        deltas = [
            (
                points_b[pair.index_b][0] - points_a[pair.index_a][0],
                points_b[pair.index_b][1] - points_a[pair.index_a][1],
            )
            for pair in result.pairs
        ]
        translation = (
            sum(d[0] for d in deltas) / len(deltas),
            sum(d[1] for d in deltas) / len(deltas),
        )
    else:
        translation = (0.0, 0.0)

    leftovers_a = list(result.unmatched_a)
    leftovers_b = list(result.unmatched_b)
    subset = match_geometric(
        [points_a[i] for i in leftovers_a],
        [points_b[i] for i in leftovers_b],
        max_residual_m=max_residual_m,
        translation=translation,
    )
    if not subset.pairs:
        return

    for pair in subset.pairs:
        index_a = leftovers_a[pair.index_a]
        index_b = leftovers_b[pair.index_b]
        result.pairs.append(
            MatchPair(index_a, index_b, "", "", residual_m=pair.residual_m)
        )
        result.unmatched_a.remove(index_a)
        result.unmatched_b.remove(index_b)

    result.pairs.sort(key=lambda pair: pair.index_a)
    result.notes.append(
        f"{len(subset.pairs)} vértice(s) sem rótulo correspondente foram casados "
        "por proximidade, usando o deslocamento dos vértices já casados por código. "
        "Confirme na revisão se o pareamento está correto."
    )


def _vertex_index(index_a: int, shift: int, n: int, flip: bool) -> int:
    return (shift - index_a) % n if flip else (index_a + shift) % n


def _side_index(index_a: int, shift: int, n: int, flip: bool) -> int:
    """Índice do lado de B correspondente ao lado que sai do vértice ``index_a`` de A."""
    return (shift - index_a - 1) % n if flip else (index_a + shift) % n


def estimate_similarity(
    points_a: Sequence[Point], points_b: Sequence[Point]
) -> tuple[float, float, float, float, float]:
    """Transformação de similaridade 2D (Helmert) de A para B.

    Retorna ``(tx, ty, rotação em graus, escala, RMS dos resíduos)``. Solução
    fechada por mínimos quadrados — sem iteração, sem chute inicial.
    """
    if len(points_a) != len(points_b) or len(points_a) < 2:
        raise ValueError("estimate_similarity exige ao menos 2 pares correspondentes")

    array_a = np.asarray(points_a, dtype=float)
    array_b = np.asarray(points_b, dtype=float)
    mean_a = array_a.mean(axis=0)
    mean_b = array_b.mean(axis=0)
    centered_a = array_a - mean_a
    centered_b = array_b - mean_b

    numerator = float(
        np.sum(centered_a[:, 0] * centered_b[:, 1] - centered_a[:, 1] * centered_b[:, 0])
    )
    denominator = float(
        np.sum(centered_a[:, 0] * centered_b[:, 0] + centered_a[:, 1] * centered_b[:, 1])
    )
    rotation = math.atan2(numerator, denominator)

    norm_a = float(np.sum(centered_a**2))
    scale = (
        math.hypot(numerator, denominator) / norm_a if norm_a > 0 else 1.0
    )

    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    rotation_matrix = np.array([[cos_r, -sin_r], [sin_r, cos_r]])
    transformed = scale * centered_a @ rotation_matrix.T + mean_b
    residuals = np.linalg.norm(array_b - transformed, axis=1)
    rms = float(np.sqrt(np.mean(residuals**2)))

    translation = mean_b - scale * rotation_matrix @ mean_a
    return (
        float(translation[0]),
        float(translation[1]),
        math.degrees(rotation) % 360.0,
        scale,
        rms,
    )


def detect_systematic(
    points_a: Sequence[Point],
    points_b: Sequence[Point],
    translation_threshold_m: float = 0.20,
    rotation_threshold_deg: float = 0.002,
    scale_threshold: float = 2e-5,
    residual_ratio: float = 0.25,
) -> SystematicOffset | None:
    """Identifica se a divergência entre os conjuntos é um padrão único.

    O critério é a razão entre o resíduo que sobra depois de aplicar a
    transformação e a magnitude da própria transformação. Resíduo pequeno
    significa que um único parâmetro explica quase toda a diferença — logo, é um
    problema sistemático, não vértices individualmente errados.
    """
    if len(points_a) != len(points_b) or len(points_a) < 3:
        return None

    tx, ty, rotation_deg, scale, rms = estimate_similarity(points_a, points_b)

    array_a = np.asarray(points_a, dtype=float)
    array_b = np.asarray(points_b, dtype=float)
    raw_delta = array_b - array_a
    mean_delta = raw_delta.mean(axis=0)
    translation_magnitude = float(np.hypot(*mean_delta))
    translation_rms = float(
        np.sqrt(np.mean(np.linalg.norm(raw_delta - mean_delta, axis=1) ** 2))
    )

    signed_rotation = (rotation_deg + 180.0) % 360.0 - 180.0
    scale_ppm = (scale - 1.0) * 1e6
    azimuth = grid_azimuth((0.0, 0.0), (float(mean_delta[0]), float(mean_delta[1])))

    # Translação pura: o resíduo em torno do deslocamento médio é pequeno
    # comparado ao próprio deslocamento.
    if (
        translation_magnitude > translation_threshold_m
        and translation_rms < residual_ratio * translation_magnitude
    ):
        return SystematicOffset(
            kind=SystematicKind.TRANSLATION,
            translation_e=float(mean_delta[0]),
            translation_n=float(mean_delta[1]),
            rotation_deg=signed_rotation,
            scale=scale,
            residual_rms_m=translation_rms,
            magnitude=translation_magnitude,
            azimuth_deg=azimuth,
            message=(
                f"Deslocamento sistemático de {format_br(translation_magnitude, 3)} m "
                f"no azimute {format_br(azimuth, 1)}°, com resíduo de apenas "
                f"{format_br(translation_rms, 3)} m. "
                "Compatível com divergência de datum ou de origem das coordenadas, "
                "não com erro vértice a vértice."
            ),
        )

    span = float(np.max(np.linalg.norm(array_a - array_a.mean(axis=0), axis=1))) or 1.0

    if abs(signed_rotation) > rotation_threshold_deg and rms < residual_ratio * (
        abs(math.radians(signed_rotation)) * span
    ):
        return SystematicOffset(
            kind=SystematicKind.ROTATION,
            translation_e=tx,
            translation_n=ty,
            rotation_deg=signed_rotation,
            scale=scale,
            residual_rms_m=rms,
            magnitude=abs(signed_rotation),
            azimuth_deg=azimuth,
            message=(
                f"Rotação sistemática de {format_br(signed_rotation * 3600, 0)}\" "
                "entre os documentos, com resíduo baixo. Compatível com norte de "
                "referência "
                "diferente (magnético, verdadeiro ou de grade)."
            ),
        )

    if abs(scale_ppm) > scale_threshold * 1e6 and rms < residual_ratio * (
        abs(scale - 1.0) * span
    ):
        return SystematicOffset(
            kind=SystematicKind.SCALE,
            translation_e=tx,
            translation_n=ty,
            rotation_deg=signed_rotation,
            scale=scale,
            residual_rms_m=rms,
            magnitude=abs(scale_ppm),
            azimuth_deg=azimuth,
            message=(
                f"Fator de escala sistemático de {'+' if scale_ppm >= 0 else '−'}"
                f"{format_br(abs(scale_ppm), 1)} ppm. Compatível com "
                "distância de grade tratada como distância de campo, ou ausência de "
                "redução ao nível do elipsoide."
            ),
        )

    return None


def match_vertices(
    codes_a: Sequence[str],
    codes_b: Sequence[str],
    points_a: Sequence[Point] | None = None,
    points_b: Sequence[Point] | None = None,
    max_residual_m: float = 5.0,
    min_code_coverage: float = 0.6,
) -> MatchResult:
    """Aplica a cascata de estratégias e devolve o melhor casamento.

    Código é aceito quando cobre a maior parte dos vértices. Caso contrário cai
    para geometria e, se ainda sobrar muita coisa sem par, para alinhamento
    cíclico.
    """
    by_code = match_by_code(codes_a, codes_b)
    expected = min(len(codes_a), len(codes_b)) or 1
    coverage = len(by_code.pairs) / expected

    if coverage >= min_code_coverage:
        result = by_code
        if points_a and points_b:
            _complete_with_geometry(result, points_a, points_b, max_residual_m)
    elif points_a and points_b:
        result = match_geometric(points_a, points_b, max_residual_m=max_residual_m)
        if len(result.pairs) / expected < min_code_coverage and len(
            points_a
        ) == len(points_b):
            result = align_cyclic(points_a, points_b)
        result.notes.insert(
            0,
            f"Casamento por código cobriu apenas {coverage:.0%} dos vértices; "
            "usada estratégia geométrica.",
        )
    else:
        result = by_code
        result.notes.append(
            "Sem coordenadas projetadas nos dois documentos: não foi possível "
            "recorrer ao casamento geométrico."
        )

    if points_a and points_b and result.pairs:
        paired_a = [points_a[p.index_a] for p in result.pairs]
        paired_b = [points_b[p.index_b] for p in result.pairs]
        result.systematic = detect_systematic(paired_a, paired_b)
        for pair_index, pair in enumerate(result.pairs):
            if pair.residual_m is None:
                distance = grid_distance(paired_a[pair_index], paired_b[pair_index])
                result.pairs[pair_index] = MatchPair(
                    pair.index_a,
                    pair.index_b,
                    codes_a[pair.index_a] if pair.index_a < len(codes_a) else "",
                    codes_b[pair.index_b] if pair.index_b < len(codes_b) else "",
                    residual_m=distance,
                )
    return result
