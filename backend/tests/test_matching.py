import math

import pytest

from app.core.matching import (
    MatchMethod,
    SystematicKind,
    align_cyclic,
    detect_systematic,
    estimate_similarity,
    match_by_code,
    match_geometric,
    match_vertices,
    normalize_code,
)

# Quadrilátero irregular: lados distintos são necessários para que o
# alinhamento cíclico tenha o que distinguir. Num quadrado perfeito toda
# rotação tem custo zero e o teste não provaria nada.
IRREGULAR = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 80.0)]


def rotate_ring(ring, by):
    return ring[by:] + ring[:by]


def rotate_about_centroid(ring, degrees):
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    rad = math.radians(degrees)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    return [
        (
            cx + (p[0] - cx) * cos_r - (p[1] - cy) * sin_r,
            cy + (p[0] - cx) * sin_r + (p[1] - cy) * cos_r,
        )
        for p in ring
    ]


def scale_about_centroid(ring, factor):
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    return [(cx + (p[0] - cx) * factor, cy + (p[1] - cy) * factor) for p in ring]


class TestCodeNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("P-01", "P1"),
            ("P 01", "P1"),
            ("P01", "P1"),
            ("p1", "P1"),
            ("V-0025", "V25"),
            ("BR-SP-M-0001", "BRSPM1"),
        ],
    )
    def test_normalization(self, raw: str, expected: str):
        assert normalize_code(raw) == expected

    def test_different_prefixes_stay_distinct(self):
        # Casar V1 com P1 por engano é pior do que cair na estratégia
        # geométrica: produz correspondência errada com aparência de certa.
        assert normalize_code("V-01") != normalize_code("P-01")


class TestMatchByCode:
    def test_exact_match(self):
        result = match_by_code(["P1", "P2", "P3"], ["P-01", "P-02", "P-03"])
        assert result.method == MatchMethod.CODE
        assert [(p.index_a, p.index_b) for p in result.pairs] == [(0, 0), (1, 1), (2, 2)]
        assert not result.unmatched_a and not result.unmatched_b

    def test_out_of_order_codes(self):
        result = match_by_code(["P1", "P2"], ["P2", "P1"])
        assert [(p.index_a, p.index_b) for p in result.pairs] == [(0, 1), (1, 0)]

    def test_missing_code_is_reported(self):
        result = match_by_code(["P1", "P2", "P3"], ["P1", "P3"])
        assert result.unmatched_a == [1]
        assert len(result.pairs) == 2

    def test_duplicate_codes_are_refused(self):
        # Rótulo repetido torna o casamento ambíguo; melhor não casar.
        result = match_by_code(["P1", "P2"], ["P1", "P1"])
        assert result.pairs == [] or all(p.code_b != "P1" for p in result.pairs)
        assert result.notes


class TestGeometricMatch:
    def test_matches_despite_uniform_translation(self):
        """O caso do datum trocado.

        Dois documentos deslocados uniformemente em 64 m não casariam nenhum
        vértice por proximidade absoluta, mas a correspondência é óbvia.
        """
        shifted = [(x + 60.0, y - 22.0) for x, y in IRREGULAR]
        result = match_geometric(IRREGULAR, shifted, max_residual_m=1.0)
        assert len(result.pairs) == 4
        assert [(p.index_a, p.index_b) for p in result.pairs] == [
            (0, 0),
            (1, 1),
            (2, 2),
            (3, 3),
        ]

    def test_assignment_is_optimal_not_greedy(self):
        # Escolha gulosa erraria: o vértice 0 de A está mais perto do vértice 1
        # de B, mas a atribuição ótima global é a identidade.
        points_a = [(0.0, 0.0), (10.0, 0.0)]
        points_b = [(4.0, 0.0), (14.0, 0.0)]
        result = match_geometric(
            points_a, points_b, max_residual_m=1.0, remove_translation=True
        )
        assert [(p.index_a, p.index_b) for p in result.pairs] == [(0, 0), (1, 1)]

    def test_residual_above_limit_is_left_unmatched(self):
        points_b = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 200.0)]
        result = match_geometric(IRREGULAR, points_b, max_residual_m=2.0)
        assert len(result.pairs) < 4
        assert result.unmatched_a


class TestCyclicAlignment:
    def test_rotated_starting_vertex(self):
        """Mesmo polígono, vértice inicial diferente — caso comum na prática."""
        for offset in range(4):
            rotated = rotate_ring(IRREGULAR, offset)
            result = align_cyclic(IRREGULAR, rotated)
            mapping = {p.index_a: p.index_b for p in result.pairs}
            for index_a in range(4):
                assert IRREGULAR[index_a] == pytest.approx(
                    rotated[mapping[index_a]]
                ), f"offset {offset}, vértice {index_a}"

    def test_reversed_orientation(self):
        """Documento percorrendo o perímetro no sentido oposto."""
        reversed_ring = list(reversed(IRREGULAR))
        result = align_cyclic(IRREGULAR, reversed_ring)
        assert result.reversed_orientation
        mapping = {p.index_a: p.index_b for p in result.pairs}
        for index_a in range(4):
            assert IRREGULAR[index_a] == pytest.approx(reversed_ring[mapping[index_a]])

    def test_reversed_and_rotated(self):
        for offset in range(4):
            candidate = rotate_ring(list(reversed(IRREGULAR)), offset)
            result = align_cyclic(IRREGULAR, candidate)
            mapping = {p.index_a: p.index_b for p in result.pairs}
            for index_a in range(4):
                assert IRREGULAR[index_a] == pytest.approx(
                    candidate[mapping[index_a]]
                ), f"offset {offset}, vértice {index_a}"

    def test_different_vertex_count_is_refused(self):
        result = align_cyclic(IRREGULAR, IRREGULAR[:3])
        assert result.pairs == []
        assert result.notes


class TestSimilarity:
    def test_recovers_pure_translation(self):
        shifted = [(x + 10.0, y + 5.0) for x, y in IRREGULAR]
        tx, ty, rotation, scale, rms = estimate_similarity(IRREGULAR, shifted)
        assert (tx, ty) == pytest.approx((10.0, 5.0))
        assert rotation == pytest.approx(0.0, abs=1e-9) or rotation == pytest.approx(
            360.0, abs=1e-9
        )
        assert scale == pytest.approx(1.0)
        assert rms == pytest.approx(0.0, abs=1e-9)

    def test_recovers_rotation_and_scale(self):
        target = scale_about_centroid(rotate_about_centroid(IRREGULAR, 2.0), 1.5)
        _, _, rotation, scale, rms = estimate_similarity(IRREGULAR, target)
        assert rotation == pytest.approx(2.0, abs=1e-6)
        assert scale == pytest.approx(1.5, rel=1e-9)
        assert rms == pytest.approx(0.0, abs=1e-6)


class TestSystematicDetection:
    def test_datum_like_translation(self):
        """O achado que substitui N divergências idênticas por uma explicação."""
        shifted = [(x - 40.0, y + 50.0) for x, y in IRREGULAR]
        systematic = detect_systematic(IRREGULAR, shifted)
        assert systematic is not None
        assert systematic.kind == SystematicKind.TRANSLATION
        assert systematic.magnitude == pytest.approx(math.hypot(40.0, 50.0))
        assert systematic.residual_rms_m == pytest.approx(0.0, abs=1e-9)
        assert "datum" in systematic.message.lower()

    def test_rotation_from_north_reference(self):
        rotated = rotate_about_centroid(IRREGULAR, 0.05)
        systematic = detect_systematic(IRREGULAR, rotated)
        assert systematic is not None
        assert systematic.kind == SystematicKind.ROTATION
        assert systematic.rotation_deg == pytest.approx(0.05, abs=1e-6)
        assert "norte" in systematic.message.lower()

    def test_scale_from_grid_versus_ground(self):
        scaled = scale_about_centroid(IRREGULAR, 1.0004)
        systematic = detect_systematic(IRREGULAR, scaled)
        assert systematic is not None
        assert systematic.kind == SystematicKind.SCALE
        assert systematic.magnitude == pytest.approx(400.0, rel=1e-3)
        assert "escala" in systematic.message.lower()

    def test_single_bad_vertex_is_not_systematic(self):
        """O teste que impede o achado sistemático de mascarar erro real.

        Um vértice deslocado 5 m tem que aparecer como divergência individual,
        não ser diluído numa explicação global.
        """
        perturbed = list(IRREGULAR)
        perturbed[1] = (perturbed[1][0] + 5.0, perturbed[1][1])
        assert detect_systematic(IRREGULAR, perturbed) is None

    def test_identical_geometry_is_not_systematic(self):
        assert detect_systematic(IRREGULAR, IRREGULAR) is None

    def test_noise_below_threshold_is_not_systematic(self):
        jittered = [(x + 0.01, y - 0.01) for x, y in IRREGULAR]
        assert detect_systematic(jittered, IRREGULAR) is None


class TestCascade:
    def test_prefers_code_when_available(self):
        result = match_vertices(
            ["P1", "P2", "P3", "P4"],
            ["P1", "P2", "P3", "P4"],
            IRREGULAR,
            IRREGULAR,
        )
        assert result.method == MatchMethod.CODE
        assert len(result.pairs) == 4

    def test_falls_back_to_geometry_when_codes_disagree(self):
        result = match_vertices(
            ["P1", "P2", "P3", "P4"],
            ["V10", "V20", "V30", "V40"],
            IRREGULAR,
            [(x + 0.5, y) for x, y in IRREGULAR],
        )
        assert result.method in {MatchMethod.GEOMETRIC, MatchMethod.CYCLIC}
        assert len(result.pairs) == 4

    def test_falls_back_to_cyclic_when_geometry_is_unrelated(self):
        # Coordenadas em origens completamente diferentes e rótulos distintos:
        # só a forma do polígono permite alinhar.
        far_away = [(x + 100_000.0, y + 200_000.0) for x, y in rotate_ring(IRREGULAR, 2)]
        result = match_vertices(
            ["P1", "P2", "P3", "P4"],
            ["A", "B", "C", "D"],
            IRREGULAR,
            far_away,
            max_residual_m=1.0,
        )
        assert len(result.pairs) == 4
        mapping = {p.index_a: p.index_b for p in result.pairs}
        for index_a in range(4):
            assert IRREGULAR[index_a] == pytest.approx(
                (
                    far_away[mapping[index_a]][0] - 100_000.0,
                    far_away[mapping[index_a]][1] - 200_000.0,
                )
            )

    def test_attaches_residuals_to_pairs(self):
        shifted = [(x + 0.30, y) for x, y in IRREGULAR]
        codes = ["P1", "P2", "P3", "P4"]
        result = match_vertices(codes, list(codes), IRREGULAR, shifted)
        assert all(p.residual_m == pytest.approx(0.30) for p in result.pairs)

    def test_without_coordinates_stays_on_codes(self):
        result = match_vertices(["P1", "P2"], ["X1", "X2"], None, None)
        assert result.method == MatchMethod.CODE
        assert result.pairs == []
        assert any("geométrico" in note for note in result.notes)
