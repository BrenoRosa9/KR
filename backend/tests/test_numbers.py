from app.core.numbers import detect_convention, format_br, parse_number


class TestParsing:
    def test_brazilian_format(self):
        parsed = parse_number("1.234,56")
        assert parsed is not None
        assert parsed.value == 1234.56
        assert parsed.decimals == 2

    def test_us_format(self):
        parsed = parse_number("1,234.56", convention="us")
        assert parsed is not None
        assert parsed.value == 1234.56
        assert parsed.decimals == 2

    def test_mixed_separators_resolve_by_position(self):
        # Não importa a convenção declarada: o separador decimal é o último.
        assert parse_number("7.394.512,345", convention="us").value == 7394512.345
        assert parse_number("7,394,512.345", convention="br").value == 7394512.345

    def test_space_as_thousands_separator(self):
        # Comum em PDF exportado de CAD.
        parsed = parse_number("7 394 512,345")
        assert parsed is not None
        assert parsed.value == 7394512.345

    def test_ambiguous_three_digits_follows_convention(self):
        assert parse_number("1,234", convention="br").value == 1.234
        assert parse_number("1,234", convention="us").value == 1234.0
        assert parse_number("1.234", convention="br").value == 1234.0
        assert parse_number("1.234", convention="us").value == 1.234

    def test_negative_and_typographic_minus(self):
        assert parse_number("-23,456").value == -23.456
        assert parse_number("\u221223,456").value == -23.456

    def test_value_embedded_in_text(self):
        parsed = parse_number("distância de 123,45 m")
        assert parsed is not None
        assert parsed.value == 123.45

    def test_no_number(self):
        assert parse_number("sem número") is None
        assert parse_number("") is None

    def test_decimals_drive_rounding_halfwidth(self):
        # A meia-largura é o que alimenta a tolerância efetiva do comparador.
        assert parse_number("100,00").rounding_halfwidth == 0.005
        assert parse_number("100,000").rounding_halfwidth == 0.0005


class TestConventionDetection:
    def test_single_decisive_sample_is_enough(self):
        assert detect_convention(["1.234,56", "789", "12"]) == "br"
        assert detect_convention(["1,234.56", "789"]) == "us"

    def test_short_decimal_decides(self):
        assert detect_convention(["0,5", "1,25"]) == "br"
        assert detect_convention(["0.5", "1.25"]) == "us"

    def test_repeated_separator_means_thousands(self):
        assert detect_convention(["7.394.512"]) == "br"
        assert detect_convention(["7,394,512"]) == "us"

    def test_genuinely_ambiguous_is_reported(self):
        # Só grupos de três dígitos: impossível decidir, e o sistema deve dizer
        # isso em vez de escolher por conta própria.
        assert detect_convention(["1,234", "5,678"]) == "ambiguous"
        assert detect_convention(["123", "456"]) == "ambiguous"

    def test_majority_wins_over_noise(self):
        samples = ["1.234,56", "2.345,67", "3,456"]
        assert detect_convention(samples) == "br"


def test_format_br_roundtrip():
    assert format_br(1234.5, 2) == "1.234,50"
    assert format_br(-7394512.345, 3) == "-7.394.512,345"
    assert parse_number(format_br(9876.54321, 4)).value == 9876.5432
