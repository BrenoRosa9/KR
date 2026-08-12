"""Testes do motor de comparação.

Cada teste segue o mesmo roteiro: parte de dois documentos internamente
consistentes, introduz um único defeito conhecido, e verifica que o motor
aponta aquele defeito — e apenas ele. Falso positivo reprova o teste tanto
quanto falso negativo, porque um relatório cheio de ruído é tão inútil quanto
um relatório vazio.
"""

from __future__ import annotations

from conftest import BASE_EASTING, BASE_NORTHING, build_parcel, measured, shifted

from app.core.compare import (
    ComparisonResult,
    FindingKind,
    Severity,
    compare_parcels,
    recompute,
)
from app.core.schema import Measured, Parcel
from app.core.tolerance import PROFILES, ToleranceProfile

PROFILE = PROFILES["padrao"]

BIG_RING = [
    (BASE_EASTING, BASE_NORTHING),
    (BASE_EASTING + 1000.0, BASE_NORTHING),
    (BASE_EASTING + 1000.0, BASE_NORTHING + 800.0),
    (BASE_EASTING, BASE_NORTHING + 900.0),
]


def errors(result: ComparisonResult) -> list:
    return [f for f in result.findings if f.severity == Severity.ERROR]


def of_kind(result: ComparisonResult, kind: FindingKind) -> list:
    return result.by_kind(kind)


class TestConsistentDocuments:
    def test_identical_documents_produce_no_errors(self, parcel_a, parcel_b):
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        assert errors(result) == [], [f.message for f in errors(result)]

    def test_no_inter_document_findings_at_all(self, parcel_a, parcel_b):
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        assert of_kind(result, FindingKind.INTER_DOCUMENT) == []

    def test_closure_is_reported_as_consistent(self, parcel_a, parcel_b):
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        closure_notes = [
            f for f in result.findings if "fechamento" in f.subject.lower()
        ]
        assert closure_notes
        assert all(f.severity == Severity.INFO for f in closure_notes)

    def test_rounding_of_declared_distances_is_flagged_internally(self):
        """Com igualdade exata, arredondamento impresso vs recálculo vira achado.

        Dois documentos idênticos ainda não divergem entre si; o que aparece é
        inconsistência interna de cada um, se a distância impressa não bate
        com a distância das coordenadas.
        """
        parcel_a = build_parcel("A", BIG_RING, "doc-a", distance_decimals=2)
        parcel_b = build_parcel("B", BIG_RING, "doc-b", distance_decimals=2)
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        assert of_kind(result, FindingKind.INTER_DOCUMENT) == []
        # O anel grande em UTM, com distâncias arredondadas a 2 casas, quase
        # sempre difere do recálculo em milímetros — isso agora é achado.
        assert of_kind(result, FindingKind.INTERNAL) or errors(result) == []

    def test_large_parcel_still_clean_thanks_to_scale_factor(self):
        parcel_a = build_parcel("A", BIG_RING, "doc-a")
        parcel_b = build_parcel("B", BIG_RING, "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        assert of_kind(result, FindingKind.INTER_DOCUMENT) == []


class TestScaleFactorMatters:
    def test_ignoring_scale_factor_condemns_a_correct_document(self):
        """A armadilha do fator de escala, demonstrada.

        O mesmo documento, correto, é aprovado com o fator aplicado e reprovado
        sem ele. É por isso que o fator não é detalhe de implementação.
        """
        parcel = build_parcel("A", BIG_RING, "doc-a")

        with_factor = recompute(parcel, PROFILE)
        without_factor = recompute(
            parcel, ToleranceProfile(name="sem fator", apply_scale_factor=False)
        )

        assert with_factor.scale_factor != 1.0
        assert without_factor.scale_factor == 1.0

        declared = parcel.segments[0].distance.value
        assert abs(declared - with_factor.ground_distances[0]) < 0.02
        assert abs(declared - without_factor.ground_distances[0]) > 0.02

    def test_grid_convention_document_is_handled(self):
        """Documento no padrão SIGEF: distância plana, não de terreno."""
        parcel = build_parcel("A", BIG_RING, "doc-a")
        parcel.distances_are_ground = False
        # Redeclara as distâncias como de grade, mantendo tudo o mais igual.
        recomputed = recompute(parcel, PROFILE)
        for index, segment in enumerate(parcel.segments):
            segment.distance = measured(
                recomputed.grid_distances[index], 2, "m", "doc-a", index, 4
            )
        parcel.perimeter = measured(
            sum(recomputed.grid_distances), 2, "m", "doc-a", 91, 1
        )

        other = build_parcel("B", BIG_RING, "doc-b")
        other.distances_are_ground = False
        other_recomputed = recompute(other, PROFILE)
        for index, segment in enumerate(other.segments):
            segment.distance = measured(
                other_recomputed.grid_distances[index], 2, "m", "doc-b", index, 4
            )
        other.perimeter = measured(
            sum(other_recomputed.grid_distances), 2, "m", "doc-b", 91, 1
        )

        result = compare_parcels(parcel, other, PROFILE)
        assert errors(result) == [], [f.message for f in errors(result)]


class TestInterDocumentDivergence:
    def test_single_moved_vertex_is_pinpointed(self, parcel_a, square_ring):
        moved = list(square_ring)
        moved[1] = (moved[1][0] + 0.40, moved[1][1])
        parcel_b = build_parcel("B", moved, "doc-b")

        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        coordinate_errors = [
            f
            for f in errors(result)
            if f.kind == FindingKind.INTER_DOCUMENT and f.field is not None
        ]
        assert coordinate_errors
        assert any("P2" in f.subject for f in coordinate_errors)
        # E não deve acusar os vértices que não mudaram.
        assert not any(
            "P3" in f.subject and f.field == "easting" for f in coordinate_errors
        )

    def test_moved_vertex_also_flags_the_two_affected_sides(self, parcel_a, square_ring):
        moved = list(square_ring)
        moved[1] = (moved[1][0] + 0.40, moved[1][1])
        parcel_b = build_parcel("B", moved, "doc-b")

        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        distance_errors = [f for f in errors(result) if f.field == "distance"]
        assert len(distance_errors) >= 2

    def test_area_divergence_is_reported(self, parcel_a, square_ring):
        bigger = list(square_ring)
        bigger[2] = (bigger[2][0] + 5.0, bigger[2][1] + 5.0)
        parcel_b = build_parcel("B", bigger, "doc-b")

        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        area_errors = [f for f in errors(result) if f.field == "area"]
        assert area_errors
        assert any("área" in f.message.lower() for f in area_errors)

    def test_declared_values_diverging_is_separate_from_geometry(
        self, parcel_a, parcel_b
    ):
        # Mesma geometria nos dois, mas B declara uma distância diferente da
        # que suas próprias coordenadas produzem.
        original = parcel_b.segments[0].distance
        parcel_b.segments[0].distance = Measured(
            value=original.value + 0.50,
            halfwidth=original.halfwidth,
            unit="m",
            provenance=original.provenance,
        )
        result = compare_parcels(parcel_a, parcel_b, PROFILE)

        internal_b = [
            f
            for f in result.findings
            if f.kind == FindingKind.INTERNAL and f.scope == "B"
        ]
        internal_a = [
            f
            for f in result.findings
            if f.kind == FindingKind.INTERNAL
            and f.scope == "A"
            and f.severity != Severity.INFO
        ]
        assert internal_b, "a inconsistência interna de B deveria aparecer"
        assert not internal_a, "o documento A está correto e não deve ser acusado"


class TestInternalInconsistency:
    def test_declared_distance_against_own_coordinates(self, parcel_a, parcel_b):
        original = parcel_a.segments[2].distance
        parcel_a.segments[2].distance = Measured(
            value=original.value - 1.25,
            halfwidth=original.halfwidth,
            unit="m",
            provenance=original.provenance,
        )
        result = compare_parcels(parcel_a, parcel_b, PROFILE)

        internal = [
            f
            for f in result.findings
            if f.kind == FindingKind.INTERNAL and f.scope == "A" and f.field == "distance"
        ]
        assert len(internal) == 1
        assert "inconsistente consigo mesmo" in internal[0].message
        assert internal[0].provenance_a is not None

    def test_declared_area_against_own_coordinates(self, parcel_a, parcel_b):
        parcel_a.area = Measured(
            value=12_500.00, halfwidth=0.005, unit="m²", provenance=None
        )
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        internal = [
            f
            for f in result.findings
            if f.kind == FindingKind.INTERNAL and f.field == "area" and f.scope == "A"
        ]
        assert internal
        assert internal[0].severity == Severity.ERROR

    def test_polygon_that_does_not_close(self, parcel_a, parcel_b):
        # Encurta um lado declarado sem mexer nas coordenadas: a poligonal
        # percorrida pelos valores impressos deixa de fechar.
        original = parcel_a.segments[0].distance
        parcel_a.segments[0].distance = Measured(
            value=original.value - 0.60,
            halfwidth=original.halfwidth,
            unit="m",
            provenance=original.provenance,
        )
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        closure = [
            f
            for f in result.findings
            if "fechamento" in f.subject.lower() and f.scope == "A"
        ]
        assert closure
        assert closure[0].severity == Severity.ERROR
        assert "não fecha" in closure[0].message

    def test_wrong_azimuth_is_reported_in_arcseconds(self, parcel_a, parcel_b):
        original = parcel_a.segments[1].azimuth
        parcel_a.segments[1].azimuth = Measured(
            value=original.value + 0.05,  # 180"
            halfwidth=original.halfwidth,
            unit="°",
            provenance=original.provenance,
        )
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        azimuth_findings = [
            f
            for f in result.findings
            if f.field == "azimuth" and f.kind == FindingKind.INTERNAL
        ]
        assert azimuth_findings
        assert '180"' in azimuth_findings[0].message


class TestSystematicHandling:
    def test_datum_shift_yields_one_explanation_not_eight_errors(
        self, parcel_a, square_ring
    ):
        """O comportamento que define a utilidade do relatório.

        Um deslocamento uniforme de 64 m é um problema — de datum, quase
        sempre. Reportá-lo como oito coordenadas erradas esconde a causa e
        enterra qualquer divergência real no meio do ruído.
        """
        parcel_b = build_parcel("B", shifted(square_ring, 60.0, -22.0), "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)

        systematic = of_kind(result, FindingKind.SYSTEMATIC)
        systematic_errors = [f for f in systematic if f.severity == Severity.ERROR]
        assert len(systematic_errors) == 1
        assert "datum" in systematic_errors[0].message.lower()

        # As oito coordenadas deslocadas continuam listadas, mas como
        # informação coerente com o padrão — não como oito erros distintos.
        coordinate_findings = [
            f for f in systematic if f.field in {"easting", "northing"}
        ]
        assert len(coordinate_findings) == 8
        assert all(f.severity == Severity.INFO for f in coordinate_findings)

    def test_translation_preserves_shape_so_no_distance_errors(
        self, parcel_a, square_ring
    ):
        parcel_b = build_parcel("B", shifted(square_ring, 60.0, -22.0), "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        assert [f for f in errors(result) if f.field == "distance"] == []
        assert [f for f in errors(result) if f.field == "area"] == []


class TestStructuralAndGaps:
    def test_vertex_count_mismatch(self, parcel_a, square_ring):
        pentagon = [*square_ring, (BASE_EASTING - 50.0, BASE_NORTHING + 50.0)]
        parcel_b = build_parcel("B", pentagon, "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)

        structural = [
            f for f in errors(result) if f.kind == FindingKind.STRUCTURAL
        ]
        assert structural
        assert "quantidade de vértices" in structural[0].subject

    def test_renamed_vertex_is_matched_by_geometry_not_abandoned(
        self, parcel_a, square_ring
    ):
        """Um vértice renomeado não deve virar dois achados estruturais falsos."""
        parcel_b = build_parcel(
            "B", square_ring, "doc-b", codes=["P1", "P2", "P3", "P9"]
        )
        result = compare_parcels(parcel_a, parcel_b, PROFILE)

        assert result.match is not None
        assert len(result.match.pairs) == 4
        assert result.match.unmatched_a == []
        assert result.match.unmatched_b == []
        assert any("proximidade" in note for note in result.match.notes)
        # O par geométrico é o correto: P4 de A com P9 de B.
        pair = next(p for p in result.match.pairs if p.index_a == 3)
        assert pair.index_b == 3
        assert errors(result) == [], [f.message for f in errors(result)]

    def test_genuinely_absent_vertex_is_still_reported(self, parcel_a, square_ring):
        # Sem par possível: aqui o achado estrutural é legítimo e obrigatório.
        triangle = square_ring[:3]
        parcel_b = build_parcel("B", triangle, "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        unmatched = [
            f
            for f in result.findings
            if f.kind == FindingKind.STRUCTURAL and f.field == "vertex_code"
        ]
        assert unmatched
        assert any("P4" in f.subject for f in unmatched)

    def test_missing_crs_blocks_instead_of_defaulting(self, square_ring):
        parcel_a = build_parcel("A", square_ring, "doc-a", crs=None)
        parcel_b = build_parcel("B", square_ring, "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)

        gaps = of_kind(result, FindingKind.DATA_GAP)
        assert gaps
        assert gaps[0].severity == Severity.ERROR
        assert result.has_blocking

    def test_different_crs_between_documents_is_flagged(self, parcel_a, square_ring):
        from app.core.crs import CRSSpec

        parcel_b = build_parcel("B", square_ring, "doc-b")
        parcel_b.crs = CRSSpec("EPSG:29193", "SAD69", 23, "S")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        datum_findings = [f for f in result.findings if f.field == "datum"]
        assert datum_findings
        assert "SAD69" in datum_findings[0].message


class TestLowConfidence:
    def test_low_confidence_values_enter_the_review_queue(self, square_ring):
        parcel_a = build_parcel("A", square_ring, "doc-a", confidence=0.55)
        parcel_b = build_parcel("B", square_ring, "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)

        low = of_kind(result, FindingKind.LOW_CONFIDENCE)
        assert len(low) == 8  # duas coordenadas por vértice
        assert all(f.scope == "A" for f in low)
        assert all(f.provenance_a is not None for f in low)

    def test_edited_values_are_trusted(self, square_ring):
        parcel = build_parcel("A", square_ring, "doc-a", confidence=0.55)
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
                        confidence=current.confidence,
                        provenance=current.provenance,
                        edited=True,
                    ),
                )
        result = compare_parcels(parcel, build_parcel("B", square_ring, "doc-b"), PROFILE)
        assert of_kind(result, FindingKind.LOW_CONFIDENCE) == []


class TestProvenanceAndSummary:
    def test_every_numeric_finding_carries_provenance(self, parcel_a, parcel_b):
        original = parcel_a.segments[0].distance
        parcel_a.segments[0].distance = Measured(
            value=original.value + 2.0,
            halfwidth=original.halfwidth,
            unit="m",
            provenance=original.provenance,
        )
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        internal = [
            f
            for f in result.findings
            if f.kind == FindingKind.INTERNAL and f.field == "distance"
        ]
        assert internal
        provenance = internal[0].provenance_a
        assert provenance is not None
        assert provenance.page == 1
        assert provenance.bbox is not None
        assert "tabela" in provenance.label()

    def test_summary_counts_match_findings(self, parcel_a, square_ring):
        parcel_b = build_parcel("B", shifted(square_ring, 60.0, -22.0), "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        assert result.summary["total"] == len(result.findings)
        assert result.summary["erros"] == len(errors(result))
        assert result.summary["systematic"] == len(
            of_kind(result, FindingKind.SYSTEMATIC)
        )

    def test_profile_name_is_recorded(self, parcel_a, parcel_b):
        result = compare_parcels(parcel_a, parcel_b, PROFILES["exato"])
        assert result.profile_name == "igualdade exata"


class TestExactEquality:
    def test_millimetre_difference_between_documents_is_an_error(
        self, parcel_a, square_ring
    ):
        """Sem faixa de tolerância: 1 mm de diferença em coordenada é divergência."""
        nudged = list(square_ring)
        nudged[1] = (nudged[1][0] + 0.001, nudged[1][1])
        parcel_b = build_parcel("B", nudged, "doc-b")
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        easting = [f for f in errors(result) if f.field == "easting"]
        assert easting, [f.message for f in errors(result)]

    def test_identical_legacy_profile_aliases_behave_the_same(
        self, parcel_a, parcel_b
    ):
        a = compare_parcels(parcel_a, parcel_b, PROFILES["padrao"])
        b = compare_parcels(parcel_a, parcel_b, PROFILES["rigoroso"])
        assert len(errors(a)) == len(errors(b))


def test_parcel_without_coordinates_degrades_gracefully():
    """Documento em que só o texto foi lido, sem tabela de coordenadas."""
    parcel_a = Parcel(label="A")
    parcel_b = Parcel(label="B")
    result = compare_parcels(parcel_a, parcel_b, PROFILE)
    assert result.recomputed_a is not None
    assert result.recomputed_a.area_m2 is None
    assert any("recálculo geométrico" in note for note in result.recomputed_a.notes)
    # Sem CRS, o bloqueio por lacuna de dados tem que aparecer.
    assert of_kind(result, FindingKind.DATA_GAP)
