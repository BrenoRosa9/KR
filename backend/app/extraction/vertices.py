"""Montagem do imóvel a partir de uma tabela de vértices.

Além do parsing linha a linha, este módulo resolve uma ambiguidade que quase
nenhuma planilha declara: o azimute e a distância de uma linha descrevem o lado
que *sai* daquele vértice ou o que *chega* nele? As duas convenções existem em
documentos reais, e escolher errado desloca todos os lados em uma posição —
produzindo um relatório inteiro de divergências falsas.

Quando há coordenadas, a ambiguidade é resolvida por evidência: testamos as
duas interpretações contra os azimutes recalculados e adotamos a que concorda.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.angles import angular_difference, normalize_azimuth, parse_angle
from ..core.crs import plausible_for_brazil, to_geographic
from ..core.geodesy import segment_azimuths
from ..core.numbers import format_br, parse_number
from ..core.schema import (
    FieldKind,
    Measured,
    Parcel,
    Provenance,
    Segment,
    SourceKind,
    TextValue,
    Vertex,
)
from .context import DocumentContext
from .headers import HeaderMapping
from .tables import Cell, ExtractedTable
from .text import normalize_label

# Linhas de rodapé que não são vértices e precisam ser descartadas antes do
# parsing, senão "TOTAL 400,02" viraria um vértice com coordenada inválida.
FOOTER_LABELS = frozenset(
    {"total", "totais", "perimetro", "soma", "area", "fechamento", "somatorio"}
)

# Lado curvo, na notação usada em loteamentos: "R6,00-D8,24" é raio 6,00 m e
# desenvolvimento (comprimento do arco) 8,24 m.
_ARC_RE = re.compile(
    r"R\s*([\d.,]+\d)\s*[-–—/]\s*D\s*([\d.,]+\d)", re.IGNORECASE
)
# "AC 78°40'56"": ângulo central da curva, ocupando a coluna do ângulo interno.
_CENTRAL_ANGLE_RE = re.compile(r"^\s*A\.?\s*C\.?\s*[:=]?\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class CellSource:
    """Documento e página de onde as células desta tabela vieram."""

    document_id: str
    page: int
    source_kind: SourceKind = SourceKind.TABLE_CELL
    table_index: int = 0

    def of(self, cell: Cell | None) -> Provenance | None:
        if cell is None:
            return None
        return Provenance(
            document_id=self.document_id,
            # Tabela emendada guarda a página em cada célula.
            page=cell.page if cell.page is not None else self.page,
            bbox=cell.bbox,
            source_kind=self.source_kind,
            table_index=self.table_index,
            row=cell.row,
            column=cell.column,
            raw_text=cell.text,
        )


@dataclass(frozen=True)
class ArcDeclaration:
    radius: Measured
    development: Measured
    central_angle: Measured | None


@dataclass
class VertexTableResult:
    parcel: Parcel
    rows_read: int = 0
    rows_skipped: int = 0
    segment_convention: str = "leading"
    warnings: list[str] = field(default_factory=list)


def build_parcel_from_table(
    table: ExtractedTable,
    mapping: HeaderMapping,
    context: DocumentContext,
    document_id: str,
    label: str,
    base_confidence: float = 1.0,
    source_kind: SourceKind = SourceKind.TABLE_CELL,
) -> VertexTableResult:
    """Converte uma tabela mapeada em :class:`Parcel` normalizado."""
    parcel = Parcel(label=label, crs=context.crs)
    result = VertexTableResult(parcel=parcel)
    source = CellSource(
        document_id=document_id,
        page=table.page,
        source_kind=source_kind,
        table_index=table.index,
    )

    declared_azimuths: list[Measured | None] = []
    declared_distances: list[Measured | None] = []
    declared_arcs: list[ArcDeclaration | None] = []
    confrontants: list[TextValue | None] = []

    for row_index in range(mapping.header_row + 1, table.row_count):
        cells = {cell.column: cell for cell in table.row(row_index)}
        if _is_footer(cells, mapping):
            result.rows_skipped += 1
            continue

        vertex = _build_vertex(
            cells, mapping, context, source, base_confidence, row_index
        )
        if vertex is None:
            result.rows_skipped += 1
            continue

        parcel.vertices.append(vertex)
        declared_azimuths.append(
            _measured_angle(
                cells, mapping, FieldKind.AZIMUTH, context, source, base_confidence
            )
        )
        arc = _read_arc(cells, mapping, context, source, base_confidence)
        declared_arcs.append(arc)
        # Num lado curvo o número da coluna de distância é o desenvolvimento do
        # arco, não a distância entre os vértices. Deixar o campo vazio impede
        # que ele seja confrontado com a corda.
        declared_distances.append(
            None
            if arc is not None
            else _measured_number(
                cells, mapping, FieldKind.DISTANCE, context, source, "m", base_confidence
            )
        )
        confrontants.append(_text_value(cells, mapping, source, base_confidence))
        result.rows_read += 1

    if not parcel.vertices:
        result.warnings.append("Nenhuma linha de vértice válida encontrada na tabela.")
        return result

    convention = _choose_segment_convention(parcel, declared_azimuths, result)
    result.segment_convention = convention
    _attach_segments(
        parcel,
        declared_azimuths,
        declared_distances,
        declared_arcs,
        confrontants,
        convention,
    )

    curves = sum(1 for arc in declared_arcs if arc is not None)
    if curves:
        result.warnings.append(
            f"{curves} lado(s) descrito(s) como curva (raio e desenvolvimento). "
            "Nesses lados a conferência é feita pela corda e pelo desenvolvimento "
            "esperados do arco, não pela distância reta."
        )
    _validate_coordinates(parcel, result)

    parcel.area = context.area
    parcel.perimeter = context.perimeter
    parcel.matricula = context.matricula
    parcel.tax_ids = list(context.tax_ids)
    parcel.area_citations = list(context.area_citations)
    parcel.perimeter_citations = list(context.perimeter_citations)
    parcel.warnings.extend(context.warnings)
    parcel.warnings.extend(result.warnings)
    return result


def _is_footer(cells: dict[int, Cell], mapping: HeaderMapping) -> bool:
    code_column = mapping.column_of(FieldKind.VERTEX_CODE)
    if code_column is not None:
        cell = cells.get(code_column)
        if cell and normalize_label(cell.text) in FOOTER_LABELS:
            return True
    joined = normalize_label(" ".join(cell.text for cell in cells.values()))
    return any(joined.startswith(label) for label in FOOTER_LABELS)


def _build_vertex(
    cells: dict[int, Cell],
    mapping: HeaderMapping,
    context: DocumentContext,
    source: CellSource,
    base_confidence: float,
    row_index: int,
) -> Vertex | None:
    code_column = mapping.column_of(FieldKind.VERTEX_CODE)
    code_cell = cells.get(code_column) if code_column is not None else None
    code = code_cell.text.strip() if code_cell else ""

    if mapping.code_is_pair and code:
        # A célula traz "origem destino" ("0PP 1"). O vértice da linha é a
        # origem; o destino é o vértice da linha seguinte e seria redundante.
        code = code.split()[0]

    easting = _measured_number(
        cells, mapping, FieldKind.EASTING, context, source, "m", base_confidence
    )
    northing = _measured_number(
        cells, mapping, FieldKind.NORTHING, context, source, "m", base_confidence
    )
    longitude = _measured_angle(
        cells, mapping, FieldKind.LONGITUDE, context, source, base_confidence
    )
    latitude = _measured_angle(
        cells, mapping, FieldKind.LATITUDE, context, source, base_confidence
    )

    has_position = (easting and northing) or (longitude and latitude)
    if not has_position:
        # Linha com rótulo mas sem coordenada nenhuma: provavelmente célula
        # quebrada em duas linhas, não um vértice.
        return None

    return Vertex(
        code=code or f"#{row_index}",
        easting=easting,
        northing=northing,
        longitude=longitude,
        latitude=latitude,
        code_provenance=source.of(code_cell),
    )


def _measured_number(
    cells: dict[int, Cell],
    mapping: HeaderMapping,
    kind: FieldKind,
    context: DocumentContext,
    source: CellSource,
    unit: str,
    base_confidence: float,
) -> Measured | None:
    cell = _cell_for(cells, mapping, kind)
    if cell is None:
        return None

    parsed = parse_number(cell.text, context.convention)
    if parsed is None:
        return None

    return Measured(
        value=parsed.value,
        halfwidth=parsed.rounding_halfwidth,
        unit=unit,
        confidence=base_confidence,
        provenance=source.of(cell),
    )


def _measured_angle(
    cells: dict[int, Cell],
    mapping: HeaderMapping,
    kind: FieldKind,
    context: DocumentContext,
    source: CellSource,
    base_confidence: float,
) -> Measured | None:
    cell = _cell_for(cells, mapping, kind)
    if cell is None:
        return None

    parsed = parse_angle(cell.text, context.convention)
    if parsed is None:
        return None

    value = (
        normalize_azimuth(parsed.degrees)
        if kind == FieldKind.AZIMUTH
        else parsed.degrees
    )
    return Measured(
        value=value,
        halfwidth=parsed.rounding_halfwidth_deg,
        unit="°",
        confidence=base_confidence,
        provenance=source.of(cell),
    )


def _text_value(
    cells: dict[int, Cell],
    mapping: HeaderMapping,
    source: CellSource,
    base_confidence: float,
) -> TextValue | None:
    cell = _cell_for(cells, mapping, FieldKind.CONFRONTANT)
    if cell is None:
        return None
    return TextValue(
        value=cell.text, confidence=base_confidence, provenance=source.of(cell)
    )


def _cell_for(
    cells: dict[int, Cell], mapping: HeaderMapping, kind: FieldKind
) -> Cell | None:
    column = mapping.column_of(kind)
    if column is None:
        return None
    cell = cells.get(column)
    if cell is None or cell.is_empty:
        return None
    return cell


def _choose_segment_convention(
    parcel: Parcel,
    declared_azimuths: list[Measured | None],
    result: VertexTableResult,
) -> str:
    """Decide se o azimute da linha descreve o lado que sai ou o que chega.

    Com coordenadas disponíveis, a decisão é medida, não presumida: comparamos a
    sequência declarada com a recalculada nas duas interpretações e adotamos a
    de menor discordância. Errar isso desloca todos os lados em uma posição.
    """
    if not parcel.has_projected_ring:
        result.warnings.append(
            "Sem anel completo de coordenadas: assumida a convenção de que azimute e "
            "distância descrevem o lado que parte do vértice da linha. Confirme na "
            "revisão."
        )
        return "leading"

    values = [m.value if m is not None else None for m in declared_azimuths]
    if sum(1 for value in values if value is not None) < 2:
        return "leading"

    computed = segment_azimuths(parcel.ring())
    n = len(computed)

    def discordance(offset: int) -> float:
        total, count = 0.0, 0
        for index, value in enumerate(values):
            if value is None:
                continue
            total += abs(angular_difference(value, computed[(index + offset) % n]))
            count += 1
        return total / count if count else float("inf")

    leading = discordance(0)
    trailing = discordance(-1)

    if trailing < leading * 0.5:
        result.warnings.append(
            f"Azimutes concordam melhor com a convenção de lado *chegando* ao vértice "
            f"(discordância média {format_br(trailing, 4)}° contra "
            f"{format_br(leading, 4)}°). Os lados "
            "foram deslocados de acordo."
        )
        return "trailing"
    return "leading"


def _read_arc(
    cells: dict[int, Cell],
    mapping: HeaderMapping,
    context: DocumentContext,
    source: CellSource,
    base_confidence: float,
) -> ArcDeclaration | None:
    cell = _cell_for(cells, mapping, FieldKind.DISTANCE)
    if cell is None:
        return None

    match = _ARC_RE.search(cell.text)
    if match is None:
        return None

    radius = parse_number(match.group(1), context.convention)
    development = parse_number(match.group(2), context.convention)
    if radius is None or development is None:
        return None

    provenance = source.of(cell)
    return ArcDeclaration(
        radius=Measured(
            value=radius.value,
            halfwidth=radius.rounding_halfwidth,
            unit="m",
            confidence=base_confidence,
            provenance=provenance,
        ),
        development=Measured(
            value=development.value,
            halfwidth=development.rounding_halfwidth,
            unit="m",
            confidence=base_confidence,
            provenance=provenance,
        ),
        central_angle=_read_central_angle(
            cells, mapping, context, source, base_confidence
        ),
    )


def _read_central_angle(
    cells: dict[int, Cell],
    mapping: HeaderMapping,
    context: DocumentContext,
    source: CellSource,
    base_confidence: float,
) -> Measured | None:
    cell = _cell_for(cells, mapping, FieldKind.INTERIOR_ANGLE)
    if cell is None:
        return None

    match = _CENTRAL_ANGLE_RE.match(cell.text)
    if match is None:
        return None

    parsed = parse_angle(match.group(1), context.convention)
    if parsed is None:
        return None

    return Measured(
        value=parsed.degrees,
        halfwidth=parsed.rounding_halfwidth_deg,
        unit="°",
        confidence=base_confidence,
        provenance=source.of(cell),
    )


def _attach_segments(
    parcel: Parcel,
    azimuths: list[Measured | None],
    distances: list[Measured | None],
    arcs: list[ArcDeclaration | None],
    confrontants: list[TextValue | None],
    convention: str,
) -> None:
    count = len(parcel.vertices)
    for index in range(count):
        source = index if convention == "leading" else (index + 1) % count
        arc = arcs[source] if source < len(arcs) else None
        parcel.segments.append(
            Segment(
                from_index=index,
                to_index=(index + 1) % count,
                azimuth=azimuths[source] if source < len(azimuths) else None,
                distance=distances[source] if source < len(distances) else None,
                confrontant=confrontants[source] if source < len(confrontants) else None,
                arc_radius=arc.radius if arc else None,
                arc_development=arc.development if arc else None,
                central_angle=arc.central_angle if arc else None,
            )
        )


def _validate_coordinates(parcel: Parcel, result: VertexTableResult) -> None:
    """Sanidade geográfica: o imóvel caiu dentro do Brasil?

    É a validação cruzada mais barata contra erro de OCR e contra coluna N/E
    invertida. Um dígito trocado no Northing joga o imóvel para o oceano, e é
    melhor descobrir aqui do que no laudo.
    """
    if parcel.crs is None or not parcel.crs.is_projected:
        return
    if not parcel.has_projected_ring:
        return

    outside = 0
    ring = parcel.ring()
    for easting, northing in ring:
        try:
            longitude, latitude = to_geographic(parcel.crs, easting, northing)
        except Exception:  # pragma: no cover - CRS inconsistente
            return
        if not plausible_for_brazil(longitude, latitude):
            outside += 1

    if outside:
        result.warnings.append(
            f"{outside} de {len(ring)} vértices caem fora do território brasileiro "
            "após a projeção. Verifique se as colunas N e E não estão trocadas, se o "
            "fuso está correto e se não houve erro de leitura."
        )
