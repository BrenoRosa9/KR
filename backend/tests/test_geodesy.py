import math

import pytest

from app.core.geodesy import (
    UTM_K0,
    combined_factor,
    elevation_factor,
    grid_azimuth,
    grid_distance,
    grid_scale_factor,
    grid_to_ground,
    ground_to_grid,
    interior_angles,
    is_counterclockwise,
    perimeter,
    polygon_area,
    ring_closure,
    segment_azimuths,
    segment_lengths,
    signed_area,
    traverse,
    traverse_closure,
)

SQUARE = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]


class TestBasics:
    def test_distance(self):
        assert grid_distance((0.0, 0.0), (3.0, 4.0)) == pytest.approx(5.0)

    def test_azimuth_cardinal_directions(self):
        origin = (0.0, 0.0)
        assert grid_azimuth(origin, (0.0, 1.0)) == pytest.approx(0.0)
        assert grid_azimuth(origin, (1.0, 0.0)) == pytest.approx(90.0)
        assert grid_azimuth(origin, (0.0, -1.0)) == pytest.approx(180.0)
        assert grid_azimuth(origin, (-1.0, 0.0)) == pytest.approx(270.0)

    def test_azimuth_is_clockwise_from_north(self):
        # A troca de argumentos em atan2 é o erro mais comum aqui: se estivesse
        # invertido, este azimute daria 45° mesmo assim, então testamos 30/60.
        assert grid_azimuth((0.0, 0.0), (1.0, math.sqrt(3.0))) == pytest.approx(30.0)
        assert grid_azimuth((0.0, 0.0), (math.sqrt(3.0), 1.0)) == pytest.approx(60.0)


class TestPolygon:
    def test_area_of_square(self):
        assert polygon_area(SQUARE) == pytest.approx(10_000.0)

    def test_signed_area_detects_orientation(self):
        assert signed_area(SQUARE) > 0
        assert is_counterclockwise(SQUARE)
        assert signed_area(list(reversed(SQUARE))) < 0

    def test_perimeter_closes_the_ring(self):
        assert perimeter(SQUARE) == pytest.approx(400.0)

    def test_segment_lengths_include_closing_side(self):
        lengths = segment_lengths(SQUARE)
        assert len(lengths) == 4
        assert all(length == pytest.approx(100.0) for length in lengths)

    def test_segment_azimuths(self):
        assert segment_azimuths(SQUARE) == pytest.approx([90.0, 0.0, 270.0, 180.0])

    def test_area_of_known_triangle(self):
        triangle = [(0.0, 0.0), (30.0, 0.0), (0.0, 40.0)]
        assert polygon_area(triangle) == pytest.approx(600.0)


class TestInteriorAngles:
    def test_square_has_right_angles(self):
        assert interior_angles(SQUARE) == pytest.approx([90.0] * 4)

    def test_orientation_does_not_change_interior_angles(self):
        # Anel anti-horário devolveria os ângulos externos se a orientação não
        # fosse levada em conta — 270° em vez de 90°.
        assert interior_angles(list(reversed(SQUARE))) == pytest.approx([90.0] * 4)

    def test_sum_matches_n_minus_two_times_180(self):
        pentagon = [
            (0.0, 0.0),
            (100.0, 0.0),
            (130.0, 80.0),
            (50.0, 130.0),
            (-30.0, 80.0),
        ]
        for ring in (pentagon, list(reversed(pentagon))):
            angles = interior_angles(ring)
            assert sum(angles) == pytest.approx((len(ring) - 2) * 180.0)

    def test_degenerate_ring_returns_empty(self):
        assert interior_angles([(0.0, 0.0), (1.0, 1.0)]) == []


class TestTraverseAndClosure:
    def test_traverse_reproduces_the_square(self):
        segments = list(
            zip(segment_azimuths(SQUARE), segment_lengths(SQUARE), strict=True)
        )
        points = traverse(SQUARE[0], segments)
        assert points[-1] == pytest.approx(SQUARE[0], abs=1e-9)

    def test_coordinates_always_close(self):
        closure = ring_closure(SQUARE)
        assert closure.linear_error == pytest.approx(0.0, abs=1e-9)
        assert closure.precision_denominator == math.inf

    def test_declared_values_may_not_close(self):
        # Um lado 0,30 m mais curto do que o declarado nas coordenadas: é
        # exatamente esse resíduo que denuncia inconsistência interna.
        segments = [(90.0, 100.0), (0.0, 100.0), (270.0, 99.7), (180.0, 100.0)]
        closure = traverse_closure(SQUARE[0], segments)
        assert closure.linear_error == pytest.approx(0.30, abs=1e-9)
        assert closure.precision_denominator == pytest.approx(399.7 / 0.30, rel=1e-6)

    def test_error_azimuth_points_in_the_right_direction(self):
        segments = [(0.0, 100.0), (90.0, 100.0), (180.0, 100.0), (270.0, 99.0)]
        closure = traverse_closure((0.0, 0.0), segments)
        assert closure.error_azimuth == pytest.approx(90.0)
        assert closure.linear_error == pytest.approx(1.0)


class TestScaleFactor:
    def test_k0_at_central_meridian(self):
        assert grid_scale_factor(500_000.0, -23.5) == pytest.approx(UTM_K0, abs=1e-12)

    def test_grows_away_from_central_meridian(self):
        near = grid_scale_factor(400_000.0, -23.5)
        far = grid_scale_factor(200_000.0, -23.5)
        assert UTM_K0 < near < far

    def test_matches_textbook_value_at_zone_edge(self):
        # O limite do fuso fica a 3° do meridiano central, cerca de 334 km no
        # equador, onde o fator de escala máximo tabelado da UTM é 1,00098.
        edge = grid_scale_factor(500_000.0 - 334_000.0, 0.0)
        assert edge == pytest.approx(1.00098, abs=5e-5)

    def test_symmetric_around_central_meridian(self):
        assert grid_scale_factor(400_000.0, -23.5) == pytest.approx(
            grid_scale_factor(600_000.0, -23.5)
        )

    def test_elevation_factor_reduces_distance(self):
        assert elevation_factor(0.0, -23.5) == pytest.approx(1.0)
        factor = elevation_factor(800.0, -23.5)
        assert factor < 1.0
        # 800 m de altitude equivalem a cerca de 125 ppm.
        assert (1.0 - factor) == pytest.approx(1.25e-4, rel=0.1)

    def test_grid_ground_roundtrip(self):
        factor = combined_factor(333_000.0, -23.55, height_m=750.0)
        ground = grid_to_ground(1000.0, factor)
        assert ground_to_grid(ground, factor) == pytest.approx(1000.0)

    def test_scale_factor_matters_at_kilometre_scale(self):
        """A armadilha que reprovaria todos os segmentos de um documento correto.

        Em São Paulo, 1 km de grade equivale a cerca de 1,0004 km no terreno.
        Ignorar isso produz divergência sistemática acima de qualquer tolerância
        razoável.
        """
        factor = combined_factor(333_000.0, -23.55)
        difference = abs(grid_to_ground(1000.0, factor) - 1000.0)
        assert difference > 0.02  # acima da tolerância padrão de distância
        assert difference < 1.0
