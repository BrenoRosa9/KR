"""Testes de extração ponta a ponta sobre PDFs gerados.

O que estes testes provam: o pipeline sai de um arquivo e chega a um
:class:`Parcel` normalizado, com procedência em cada valor, sem intervenção
humana e sem nenhuma chamada a modelo de linguagem. O que eles *não* provam é
cobertura sobre o acervo real — isso só a amostra de documentos do cliente
resolve.
"""

from __future__ import annotations

import pytest
from pdf_fixtures import MemorialSpec, write_memorial, write_scanned_like

from app.core.compare import Severity, compare_parcels
from app.core.tolerance import PROFILES
from app.extraction.headers import map_header_row, match_header
from app.extraction.pipeline import extract_document
from app.extraction.tables import extract_tables
from app.extraction.text import collapse_spaced_letters, normalize_label
from app.extraction.triage import PageClass, profile_document

RING = [
    (333_000.000, 7_394_000.000),
    (333_120.500, 7_394_010.250),
    (333_135.750, 7_394_130.900),
    (333_010.250, 7_394_115.400),
]


@pytest.fixture
def memorial(tmp_path):
    spec = MemorialSpec(ring=RING)
    return write_memorial(tmp_path / "memorial.pdf", spec)


class TestTextNormalization:
    def test_accents_and_punctuation_are_flattened(self):
        assert normalize_label("Vért.") == "vert"
        assert normalize_label("COORDENADA  N (m)") == "coordenada n m"
        assert normalize_label("Distância") == "distancia"

    def test_spaced_letters_from_cad_are_rejoined(self):
        assert collapse_spaced_letters("V É R T I C E") == "VÉRTICE"
        assert collapse_spaced_letters("Vértice") == "Vértice"
        # Duas palavras normais não devem ser coladas.
        assert collapse_spaced_letters("Coordenada N") == "Coordenada N"


class TestHeaderMapping:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("Vértice", "vertex_code"),
            ("Vért.", "vertex_code"),
            ("Estaca", "vertex_code"),
            ("Coordenada N (m)", "northing"),
            ("Norte", "northing"),
            ("Coordenada E (m)", "easting"),
            ("Leste", "easting"),
            ("Azimute Plano", "azimuth"),
            ("Distância (m)", "distance"),
            ("Confrontante", "confrontant"),
            ("Latitude", "latitude"),
        ],
    )
    def test_known_variants(self, label, expected):
        assert match_header(label) == expected

    def test_grouping_label_is_not_a_field(self):
        # "COORDENADAS" é agrupador de duas colunas, não um campo.
        assert match_header("Coordenadas") is None
        assert match_header("Coordenadas UTM") is None

    def test_unknown_header_returns_none(self):
        # É exatamente aqui que um modelo de linguagem ganharia utilidade.
        assert match_header("Refª topográfica interna") is None

    def test_header_row_is_found_below_a_title(self):
        grid = [
            ["MEMORIAL DESCRITIVO", "", "", ""],
            ["", "", "", ""],
            ["Vértice", "Coordenada N", "Coordenada E", "Distância"],
            ["P-01", "7394000,000", "333000,000", "120,94"],
        ]
        mapping = map_header_row(grid)
        assert mapping is not None
        assert mapping.header_row == 2
        assert mapping.has_coordinates


class TestTriage:
    def test_digital_page_is_classified_as_digital(self, memorial):
        profile = profile_document(memorial)
        assert profile.page_count == 1
        assert profile.pages[0].classification == PageClass.DIGITAL
        assert not profile.needs_ocr
        assert profile.pages[0].char_count > 100

    def test_page_without_text_is_not_treated_as_digital(self, tmp_path):
        path = write_scanned_like(tmp_path / "scan.pdf", MemorialSpec(ring=RING))
        profile = profile_document(path)
        assert profile.pages[0].classification in {
            PageClass.SCANNED,
            PageClass.VECTOR_CAD,
        }
        assert profile.pages[0].char_count < 20

    def test_data_pages_exclude_nothing_in_a_single_page_document(self, memorial):
        profile = profile_document(memorial)
        assert profile.data_pages() == [1]


class TestTableExtraction:
    def test_cells_carry_bounding_boxes(self, memorial):
        import pdfplumber

        with pdfplumber.open(memorial) as pdf:
            tables = extract_tables(pdf.pages[0], 1)

        assert tables
        table = max(tables, key=lambda candidate: candidate.row_count)
        with_bbox = [cell for cell in table.cells if cell.bbox is not None]
        # Sem bbox não existe tela de revisão nem laudo rastreável.
        assert len(with_bbox) / len(table.cells) > 0.8

    def test_finds_the_vertex_table(self, memorial):
        import pdfplumber

        with pdfplumber.open(memorial) as pdf:
            tables = extract_tables(pdf.pages[0], 1)

        joined = " ".join(cell.text for table in tables for cell in table.cells)
        assert "P-01" in joined
        assert "7.394.000,000" in joined


class TestPipeline:
    def test_extracts_a_complete_parcel(self, memorial):
        result = extract_document(memorial, document_id="doc-a", label="A")

        assert result.ok, result.errors
        parcel = result.parcel
        assert parcel is not None
        assert len(parcel.vertices) == 4
        assert [v.code for v in parcel.vertices] == ["P-01", "P-02", "P-03", "P-04"]
        assert parcel.has_projected_ring

    def test_coordinates_are_parsed_in_brazilian_format(self, memorial):
        result = extract_document(memorial, document_id="doc-a")
        vertex = result.parcel.vertices[0]
        assert vertex.northing.value == pytest.approx(7_394_000.0, abs=0.001)
        assert vertex.easting.value == pytest.approx(333_000.0, abs=0.001)
        # Três casas na origem viram meia-largura de 0,0005 m na tolerância.
        assert vertex.easting.halfwidth == pytest.approx(0.0005)

    def test_crs_is_resolved_from_the_text(self, memorial):
        result = extract_document(memorial, document_id="doc-a")
        assert result.context.crs is not None
        assert result.context.crs.epsg == "EPSG:31983"
        assert result.context.crs.utm_zone == 23
        assert result.context.crs.hemisphere == "S"

    def test_area_perimeter_and_matricula_come_from_the_text(self, memorial):
        result = extract_document(memorial, document_id="doc-a")
        parcel = result.parcel
        assert parcel.area is not None
        assert parcel.perimeter is not None
        assert parcel.matricula is not None
        assert parcel.matricula.value == "12.345"

    def test_totals_row_is_not_read_as_a_vertex(self, memorial):
        result = extract_document(memorial, document_id="doc-a")
        assert all(v.code != "TOTAL" for v in result.parcel.vertices)
        assert len(result.parcel.vertices) == 4

    def test_every_value_has_provenance(self, memorial):
        result = extract_document(memorial, document_id="doc-a")
        for vertex in result.parcel.vertices:
            for measured in (vertex.easting, vertex.northing):
                assert measured.provenance is not None
                assert measured.provenance.page == 1
                assert measured.provenance.bbox is not None
                assert measured.provenance.raw_text

    def test_azimuths_and_distances_become_segments(self, memorial):
        result = extract_document(memorial, document_id="doc-a")
        parcel = result.parcel
        assert len(parcel.segments) == 4
        assert all(segment.azimuth is not None for segment in parcel.segments)
        assert all(segment.distance is not None for segment in parcel.segments)
        assert all(segment.confrontant is not None for segment in parcel.segments)

    def test_stage_log_records_the_pipeline(self, memorial):
        result = extract_document(memorial, document_id="doc-a")
        stages = {log.stage: log for log in result.stages}
        assert stages["triage"].ok
        assert stages["tables"].ok
        assert stages["parcel"].ok
        assert "não necessário" in stages["ocr"].message

    def test_missing_file_fails_cleanly(self, tmp_path):
        result = extract_document(tmp_path / "inexistente.pdf", document_id="x")
        assert not result.ok
        assert result.errors


class TestLayoutVariants:
    def test_table_without_drawn_grid(self, tmp_path):
        """Tabela alinhada apenas por posição, sem nenhuma linha desenhada."""
        path = write_memorial(
            tmp_path / "sem_grade.pdf", MemorialSpec(ring=RING, draw_grid=False)
        )
        result = extract_document(path, document_id="doc-a")
        assert result.ok, result.errors
        assert len(result.parcel.vertices) == 4

    def test_two_row_header(self, tmp_path):
        """`COORDENADAS` acima de `N | E`: só o par de linhas identifica campos."""
        path = write_memorial(
            tmp_path / "duas_linhas.pdf", MemorialSpec(ring=RING, two_row_header=True)
        )
        result = extract_document(path, document_id="doc-a")
        assert result.ok, result.errors
        assert len(result.parcel.vertices) == 4
        assert result.parcel.vertices[0].easting is not None

    def test_table_split_across_pages_is_stitched(self, tmp_path):
        """Tabela grande quebrada em duas páginas, sem repetir o cabeçalho.

        É o caso que mais compromete o resultado quando falha: metade dos
        vértices some, o polígono fecha em outro lugar e a área sai errada sem
        que nada denuncie o problema.
        """
        ring = [
            (333_000.0 + 30.0 * index, 7_394_000.0 + 18.0 * (index % 5))
            for index in range(12)
        ]
        path = write_memorial(
            tmp_path / "duas_paginas.pdf",
            MemorialSpec(ring=ring, rows_per_page=7, include_totals=False),
        )

        result = extract_document(path, document_id="doc-a")
        assert result.ok, result.errors
        assert len(result.parcel.vertices) == 12
        assert any("continua nas páginas" in w for w in result.warnings)

    def test_stitched_rows_keep_their_own_page_in_the_provenance(self, tmp_path):
        ring = [
            (333_000.0 + 30.0 * index, 7_394_000.0 + 18.0 * (index % 5))
            for index in range(12)
        ]
        path = write_memorial(
            tmp_path / "duas_paginas.pdf",
            MemorialSpec(ring=ring, rows_per_page=7, include_totals=False),
        )

        result = extract_document(path, document_id="doc-a")
        pages = {
            vertex.easting.provenance.page for vertex in result.parcel.vertices
        }
        # Sem isso o destaque na revisão apontaria a página errada.
        assert pages == {1, 2}

    def test_unrelated_table_on_a_later_page_is_not_stitched(self, tmp_path):
        """Duas parcelas distintas, cada uma com a sua tabela, no mesmo arquivo."""
        import pypdfium2

        first = write_memorial(tmp_path / "p1.pdf", MemorialSpec(ring=RING))
        second_ring = [(x + 4_000.0, y + 4_000.0) for x, y in RING]
        second = write_memorial(tmp_path / "p2.pdf", MemorialSpec(ring=second_ring))

        merged = pypdfium2.PdfDocument.new()
        for part in (first, second):
            merged.import_pages(pypdfium2.PdfDocument(str(part)))
        target = tmp_path / "duas_parcelas.pdf"
        merged.save(str(target))

        result = extract_document(target, document_id="doc-a")
        assert result.ok, result.errors
        # Emendar as duas produziria um octógono que não existe em lugar nenhum.
        assert len(result.parcel.vertices) == 4

    def test_document_without_datum_leaves_crs_unresolved(self, tmp_path):
        path = write_memorial(
            tmp_path / "sem_datum.pdf",
            MemorialSpec(ring=RING, datum="Datum local da gleba", zone_text=""),
        )
        result = extract_document(path, document_id="doc-a")
        # Regra do projeto: sem datum reconhecido, nada é assumido.
        assert result.context.crs is None
        assert any("Datum" in warning for warning in result.warnings)


class TestEndToEndComparison:
    def test_identical_documents_produce_no_errors(self, tmp_path):
        """O caminho completo: dois PDFs entram, zero divergência sai."""
        spec = MemorialSpec(ring=RING)
        path_a = write_memorial(tmp_path / "a.pdf", spec)
        path_b = write_memorial(tmp_path / "b.pdf", spec)

        result_a = extract_document(path_a, document_id="doc-a", label="A")
        result_b = extract_document(path_b, document_id="doc-b", label="B")
        assert result_a.ok and result_b.ok

        comparison = compare_parcels(
            result_a.parcel, result_b.parcel, PROFILES["padrao"]
        )
        blocking = [f for f in comparison.findings if f.severity == Severity.ERROR]
        assert blocking == [], [f.message for f in blocking]

    def test_moved_vertex_is_detected_from_the_pdfs(self, tmp_path):
        moved = list(RING)
        moved[2] = (moved[2][0] + 0.85, moved[2][1] - 0.40)

        path_a = write_memorial(tmp_path / "a.pdf", MemorialSpec(ring=RING))
        path_b = write_memorial(tmp_path / "b.pdf", MemorialSpec(ring=moved))

        result_a = extract_document(path_a, document_id="doc-a", label="A")
        result_b = extract_document(path_b, document_id="doc-b", label="B")
        comparison = compare_parcels(
            result_a.parcel, result_b.parcel, PROFILES["padrao"]
        )

        coordinate_errors = [
            f
            for f in comparison.findings
            if f.severity == Severity.ERROR and f.field in {"easting", "northing"}
        ]
        assert coordinate_errors
        assert any("P-03" in f.subject for f in coordinate_errors)
        # E a procedência tem que apontar as duas células de origem.
        assert coordinate_errors[0].provenance_a is not None
        assert coordinate_errors[0].provenance_b is not None

    def test_wrong_declared_distance_is_internal_not_inter_document(self, tmp_path):
        """Distância impressa errada em A, coordenadas iguais nos dois."""
        distances = MemorialSpec(ring=RING)
        broken = MemorialSpec(ring=RING, distance_overrides={0: 999.99})

        path_a = write_memorial(tmp_path / "a.pdf", broken)
        path_b = write_memorial(tmp_path / "b.pdf", distances)

        result_a = extract_document(path_a, document_id="doc-a", label="A")
        result_b = extract_document(path_b, document_id="doc-b", label="B")
        comparison = compare_parcels(
            result_a.parcel, result_b.parcel, PROFILES["padrao"]
        )

        internal_a = [
            f
            for f in comparison.findings
            if f.kind == "internal" and f.scope == "A" and f.field == "distance"
        ]
        assert internal_a
        assert "inconsistente consigo mesmo" in internal_a[0].message

    def test_datum_shift_between_documents_is_reported_once(self, tmp_path):
        shifted_ring = [(x + 61.3, y - 24.7) for x, y in RING]
        path_a = write_memorial(tmp_path / "a.pdf", MemorialSpec(ring=RING))
        path_b = write_memorial(tmp_path / "b.pdf", MemorialSpec(ring=shifted_ring))

        result_a = extract_document(path_a, document_id="doc-a", label="A")
        result_b = extract_document(path_b, document_id="doc-b", label="B")
        comparison = compare_parcels(
            result_a.parcel, result_b.parcel, PROFILES["padrao"]
        )

        systematic = [
            f
            for f in comparison.findings
            if f.kind == "systematic" and f.severity == Severity.ERROR
        ]
        assert len(systematic) == 1
        assert "datum" in systematic[0].message.lower()
