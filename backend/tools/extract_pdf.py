"""Roda extração e comparação sobre dois PDFs, sem banco e sem servidor.

É a ferramenta de diagnóstico de primeira linha: quando um documento real sai
errado na aplicação, aqui se vê em qual estágio a leitura se perdeu, sem ter de
subir a pilha inteira. Documentos de cliente não podem ser versionados, então
esta conferência não vira teste automatizado.

    python tools/extract_pdf.py "..\\A.pdf" "..\\B.pdf" [filtro]

O filtro opcional restringe os achados exibidos a um tipo (``inter_document``,
``internal``, ``low_confidence``…).
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.compare import compare_parcels
from app.core.numbers import format_br
from app.core.tolerance import PROFILES
from app.extraction.pipeline import extract_document

LIMIT = 60


def show(result, role: str) -> None:
    print(f"\n{'=' * 70}\nDOCUMENTO {role}: {Path(result.path).name}\n{'=' * 70}")
    for stage in result.stages:
        print(f"  [{'ok' if stage.ok else 'FALHOU'}] {stage.stage}: {stage.message}")

    if result.profile:
        for page in result.profile.pages:
            print(
                f"    p.{page.number}: {page.classification} / {page.relevance} "
                f"({page.char_count} chars, {page.table_candidates} tab.)"
            )

    for warning in result.warnings:
        print(f"  aviso: {warning}")
    for error in result.errors:
        print(f"  ERRO: {error}")

    parcel = result.parcel
    if parcel is None:
        print("  (nenhuma parcela construída)")
        return

    print(f"\n  CRS: {parcel.crs}  |  anel projetado: {parcel.has_projected_ring}")
    print(f"  Vértices: {len(parcel.vertices)}  Segmentos: {len(parcel.segments)}")
    if parcel.area:
        print(f"  Área declarada: {format_br(parcel.area.value, 4)} m²")
    if parcel.perimeter:
        print(f"  Perímetro declarado: {format_br(parcel.perimeter.value, 4)} m")
    if parcel.matricula:
        print(f"  Matrícula: {parcel.matricula.value}")

    for vertex in parcel.vertices[:8]:
        east = vertex.easting.value if vertex.easting else None
        north = vertex.northing.value if vertex.northing else None
        page = vertex.easting.provenance.page if vertex.easting else "?"
        print(f"    {vertex.code:>10}  E={east!s:>14}  N={north!s:>15}  (p.{page})")
    if len(parcel.vertices) > 8:
        print(f"    ... mais {len(parcel.vertices) - 8} vértice(s)")


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        return 2

    results = []
    for role, argument in zip("AB", sys.argv[1:3], strict=True):
        result = extract_document(
            argument, document_id=role, label=role, ocr_enabled=False
        )
        show(result, role)
        results.append(result)

    if not all(item.parcel for item in results):
        print("\nSem as duas parcelas não há o que comparar.")
        return 1

    comparison = compare_parcels(
        results[0].parcel, results[1].parcel, PROFILES["padrao"]
    )
    print(f"\n{'=' * 70}\nCOMPARAÇÃO\n{'=' * 70}")
    print(f"  Resumo: {comparison.summary}")
    if comparison.match:
        print(
            f"  Pareamento: {comparison.match.method}, "
            f"{len(comparison.match.pairs)} par(es)"
        )
        if comparison.match.systematic:
            print(f"  Sistemático: {comparison.match.systematic.message}")

    only = sys.argv[3] if len(sys.argv) > 3 else ""
    shown = [f for f in comparison.findings if not only or only in str(f.kind)]
    for finding in shown[:LIMIT]:
        print(
            f"  [{finding.severity}] {finding.kind} | "
            f"{finding.subject}: {finding.message}"
        )
    if len(shown) > LIMIT:
        print(f"  ... mais {len(shown) - LIMIT} achado(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
