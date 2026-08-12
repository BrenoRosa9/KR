"""Leitura do contexto: fuso, área, perímetro e matrícula no texto corrido.

Os casos vêm de documentos reais. O padrão que os une é a ambiguidade: uma
prancha cita a área de meio quarteirão e a matrícula de todos os confrontantes,
e escolher o primeiro número que aparece é sorteio disfarçado de extração.
"""

from __future__ import annotations

from app.extraction.context import DISPUTED_CONFIDENCE, extract_context


def context_of(text: str, **kwargs):
    return extract_context({1: text}, document_id="doc", **kwargs)


class TestZone:
    def test_declared_zone_is_used_directly(self):
        context = context_of("SIRGAS 2000, Fuso 23 S, projeção UTM.")
        assert context.utm_zone == 23

    def test_central_meridian_is_converted_to_zone(self):
        # MC-51°W é o meridiano central do fuso 22, e não o fuso 51.
        context = context_of("SIRGAS 2000, MC-51°W", northing_hint=7_054_658.0)
        assert context.utm_zone == 22
        assert context.crs is not None
        assert context.crs.epsg == "EPSG:31982"

    def test_zone_alone_does_not_produce_a_projected_crs(self):
        # Sem hemisfério não há EPSG projetado, e inventar um seria pior que
        # devolver o geográfico e pedir confirmação.
        context = context_of("SIRGAS 2000, MC-51°W")
        assert context.utm_zone == 22
        assert context.crs is not None
        assert context.crs.epsg == "EPSG:4674"

    def test_number_from_another_legend_does_not_become_the_meridian(self):
        # O carimbo do CAD é lido em ordem de coluna e intercala legendas.
        context = context_of("SIRGAS 2000 MERIDIANO CENTRAL 5,88 51° WGR")
        assert context.utm_zone == 22

    def test_value_that_is_not_a_valid_central_meridian_is_refused(self):
        # Meridiano central de fuso UTM é múltiplo ímpar de 3°.
        context = context_of("SIRGAS 2000 MERIDIANO CENTRAL 50° W")
        assert context.utm_zone is None
        assert any("Fuso UTM não localizado" in w for w in context.warnings)


class TestArea:
    def test_single_area_is_taken_with_full_confidence(self):
        context = context_of("Área: 23.995,18 m²")
        assert context.area is not None
        assert context.area.value == 23_995.18
        assert context.area.confidence == 1.0

    def test_repeated_value_wins_over_a_stray_one(self):
        context = context_of(
            "Área: 23.995,18 m². Consta ainda área de 500,37 m² do lote vizinho. "
            "A área de 23.995,18 m² foi confirmada em campo."
        )
        assert context.area is not None
        assert context.area.value == 23_995.18

    def test_dispute_lowers_confidence_and_is_reported(self):
        context = context_of("Área: 500,37 m² e área: 791,12 m²")
        assert context.area is not None
        assert context.area.confidence == DISPUTED_CONFIDENCE
        assert any("mais de uma área" in warning for warning in context.warnings)

    def test_hectares_are_converted(self):
        context = context_of("Área: 2,5 ha")
        assert context.area is not None
        assert context.area.value == 25_000.0


class TestMatricula:
    def test_the_only_matricula_is_not_treated_as_doubtful(self):
        context = context_of("Imóvel matrícula nº 12.345 do Registro de Imóveis.")
        assert context.matricula is not None
        assert context.matricula.value == "12.345"
        assert context.matricula.confidence == 1.0

    def test_confrontant_matriculas_do_not_win_by_repetition(self):
        text = (
            "DESCRIÇÃO DA ÁREA ENCONTRADA - Matrícula n° 16.448, L°2. "
            "Segue confrontando com o imóvel matriculado sob o n° 36.393, L°2, "
            "depois confrontando com a matrícula n° 36.393 e novamente "
            "confrontando com a matrícula n° 36.393."
        )
        context = context_of(text)
        assert context.matricula is not None
        # A do imóvel aparece uma vez; a do vizinho, três.
        assert context.matricula.value == "16.448"

    def test_without_a_distinguishing_context_the_value_goes_to_review(self):
        text = "Matrícula n° 1.985 e matrícula n° 3.849 constam da planta."
        context = context_of(text)
        assert context.matricula is not None
        assert context.matricula.confidence == DISPUTED_CONFIDENCE
        assert any("Confirme na revisão" in warning for warning in context.warnings)
