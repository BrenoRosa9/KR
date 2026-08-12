"""Mapeamento de cabeçalhos heterogêneos para campos canônicos.

Cada escritório nomeia as colunas do seu jeito: ``Vért.``, ``Estaca``,
``Ponto``, ``Est.``. Este módulo resolve deterministicamente os casos
conhecidos, que na prática cobrem a maior parte do acervo.

Os que sobrarem são exatamente onde um modelo de linguagem ganha o seu lugar:
mapear um cabeçalho nunca visto custa uma chamada, e o resultado — depois de
confirmado por um humano — vira template salvo, tornando os documentos
seguintes do mesmo layout puramente determinísticos.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.schema import FieldKind
from .text import normalize_label

# Ordem importa: rótulos mais específicos primeiro, porque "coordenada n" tem
# que ganhar de "n" e "azimute plano" de "azimute".
HEADER_PATTERNS: list[tuple[FieldKind, tuple[str, ...]]] = [
    (
        FieldKind.VERTEX_CODE,
        (
            "vertice",
            "vert",
            "codigo do vertice",
            "cod vertice",
            "ponto",
            "estaca",
            "est",
            "marco",
            "identificacao",
            "id",
            # "DE" e "PARA" nomeiam as pontas do lado em memoriais brasileiros.
            # Entram só como casamento exato: como prefixo, "de" apareceria em
            # metade dos rótulos do documento.
            "de",
            "de para",
            "vertice inicial",
            "origem",
        ),
    ),
    (
        FieldKind.NORTHING,
        (
            "coordenada n",
            "coord n",
            "utm n",
            "n utm",
            "utm norte",
            "norte",
            "northing",
            "n m",
            "y",
            "n",
        ),
    ),
    (
        FieldKind.EASTING,
        (
            "coordenada e",
            "coord e",
            "utm e",
            "e utm",
            "utm este",
            "este",
            "leste",
            "easting",
            "e m",
            "x",
            "e",
        ),
    ),
    (
        FieldKind.LATITUDE,
        ("latitude", "lat"),
    ),
    (
        FieldKind.LONGITUDE,
        ("longitude", "long", "lon"),
    ),
    (
        FieldKind.AZIMUTH,
        (
            "azimute plano",
            "azimute verdadeiro",
            "azimute",
            "az",
            "rumo",
            "orientacao",
        ),
    ),
    (
        FieldKind.DISTANCE,
        (
            "distancia plana",
            "distancia horizontal",
            "distancia",
            "dist",
            "extensao",
            "lado",
            "comprimento",
        ),
    ),
    (
        FieldKind.INTERIOR_ANGLE,
        (
            "angulo interno",
            "ang interno",
            "angulo",
            "deflexao",
        ),
    ),
    (
        FieldKind.CONFRONTANT,
        (
            "confrontante",
            "confrontacao",
            "confronta",
            "limite",
            "divisa",
            "lindeiro",
        ),
    ),
    (
        FieldKind.MATRICULA,
        ("matricula", "matr", "transcricao"),
    ),
]

# Cabeçalhos de duas linhas: "COORDENADAS" acima de "N | E". A primeira linha é
# um agrupador e não deve ser confundida com um campo.
#
# Só entram aqui rótulos que *nunca* são campo por si mesmos. "Vértice" e "Lado"
# ficam de fora de propósito: no plural agrupador eles são raros, e no singular
# são os cabeçalhos mais comuns que existem — bloqueá-los deixaria a coluna de
# código de vértice sem mapear no layout mais banal do acervo.
GROUPING_LABELS = frozenset(
    {
        "coordenadas",
        "coordenadas utm",
        "coordenadas planas",
        "coordenadas geograficas",
        "coordenadas geodesicas",
    }
)


# Pares que descrevem as duas pontas de um lado. Quando os dois rótulos caem na
# mesma célula ("Vértice Vértice", "De Para"), a coluna guarda origem e destino
# juntos e precisa ser desmembrada na leitura das linhas.
PAIR_LABELS = frozenset(
    {
        ("vertice", "vertice"),
        ("de", "para"),
        ("origem", "destino"),
        ("inicial", "final"),
        ("vertice inicial", "vertice final"),
        ("ponto", "ponto"),
        ("estaca", "estaca"),
    }
)


def _is_paired_label(normalized: str) -> bool:
    # Os dois últimos tokens, e não a string inteira, porque o par costuma vir
    # precedido do agrupador: "LADOS Vértice Vértice".
    tokens = normalized.split()
    if len(tokens) < 2:
        return False
    return (tokens[-2], tokens[-1]) in PAIR_LABELS


@dataclass(frozen=True)
class HeaderMapping:
    """Resultado do mapeamento de uma linha de cabeçalho."""

    columns: dict[int, FieldKind]
    header_row: int
    unmapped: dict[int, str]
    confidence: float
    # A coluna de código traz "origem destino" na mesma célula.
    code_is_pair: bool = False

    @property
    def has_coordinates(self) -> bool:
        kinds = set(self.columns.values())
        return {FieldKind.EASTING, FieldKind.NORTHING} <= kinds or {
            FieldKind.LATITUDE,
            FieldKind.LONGITUDE,
        } <= kinds

    def column_of(self, kind: FieldKind) -> int | None:
        for index, mapped in self.columns.items():
            if mapped == kind:
                return index
        return None


def match_header(label: str) -> FieldKind | None:
    """Resolve um rótulo isolado para um campo canônico."""
    normalized = normalize_label(label)
    if not normalized or normalized in GROUPING_LABELS:
        return None

    for kind, aliases in HEADER_PATTERNS:
        if normalized in aliases:
            return kind

    # Casamento por prefixo/contenção, para "azimute (gms)" e afins. Exige
    # alias com 3+ caracteres para não casar "e" dentro de qualquer palavra.
    for kind, aliases in HEADER_PATTERNS:
        for alias in aliases:
            if len(alias) >= 3 and (
                normalized.startswith(alias) or f" {alias} " in f" {normalized} "
            ):
                return kind
    return None


def map_header_row(grid: list[list[str]], max_scan_rows: int = 5) -> HeaderMapping | None:
    """Encontra e mapeia a linha de cabeçalho de uma tabela.

    Varre as primeiras linhas porque tabelas reais quase nunca começam no
    cabeçalho: costuma haver título, subtítulo ou linha de agrupamento antes.
    Escolhe a linha que mapeia mais colunas.
    """
    best: HeaderMapping | None = None

    for row_index, row in enumerate(grid[:max_scan_rows]):
        columns: dict[int, FieldKind] = {}
        unmapped: dict[int, str] = {}
        code_is_pair = False
        for column_index, label in enumerate(row):
            kind = match_header(label)
            if kind is not None and kind not in columns.values():
                columns[column_index] = kind
                if kind == FieldKind.VERTEX_CODE:
                    code_is_pair = _is_paired_label(normalize_label(label))
            elif label.strip():
                unmapped[column_index] = label.strip()

        if not columns:
            continue

        filled = sum(1 for label in row if label.strip()) or 1
        candidate = HeaderMapping(
            columns=columns,
            header_row=row_index,
            unmapped=unmapped,
            confidence=len(columns) / filled,
            code_is_pair=code_is_pair,
        )
        if best is None or len(candidate.columns) > len(best.columns):
            best = candidate

    return best


def merge_two_row_header(grid: list[list[str]], header_row: int) -> list[str]:
    """Concatena cabeçalho de duas linhas antes de mapear.

    ``COORDENADAS`` sobre ``N | E`` só faz sentido lido em conjunto; separado,
    ``N`` é ambíguo entre Northing e número de ordem.
    """
    if header_row + 1 >= len(grid):
        return grid[header_row]

    upper, lower = grid[header_row], grid[header_row + 1]
    merged: list[str] = []
    for index in range(max(len(upper), len(lower))):
        top = upper[index] if index < len(upper) else ""
        bottom = lower[index] if index < len(lower) else ""
        merged.append(f"{top} {bottom}".strip())
    return merged
