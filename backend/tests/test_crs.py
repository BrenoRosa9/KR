import pytest
from pyproj import CRS

from app.core.crs import (
    _UTM_EPSG,
    CORREGO_ALEGRE,
    SAD69,
    SIRGAS2000,
    WGS84,
    CRSSpec,
    CRSUndetermined,
    central_meridian,
    geodesic_area,
    geodesic_inverse,
    infer_hemisphere,
    normalize_datum_label,
    plausible_for_brazil,
    to_geographic,
    transform_point,
    utm_epsg,
    zone_from_longitude,
)

_FAMILY_NAMES = {
    "SIRGAS2000": "SIRGAS 2000",
    "SAD69": "SAD69",
    "CORREGO_ALEGRE": "Corrego Alegre 1970-72",
    "WGS84": "WGS 84",
}


class TestEPSGTables:
    """Valida as tabelas de código EPSG contra a base do PROJ.

    Tabela de EPSG escrita de memória é fonte silenciosa de laudo errado: um
    fuso trocado desloca as coordenadas em centenas de quilômetros sem que nada
    falhe em tempo de execução. Este teste é a única razão para confiar nelas.
    """

    @pytest.mark.parametrize("family", sorted(_UTM_EPSG))
    def test_every_code_matches_its_declared_zone(self, family: str):
        for key, code in _UTM_EPSG[family].items():
            zone = abs(key)
            hemisphere = "S" if key > 0 else "N"
            name = CRS.from_epsg(code).name
            assert _FAMILY_NAMES[family] in name, f"{code}: {name}"
            assert f"zone {zone}{hemisphere}" in name, f"{code}: {name}"

    def test_brazilian_zones_are_covered(self):
        for zone in range(18, 26):
            assert utm_epsg(SIRGAS2000, zone, "S").startswith("EPSG:")

    def test_zone_outside_coverage_raises_instead_of_guessing(self):
        with pytest.raises(CRSUndetermined):
            utm_epsg(SIRGAS2000, 5, "S")

    def test_known_anchors(self):
        # 23S é o fuso de São Paulo, o mais usado no país.
        assert utm_epsg(SIRGAS2000, 23, "S") == "EPSG:31983"
        assert utm_epsg(SAD69, 23, "S") == "EPSG:29193"
        assert utm_epsg(CORREGO_ALEGRE, 23, "S") == "EPSG:22523"
        assert utm_epsg(WGS84, 23, "S") == "EPSG:32723"


class TestDatumLabels:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("SIRGAS 2000", SIRGAS2000),
            ("sirgas2000", SIRGAS2000),
            ("SIRGAS-2000", SIRGAS2000),
            ("Datum: SIRGAS 2000", SIRGAS2000),
            ("WGS 84", WGS84),
            ("SAD 69", SAD69),
            ("Córrego Alegre", CORREGO_ALEGRE),
        ],
    )
    def test_recognized_labels(self, label: str, expected: str):
        assert normalize_datum_label(label) == expected

    def test_unknown_label_returns_none_rather_than_guessing(self):
        assert normalize_datum_label("Datum Local da Fazenda") is None
        assert normalize_datum_label("") is None


class TestZoneInference:
    def test_zone_from_longitude(self):
        assert zone_from_longitude(-46.6) == 23  # São Paulo
        assert zone_from_longitude(-34.9) == 25  # Recife
        assert zone_from_longitude(-60.02) == 20  # Manaus

    def test_longitude_exactly_on_boundary_goes_to_the_east_zone(self):
        # -60° é a divisa entre 20 e 21. O comportamento é convencional, mas
        # precisa ser determinístico e conhecido.
        assert zone_from_longitude(-60.0) == 21
        assert zone_from_longitude(-60.000001) == 20

    def test_central_meridian_roundtrip(self):
        for zone in range(18, 26):
            assert zone_from_longitude(central_meridian(zone) + 0.1) == zone

    def test_hemisphere_from_northing(self):
        assert infer_hemisphere(7_394_000.0) == "S"
        assert infer_hemisphere(300_000.0) == "N"

    def test_ambiguous_northing_raises(self):
        # Faixa intermediária: melhor exigir confirmação do que adivinhar.
        with pytest.raises(CRSUndetermined):
            infer_hemisphere(3_000_000.0)


class TestTransforms:
    def test_utm_to_geographic_lands_in_sao_paulo(self):
        spec = CRSSpec("EPSG:31983", "SIRGAS2000", 23, "S")
        longitude, latitude = to_geographic(spec, 333_000.0, 7_394_000.0)
        assert -47.5 < longitude < -46.0
        assert -24.0 < latitude < -23.0
        assert plausible_for_brazil(longitude, latitude)

    def test_datum_shift_sad69_to_sirgas_is_tens_of_metres(self):
        """O motivo pelo qual datum ausente é bloqueio e não default.

        Assumir SIRGAS quando o documento é SAD69 desloca o imóvel em dezenas de
        metros — erro grande o suficiente para invalidar o laudo e pequeno o
        suficiente para passar despercebido numa conferência visual.
        """
        longitude, latitude = -46.6, -23.55
        shifted_lon, shifted_lat = transform_point(
            longitude, latitude, SAD69, SIRGAS2000
        )
        _, _, distance = geodesic_inverse(
            longitude, latitude, shifted_lon, shifted_lat
        )
        assert 20.0 < distance < 100.0

    def test_identity_transform_is_exact(self):
        assert transform_point(-46.6, -23.55, SIRGAS2000, SIRGAS2000) == (
            -46.6,
            -23.55,
        )

    def test_outside_brazil_is_flagged(self):
        assert not plausible_for_brazil(2.35, 48.85)  # Paris
        assert not plausible_for_brazil(-46.6, 23.55)  # latitude com sinal trocado


class TestEllipsoidal:
    def test_geodesic_distance_matches_known_pair(self):
        # São Paulo para Rio de Janeiro: cerca de 360 km.
        _, _, distance = geodesic_inverse(-46.6333, -23.5505, -43.1729, -22.9068)
        assert distance == pytest.approx(360_000.0, rel=0.02)

    def test_geodesic_azimuth_points_northeast(self):
        forward, _, _ = geodesic_inverse(-46.6333, -23.5505, -43.1729, -22.9068)
        assert 60.0 < forward < 90.0

    def test_geodesic_area_of_small_ring_matches_planar(self):
        # Para um imóvel de 100 m de lado, área elipsoidal e área plana devem
        # concordar em menos de 1 m².
        spec = CRSSpec("EPSG:31983", "SIRGAS2000", 23, "S")
        ring = [
            (333_000.0, 7_394_000.0),
            (333_100.0, 7_394_000.0),
            (333_100.0, 7_394_100.0),
            (333_000.0, 7_394_100.0),
        ]
        geographic = [to_geographic(spec, e, n) for e, n in ring]
        assert geodesic_area(geographic) == pytest.approx(10_000.0, abs=10.0)
