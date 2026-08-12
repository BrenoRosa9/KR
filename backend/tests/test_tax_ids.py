"""Testes de CPF/CNPJ e citações de área no texto."""

from __future__ import annotations

from app.core.compare import FindingKind, compare_parcels
from app.core.tolerance import PROFILES
from app.extraction.context import extract_context
from app.extraction.tax_ids import extract_tax_ids, format_tax_id
from conftest import build_parcel

PROFILE = PROFILES["exato"]


class TestTaxIdExtraction:
    def test_extracts_cpf_and_cnpj(self):
        pages = {
            1: (
                "Proprietários: Ari João Lescovitz CPF: 081.972.379-72 e "
                "H4A49 Administradora CNPJ: 07.691.441/0001-26"
            ),
            2: "Também Klaus Franzner Sell, CPF nº 020.174.721-00",
        }
        values = extract_tax_ids(pages, "doc-a")
        digits = {v.value for v in values}
        assert "08197237972" in digits
        assert "02017472100" in digits
        assert "07691441000126" in digits
        assert format_tax_id("08197237972") == "081.972.379-72"

    def test_invalid_cpf_gets_lower_confidence(self):
        pages = {1: "CPF: 111.111.111-11"}
        values = extract_tax_ids(pages, "doc-a")
        assert values
        assert values[0].confidence < 0.8


class TestTaxIdComparison:
    def test_cpf_only_in_one_document_is_reported(self, parcel_a, square_ring):
        from app.core.schema import Provenance, SourceKind, TextValue

        parcel_b = build_parcel("B", square_ring, "doc-b")
        parcel_a.tax_ids = [
            TextValue(
                value="08197237972",
                provenance=Provenance(
                    document_id="doc-a",
                    page=1,
                    source_kind=SourceKind.TEXT_SPAN,
                    raw_text="081.972.379-72",
                ),
            )
        ]
        parcel_b.tax_ids = [
            TextValue(
                value="29330513972",
                provenance=Provenance(
                    document_id="doc-b",
                    page=1,
                    source_kind=SourceKind.TEXT_SPAN,
                    raw_text="293.305.139-72",
                ),
            )
        ]
        result = compare_parcels(parcel_a, parcel_b, PROFILE)
        cpf_findings = [
            f
            for f in result.findings
            if f.field in {"cpf", "cnpj"} and f.kind == FindingKind.INTER_DOCUMENT
        ]
        assert len(cpf_findings) >= 2
        messages = " ".join(f.message for f in cpf_findings)
        assert "081.972.379-72" in messages
        assert "293.305.139-72" in messages


class TestAreaCitations:
    def test_context_keeps_all_area_citations(self):
        pages = {
            1: "Área: 23.995,18 m² do núcleo. Perímetro: 1.105,60 m.",
            2: "Área da Rua Blumenau: 1.969,21 m². Área: 23.995,18 m².",
            3: "Área da via: 500,37 m².",
        }
        context = extract_context(pages, "doc-a", northing_hint=7_050_000.0)
        assert context.area is not None
        assert abs(context.area.value - 23995.18) < 0.01
        assert len(context.area_citations) >= 2
