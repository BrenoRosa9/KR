"""Modelo canônico de dados extraídos.

Tudo que sai da extração vira uma destas estruturas. A característica que
define o modelo é que nenhum número circula sozinho: cada grandeza é um
:class:`Measured`, que carrega a precisão da origem, a confiança e a
:class:`Provenance` — documento, página, região e texto bruto. Sem isso não há
tela de revisão, não há laudo defensável e não há como derivar tolerância do
arredondamento da fonte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from .crs import CRSSpec
from .geodesy import Point


class FieldKind(StrEnum):
    VERTEX_CODE = "vertex_code"
    EASTING = "easting"
    NORTHING = "northing"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    AZIMUTH = "azimuth"
    DISTANCE = "distance"
    INTERIOR_ANGLE = "interior_angle"
    # Lado curvo: raio, desenvolvimento (comprimento do arco) e ângulo central.
    ARC_RADIUS = "arc_radius"
    ARC_DEVELOPMENT = "arc_development"
    CENTRAL_ANGLE = "central_angle"
    CONFRONTANT = "confrontant"
    AREA = "area"
    PERIMETER = "perimeter"
    MATRICULA = "matricula"
    CPF = "cpf"
    CNPJ = "cnpj"
    UTM_ZONE = "utm_zone"
    DATUM = "datum"


class SourceKind(StrEnum):
    """Como o valor chegou ao sistema. Determina a confiança inicial."""

    TABLE_CELL = "table_cell"
    TEXT_SPAN = "text_span"
    OCR = "ocr"
    VISION_MODEL = "vision_model"
    HUMAN = "human"
    COMPUTED = "computed"


@dataclass(frozen=True)
class Provenance:
    """Onde exatamente este valor foi lido.

    ``bbox`` está em pontos PDF, origem no canto superior esquerdo, no mesmo
    referencial que o frontend usa para desenhar o destaque sobre a página.
    """

    document_id: str
    page: int
    bbox: tuple[float, float, float, float] | None = None
    source_kind: SourceKind = SourceKind.TABLE_CELL
    table_index: int | None = None
    row: int | None = None
    column: int | None = None
    raw_text: str = ""

    def label(self) -> str:
        parts = [f"p. {self.page}"]
        if self.table_index is not None and self.row is not None:
            column = "?" if self.column is None else str(self.column + 1)
            parts.append(
                f"tabela {self.table_index + 1}, linha {self.row + 1}, col. {column}"
            )
        return " · ".join(parts)


@dataclass(frozen=True)
class Measured:
    """Grandeza numérica com precisão de origem, confiança e procedência."""

    value: float
    halfwidth: float = 0.0
    unit: str = ""
    confidence: float = 1.0
    provenance: Provenance | None = None
    edited: bool = False

    @classmethod
    def computed(cls, value: float, unit: str = "") -> Measured:
        """Valor derivado por cálculo, sem arredondamento de origem próprio."""
        return cls(value=value, halfwidth=0.0, unit=unit, confidence=1.0)


@dataclass(frozen=True)
class TextValue:
    """Campo textual (confrontante, matrícula) com procedência."""

    value: str
    confidence: float = 1.0
    provenance: Provenance | None = None
    edited: bool = False


@dataclass
class Vertex:
    """Vértice do perímetro. Coordenadas projetadas e/ou geográficas."""

    code: str
    easting: Measured | None = None
    northing: Measured | None = None
    longitude: Measured | None = None
    latitude: Measured | None = None
    code_provenance: Provenance | None = None

    @property
    def has_projected(self) -> bool:
        return self.easting is not None and self.northing is not None

    @property
    def has_geographic(self) -> bool:
        return self.longitude is not None and self.latitude is not None

    def point(self) -> Point:
        if not self.has_projected:
            raise ValueError(f"Vértice {self.code} sem coordenadas projetadas")
        return (self.easting.value, self.northing.value)  # type: ignore[union-attr]

    def coordinate_halfwidth(self) -> float:
        widths = [m.halfwidth for m in (self.easting, self.northing) if m is not None]
        return max(widths) if widths else 0.0

    def min_confidence(self) -> float:
        values = [
            m.confidence
            for m in (self.easting, self.northing, self.longitude, self.latitude)
            if m is not None
        ]
        return min(values) if values else 0.0


@dataclass
class Segment:
    """Lado do perímetro, do vértice ``from_index`` ao ``to_index``.

    Azimute e distância aqui são os valores *declarados* no documento. Os
    recalculados nunca são gravados no mesmo lugar — é justamente a separação
    entre declarado e recalculado que permite distinguir inconsistência interna
    de divergência entre documentos.
    """

    from_index: int
    to_index: int
    azimuth: Measured | None = None
    distance: Measured | None = None
    confrontant: TextValue | None = None
    # Lado curvo. Documentos de loteamento descrevem a curva por raio e
    # desenvolvimento ("R6,00-D8,24"), com o ângulo central no lugar do interno.
    # O desenvolvimento é o comprimento do arco, e confrontá-lo com a distância
    # entre os dois vértices — que é a corda — reprovaria um lado correto.
    arc_radius: Measured | None = None
    arc_development: Measured | None = None
    central_angle: Measured | None = None

    @property
    def is_arc(self) -> bool:
        return self.arc_radius is not None and self.arc_development is not None

    def expected_chord(self) -> float | None:
        """Corda do arco: ``2·R·sen(AC/2)``."""
        if self.arc_radius is None or self.central_angle is None:
            return None
        return 2.0 * self.arc_radius.value * math.sin(
            math.radians(self.central_angle.value) / 2.0
        )

    def expected_development(self) -> float | None:
        if self.arc_radius is None or self.central_angle is None:
            return None
        return self.arc_radius.value * math.radians(self.central_angle.value)


@dataclass
class Parcel:
    """Imóvel descrito por um documento, já normalizado."""

    label: str
    crs: CRSSpec | None = None
    vertices: list[Vertex] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    area: Measured | None = None
    perimeter: Measured | None = None
    matricula: TextValue | None = None
    # CPF/CNPJ citados no documento (capa, quadro, legenda). Value = só dígitos.
    tax_ids: list[TextValue] = field(default_factory=list)
    # Outras citações de área/perímetro no texto, distintas da escolhida — para
    # confrontar a tabela/cálculo com o texto descritivo do mesmo memorial.
    area_citations: list[Measured] = field(default_factory=list)
    perimeter_citations: list[Measured] = field(default_factory=list)
    average_height_m: float = 0.0
    # SIGEF publica azimute e distância *planos* (de grade); memoriais antigos e
    # cadernetas de campo publicam distância horizontal do terreno. A diferença
    # é o fator combinado de escala, cerca de 0,4 m/km — tratar uma pela outra
    # reprova todos os segmentos de um documento correto, então a convenção é
    # declarada pelo template de extração em vez de adivinhada.
    distances_are_ground: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def has_projected_ring(self) -> bool:
        return len(self.vertices) >= 3 and all(v.has_projected for v in self.vertices)

    def ring(self) -> list[Point]:
        return [v.point() for v in self.vertices]

    def code_index(self) -> dict[str, int]:
        return {v.code: i for i, v in enumerate(self.vertices)}

    def segment_label(self, segment: Segment) -> str:
        origin = self.vertices[segment.from_index].code
        target = self.vertices[segment.to_index].code
        return f"{origin}→{target}"
