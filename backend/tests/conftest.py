"""Fábricas de imóveis sintéticos para os testes.

A estratégia dos testes de comparação é sempre a mesma: construir um documento
*internamente consistente*, introduzir um defeito específico, e verificar que o
motor aponta exatamente aquele defeito e nada além dele. Falso positivo é
tratado como falha de teste tanto quanto falso negativo.
"""

from __future__ import annotations

import pytest

from app.core.crs import CRSSpec
from app.core.geodesy import (
    Point,
    combined_factor,
    grid_to_ground,
    polygon_area,
    segment_azimuths,
    segment_lengths,
)
from app.core.schema import Measured, Parcel, Provenance, Segment, SourceKind, Vertex

# Quadrado de 100 m em SIRGAS 2000 / UTM 23S, região de São Paulo.
SP_UTM23S = CRSSpec(
    epsg="EPSG:31983", datum_label="SIRGAS2000", utm_zone=23, hemisphere="S"
)
BASE_EASTING = 333_000.0
BASE_NORTHING = 7_394_000.0

SQUARE: list[Point] = [
    (BASE_EASTING, BASE_NORTHING),
    (BASE_EASTING + 100.0, BASE_NORTHING),
    (BASE_EASTING + 100.0, BASE_NORTHING + 100.0),
    (BASE_EASTING, BASE_NORTHING + 100.0),
]


def _provenance(document_id: str, row: int, column: int, raw: str) -> Provenance:
    return Provenance(
        document_id=document_id,
        page=1,
        bbox=(50.0, 100.0 + row * 12.0, 120.0, 112.0 + row * 12.0),
        source_kind=SourceKind.TABLE_CELL,
        table_index=0,
        row=row,
        column=column,
        raw_text=raw,
    )


def measured(
    value: float,
    decimals: int,
    unit: str,
    document_id: str,
    row: int,
    column: int,
    confidence: float = 1.0,
) -> Measured:
    """Cria um ``Measured`` arredondado como o documento o imprimiria."""
    rounded = round(value, decimals)
    return Measured(
        value=rounded,
        halfwidth=0.5 * 10.0**-decimals,
        unit=unit,
        confidence=confidence,
        provenance=_provenance(document_id, row, column, f"{rounded:.{decimals}f}"),
    )


def build_parcel(
    label: str,
    ring: list[Point],
    document_id: str = "doc-a",
    codes: list[str] | None = None,
    crs: CRSSpec | None = SP_UTM23S,
    coordinate_decimals: int = 3,
    distance_decimals: int = 2,
    declare_area: bool = True,
    declare_perimeter: bool = True,
    confidence: float = 1.0,
) -> Parcel:
    """Monta um imóvel cujos valores declarados derivam da própria geometria.

    O resultado é internamente consistente por construção: distâncias
    declaradas são as de campo (grade reduzida pelo fator combinado), azimutes
    são os de grade, e área e perímetro batem com os vértices.
    """
    codes = codes or [f"P{i + 1}" for i in range(len(ring))]
    vertices = [
        Vertex(
            code=code,
            easting=measured(
                point[0], coordinate_decimals, "m", document_id, index, 1
            ),
            northing=measured(
                point[1], coordinate_decimals, "m", document_id, index, 2
            ),
            code_provenance=_provenance(document_id, index, 0, code),
        )
        for index, (code, point) in enumerate(zip(codes, ring, strict=True))
    ]

    mean_easting = sum(p[0] for p in ring) / len(ring)
    factor = (
        combined_factor(mean_easting, -23.55) if crs and crs.is_projected else 1.0
    )
    grid_lengths = segment_lengths(ring)
    ground_lengths = [grid_to_ground(d, factor) for d in grid_lengths]
    azimuths = segment_azimuths(ring)

    segments = [
        Segment(
            from_index=index,
            to_index=(index + 1) % len(ring),
            azimuth=measured(azimuths[index], 6, "°", document_id, index, 3),
            distance=measured(
                ground_lengths[index], distance_decimals, "m", document_id, index, 4
            ),
        )
        for index in range(len(ring))
    ]

    parcel = Parcel(
        label=label,
        crs=crs,
        vertices=vertices,
        segments=segments,
        average_height_m=0.0,
    )
    if declare_area:
        parcel.area = measured(polygon_area(ring), 2, "m²", document_id, 90, 1)
    if declare_perimeter:
        parcel.perimeter = measured(
            sum(ground_lengths), distance_decimals, "m", document_id, 91, 1
        )
    if confidence < 1.0:
        for vertex in parcel.vertices:
            for name in ("easting", "northing"):
                current = getattr(vertex, name)
                setattr(
                    vertex,
                    name,
                    Measured(
                        value=current.value,
                        halfwidth=current.halfwidth,
                        unit=current.unit,
                        confidence=confidence,
                        provenance=current.provenance,
                    ),
                )
    return parcel


def shifted(ring: list[Point], de: float, dn: float) -> list[Point]:
    return [(p[0] + de, p[1] + dn) for p in ring]


@pytest.fixture
def square_ring() -> list[Point]:
    return list(SQUARE)


@pytest.fixture
def parcel_a(square_ring: list[Point]) -> Parcel:
    return build_parcel("Documento A", square_ring, document_id="doc-a")


@pytest.fixture
def parcel_b(square_ring: list[Point]) -> Parcel:
    return build_parcel("Documento B", square_ring, document_id="doc-b")
