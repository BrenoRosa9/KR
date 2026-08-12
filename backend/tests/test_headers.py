"""Cabeçalhos vistos em documentos reais.

Cada caso aqui saiu de um PDF de verdade que a extração errou antes: são os
layouts que a leitura ingênua não resolve, e a razão de existir do mapeamento.
"""

from __future__ import annotations

from app.core.schema import FieldKind
from app.extraction.headers import map_header_row, match_header
from app.extraction.pipeline import _map_with_two_row_fallback
from app.extraction.tables import Cell, ExtractedTable


def build_table(grid: list[list[str]]) -> ExtractedTable:
    cells = [
        Cell(text=text, bbox=(0.0, 0.0, 1.0, 1.0), row=row, column=column)
        for row, line in enumerate(grid)
        for column, text in enumerate(line)
    ]
    return ExtractedTable(
        page=1,
        index=0,
        cells=cells,
        row_count=len(grid),
        column_count=max(len(line) for line in grid),
    )


class TestFieldAliases:
    def test_utm_prefixed_coordinates(self):
        # "UTM E" e "UTM N" são o rótulo mais comum em memorial de REURB.
        assert match_header("UTM E") == FieldKind.EASTING
        assert match_header("UTM N") == FieldKind.NORTHING

    def test_de_and_para_name_the_ends_of_a_side(self):
        assert match_header("DE") == FieldKind.VERTEX_CODE
        # "PARA" não é código de vértice: é o destino, e a linha já é
        # identificada pela origem.
        assert match_header("PARA") is None

    def test_short_alias_does_not_match_by_prefix(self):
        # "de" casaria com meio documento se valesse como prefixo.
        assert match_header("Desenvolvimento") != FieldKind.VERTEX_CODE
        assert match_header("Descrição") != FieldKind.VERTEX_CODE


class TestMemorialHeader:
    HEADER = [
        "DE",
        "PARA",
        "LONGITUDE",
        "LATITUDE",
        "UTM E",
        "UTM N",
        "AZIMUTE",
        "ANG.INTERNO",
        "DIST.",
        "CONFRONTANTE",
    ]

    def test_all_relevant_columns_are_mapped(self):
        mapping = map_header_row([self.HEADER])
        assert mapping is not None
        assert mapping.has_coordinates
        assert mapping.columns[0] == FieldKind.VERTEX_CODE
        assert mapping.columns[4] == FieldKind.EASTING
        assert mapping.columns[5] == FieldKind.NORTHING
        assert mapping.columns[8] == FieldKind.DISTANCE
        assert mapping.columns[9] == FieldKind.CONFRONTANT

    def test_two_columns_of_vertex_are_not_a_pair_in_one_cell(self):
        mapping = map_header_row([self.HEADER])
        assert mapping is not None
        assert not mapping.code_is_pair


class TestPlantaHeader:
    """Cabeçalho de duas linhas de planta de CAD.

    A linha de baixo, sozinha, traz coordenadas — e por isso a implementação
    antiga parava nela, perdendo azimute, ângulo e distância, que só existem
    na linha de cima.
    """

    GRID = [
        ["DESCRIÇÃO ÁREA ENCONTRADA - Matrícula n° 16.448", "", "", "", "", "", "", ""],
        [
            "TABELA DE VÉRTICES, COORDENADAS, AZIMUTES E DISTÂNCIAS",
            *[""] * 7,
        ],
        [
            "LADOS",
            "COORDENADAS (GEOGRAFICAS)",
            "",
            "COORDENADAS (UTM)",
            "",
            "AZIMUTE",
            "ANGULO INTERNO",
            "DISTÂNCIA (metros)",
        ],
        [
            "Vértice Vértice",
            "Longitude",
            "Latitude",
            "E (metros)",
            "N (metros)",
            "",
            "",
            "",
        ],
        [
            "0PP 1",
            "49°24'16,1046\" O",
            "26°37'12,0750\" S",
            "658.841,425",
            "7.054.658,671",
            "125°36'15\"",
            "177°43'08\"",
            "20,00",
        ],
    ]

    def test_merged_header_keeps_azimuth_and_distance(self):
        mapping = _map_with_two_row_fallback(build_table(self.GRID))
        assert mapping is not None
        assert mapping.has_coordinates
        kinds = set(mapping.columns.values())
        assert FieldKind.AZIMUTH in kinds
        assert FieldKind.DISTANCE in kinds
        assert FieldKind.EASTING in kinds

    def test_data_starts_after_both_header_rows(self):
        mapping = _map_with_two_row_fallback(build_table(self.GRID))
        assert mapping is not None
        assert mapping.header_row == 3

    def test_doubled_label_marks_the_column_as_a_pair(self):
        mapping = _map_with_two_row_fallback(build_table(self.GRID))
        assert mapping is not None
        # "LADOS / Vértice Vértice": a célula traz origem e destino juntos.
        assert mapping.code_is_pair
