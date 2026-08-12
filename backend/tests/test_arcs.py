"""Lados curvos.

Loteamentos urbanos têm esquinas arredondadas, e o documento as descreve por
raio e desenvolvimento ("R6,00-D8,24"), com o ângulo central no lugar do
interno. Tratar o desenvolvimento como se fosse a distância entre os vértices
reprova um lado que está correto — e, num loteamento, isso é quase todo canto
de quadra.
"""

from __future__ import annotations

import math

import pytest
from conftest import build_parcel, measured
from test_headers import build_table

from app.core.compare import FindingKind, Severity, compare_parcels
from app.core.schema import Measured, Parcel
from app.core.tolerance import PROFILES
from app.extraction.context import DocumentContext
from app.extraction.headers import map_header_row
from app.extraction.vertices import build_parcel_from_table

RADIUS = 6.0
CENTRAL_ANGLE = 90.0
CHORD = 2 * RADIUS * math.sin(math.radians(CENTRAL_ANGLE) / 2)  # 8,485 m
DEVELOPMENT = RADIUS * math.radians(CENTRAL_ANGLE)  # 9,425 m


def parcel_with_arc(
    development: float = DEVELOPMENT,
    radius: float = RADIUS,
    central_angle: float | None = CENTRAL_ANGLE,
) -> Parcel:
    """Quadrado cujo primeiro lado é declarado como curva."""
    parcel = build_parcel("A", _ring(), document_id="doc-a")
    segment = parcel.segments[0]
    segment.distance = None
    segment.arc_radius = measured(radius, 2, "m", "doc-a", 0, 4)
    segment.arc_development = measured(development, 2, "m", "doc-a", 0, 4)
    segment.central_angle = (
        None
        if central_angle is None
        else measured(central_angle, 4, "°", "doc-a", 0, 5)
    )
    return parcel


def _ring():
    """Anel em que o primeiro lado tem exatamente o comprimento da corda."""
    base_e, base_n = 333_000.0, 7_394_000.0
    return [
        (base_e, base_n),
        (base_e + CHORD, base_n),
        (base_e + CHORD, base_n + 60.0),
        (base_e, base_n + 60.0),
    ]


class TestArcParsing:
    GRID = [
        ["Vértice", "Coordenada E", "Coordenada N", "Ângulo", "Distância"],
        ["P-01", "333.000,000", "7.394.000,000", "AC 90°00'00\"", "R6,00-D9,42"],
        ["P-02", "333.008,485", "7.394.000,000", "178°00'00\"", "60,00"],
        ["P-03", "333.008,485", "7.394.060,000", "90°00'00\"", "8,49"],
        ["P-04", "333.000,000", "7.394.060,000", "90°00'00\"", "60,00"],
    ]

    def build(self):
        table = build_table(self.GRID)
        mapping = map_header_row(table.text_grid())
        assert mapping is not None
        return build_parcel_from_table(
            table=table,
            mapping=mapping,
            context=DocumentContext(),
            document_id="doc-a",
            label="A",
        )

    def test_radius_and_development_are_read(self):
        segment = self.build().parcel.segments[0]
        assert segment.is_arc
        assert segment.arc_radius.value == pytest.approx(6.0)
        assert segment.arc_development.value == pytest.approx(9.42)

    def test_development_is_not_taken_as_the_distance(self):
        # A distância entre os vértices é a corda; o desenvolvimento é o arco.
        assert self.build().parcel.segments[0].distance is None

    def test_central_angle_is_read_from_the_angle_column(self):
        segment = self.build().parcel.segments[0]
        assert segment.central_angle is not None
        assert segment.central_angle.value == pytest.approx(90.0)

    def test_straight_sides_are_unaffected(self):
        segments = self.build().parcel.segments
        assert not segments[1].is_arc
        assert segments[1].distance.value == pytest.approx(60.0)

    def test_curves_are_announced_for_review(self):
        result = self.build()
        assert any("curva" in warning for warning in result.warnings)


class TestArcChecks:
    def test_consistent_curve_produces_no_distance_finding(self):
        parcel = parcel_with_arc()
        comparison = compare_parcels(parcel, parcel_with_arc(), PROFILES["padrao"])

        distance_findings = [
            finding
            for finding in comparison.findings
            if finding.field == "distance" and finding.severity != Severity.INFO
        ]
        assert distance_findings == [], [f.message for f in distance_findings]

    def test_chord_that_disagrees_with_the_radius_is_caught(self):
        # Raio compatível com uma corda de 8,49 m, mas declarado como 4 m.
        parcel = parcel_with_arc(radius=4.0, development=DEVELOPMENT)
        comparison = compare_parcels(parcel, parcel_with_arc(), PROFILES["padrao"])

        internal = [
            finding
            for finding in comparison.findings
            if finding.kind == FindingKind.INTERNAL and "curvo" in finding.message
        ]
        assert internal
        assert "corda" in internal[0].message

    def test_development_inconsistent_with_radius_and_angle(self):
        parcel = parcel_with_arc(development=DEVELOPMENT + 3.0)
        comparison = compare_parcels(parcel, parcel_with_arc(), PROFILES["padrao"])

        internal = [
            finding
            for finding in comparison.findings
            if finding.kind == FindingKind.INTERNAL
            and "desenvolvimento" in finding.message
        ]
        assert internal

    def test_curve_without_central_angle_is_reported_but_not_reproved(self):
        parcel = parcel_with_arc(central_angle=None)
        comparison = compare_parcels(parcel, parcel, PROFILES["padrao"])

        notes = [
            finding
            for finding in comparison.findings
            if finding.severity == Severity.INFO and "lado curvo" in finding.message
        ]
        assert notes
        # Sem o ângulo central não há o que conferir: nada de reprovação.
        assert all(
            finding.kind != FindingKind.INTERNAL
            for finding in comparison.findings
            if "curvo" in finding.message
        )


class TestArcPersistence:
    """A curva precisa sobreviver ao banco.

    Depois da revisão humana o imóvel é reconstruído a partir das observações
    gravadas. Se o raio e o desenvolvimento não forem gravados, a recomparação
    volta a tratar a curva como reta e o laudo muda sozinho entre uma execução
    e outra.
    """

    @pytest.fixture
    def session(self):
        sqlalchemy = pytest.importorskip("sqlalchemy")
        from app.models import Base

        engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with sqlalchemy.orm.Session(engine) as session:
            yield session

    def test_round_trip_keeps_radius_and_development(self, session):
        import uuid

        from app.extraction.pipeline import ExtractionResult
        from app.repository import parcel_from_extraction, persist_extraction

        result = ExtractionResult(
            document_id="doc-a",
            path="memorial.pdf",
            context=DocumentContext(),
            parcel=parcel_with_arc(),
        )
        extraction = persist_extraction(
            session, uuid.uuid4(), uuid.uuid4(), "A", result
        )
        session.flush()

        reloaded = parcel_from_extraction(session, extraction)
        segment = reloaded.segments[0]
        assert segment.is_arc
        assert segment.arc_radius.value == pytest.approx(RADIUS)
        assert segment.arc_development.value == pytest.approx(DEVELOPMENT, abs=0.01)
        assert segment.central_angle.value == pytest.approx(CENTRAL_ANGLE)
        assert segment.distance is None


class TestArcGeometry:
    def test_chord_and_development_formulas(self):
        parcel = parcel_with_arc()
        segment = parcel.segments[0]
        assert segment.expected_chord() == pytest.approx(CHORD, abs=0.01)
        assert segment.expected_development() == pytest.approx(DEVELOPMENT, abs=0.01)

    def test_without_the_central_angle_nothing_is_predicted(self):
        segment = parcel_with_arc(central_angle=None).segments[0]
        assert segment.expected_chord() is None
        assert segment.expected_development() is None

    def test_measured_arc_keeps_provenance(self):
        segment = parcel_with_arc().segments[0]
        assert isinstance(segment.arc_radius, Measured)
        assert segment.arc_radius.provenance is not None
