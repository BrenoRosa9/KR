import math

import pytest

from app.core.angles import (
    angular_difference,
    azimuth_to_rumo,
    normalize_azimuth,
    parse_angle,
    rumo_to_azimuth,
    to_dms_string,
)


class TestDMS:
    def test_basic_dms(self):
        parsed = parse_angle("23°45'12,34\"")
        assert parsed is not None
        assert parsed.fmt == "dms"
        expected = 23 + 45 / 60 + 12.34 / 3600
        assert parsed.degrees == pytest.approx(expected, abs=1e-9)

    def test_hemisphere_suffix_makes_it_negative(self):
        south = parse_angle("23°45'12,34\"S")
        north = parse_angle("23°45'12,34\"N")
        assert south.degrees == pytest.approx(-north.degrees)

    def test_oeste_in_portuguese(self):
        # Documentos brasileiros escrevem "O" de Oeste onde o inglês usa "W".
        assert parse_angle("46°38'10\"O").degrees < 0
        assert parse_angle("46°38'10\"W").degrees < 0

    def test_ordinal_indicator_variant(self):
        # PDFs de CAD frequentemente usam º (U+00BA) em vez de ° (U+00B0).
        assert parse_angle("90º00'00\"").degrees == pytest.approx(90.0)

    def test_missing_seconds_yields_coarse_precision(self):
        parsed = parse_angle("23°45'")
        assert parsed.degrees == pytest.approx(23.75)
        assert parsed.arcsec_precision == 60.0

    def test_seconds_decimals_drive_precision(self):
        assert parse_angle("1°00'00\"").arcsec_precision == 1.0
        assert parse_angle("1°00'00,00\"").arcsec_precision == pytest.approx(0.01)


class TestDecimalDegrees:
    def test_plain_decimal(self):
        parsed = parse_angle("123,456789")
        assert parsed.fmt == "decimal"
        assert parsed.degrees == pytest.approx(123.456789)

    def test_precision_converted_to_arcseconds(self):
        # 6 casas em grau decimal equivalem a 0,0036" de precisão.
        assert parse_angle("123,456789").arcsec_precision == pytest.approx(0.0036)

    def test_pair_of_numbers_is_not_read_as_dms(self):
        # Sem símbolo de grau, "12 34" não deve ser interpretado como 12°34'.
        parsed = parse_angle("12 34")
        assert parsed.fmt == "decimal"


class TestRumo:
    def test_all_four_quadrants(self):
        assert rumo_to_azimuth(45.0, "NE") == pytest.approx(45.0)
        assert rumo_to_azimuth(45.0, "SE") == pytest.approx(135.0)
        assert rumo_to_azimuth(45.0, "SW") == pytest.approx(225.0)
        assert rumo_to_azimuth(45.0, "NW") == pytest.approx(315.0)

    def test_roundtrip(self):
        for azimuth in (0.0, 30.0, 95.5, 180.0, 260.25, 350.0):
            angle, quadrant = azimuth_to_rumo(azimuth)
            assert rumo_to_azimuth(angle, quadrant) == pytest.approx(azimuth)

    def test_parse_rumo_string(self):
        parsed = parse_angle("N 45°30'00\" E")
        assert parsed.fmt == "rumo"
        assert parsed.degrees == pytest.approx(45.5)

    def test_parse_rumo_southwest(self):
        parsed = parse_angle("S 30°00'00\" W")
        assert parsed.degrees == pytest.approx(210.0)

    def test_rumo_with_leste_in_portuguese(self):
        parsed = parse_angle("N 45°00'00\" L")
        assert parsed.degrees == pytest.approx(45.0)


class TestAzimuthArithmetic:
    def test_normalize(self):
        assert normalize_azimuth(370.0) == pytest.approx(10.0)
        assert normalize_azimuth(-10.0) == pytest.approx(350.0)

    def test_difference_wraps_around_north(self):
        # O erro clássico: 359,9 - 0,1 dá 359,8 em subtração direta.
        assert angular_difference(359.9, 0.1) == pytest.approx(-0.2)
        assert angular_difference(0.1, 359.9) == pytest.approx(0.2)

    def test_difference_is_signed_and_bounded(self):
        assert angular_difference(10.0, 350.0) == pytest.approx(20.0)
        assert abs(angular_difference(0.0, 180.0)) == pytest.approx(180.0)


class TestFormatting:
    def test_dms_string(self):
        assert to_dms_string(23.7534277778) == "23°45'12,34\""

    def test_rounding_overflow_does_not_produce_sixty(self):
        # 59,999" arredondado a 2 casas transborda para o minuto seguinte.
        text = to_dms_string(1.0 - 0.0001 / 3600, seconds_decimals=2)
        assert "60,00" not in text
        assert text == "1°00'00,00\""

    def test_negative_sign_preserved(self):
        assert to_dms_string(-23.5).startswith("-23°30'")

    def test_matches_math_radians(self):
        from app.core.angles import radians

        assert radians(180.0) == pytest.approx(math.pi)
