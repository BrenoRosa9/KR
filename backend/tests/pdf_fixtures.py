"""Gera PDFs sintéticos no formato de memorial descritivo.

Não substituem o acervo real — nenhum teste sintético substitui — mas cobrem os
casos estruturais que sabemos existir: tabela com grade desenhada, tabela
alinhada só por posição, cabeçalho em duas linhas, rodapé de totais e valores em
formato brasileiro. Serve para provar que o pipeline liga de ponta a ponta antes
de haver documento de cliente disponível.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.core.angles import to_dms_string
from app.core.geodesy import (
    Point,
    combined_factor,
    grid_to_ground,
    polygon_area,
    segment_azimuths,
    segment_lengths,
)
from app.core.numbers import format_br

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 40.0
ROW_HEIGHT = 16.0

COLUMN_LAYOUT = [
    ("Vértice", 60.0),
    ("Coordenada N (m)", 100.0),
    ("Coordenada E (m)", 100.0),
    ("Azimute", 90.0),
    ("Distância (m)", 70.0),
    ("Confrontante", 95.0),
]


@dataclass
class MemorialSpec:
    """Descrição do documento a gerar."""

    ring: list[Point]
    codes: list[str] = field(default_factory=list)
    datum: str = "SIRGAS 2000"
    zone_text: str = "UTM 23S"
    matricula: str = "12.345"
    owner: str = "Fazenda Boa Vista"
    confrontantes: list[str] = field(default_factory=list)
    draw_grid: bool = True
    two_row_header: bool = False
    include_totals: bool = True
    coordinate_decimals: int = 3
    distance_decimals: int = 2
    latitude_for_scale: float = -23.55
    # Permite injetar defeitos: substitui a distância declarada de um lado.
    distance_overrides: dict[int, float] = field(default_factory=dict)
    declared_area_override: float | None = None
    # Quantas linhas de dados cabem por página. Zero mantém tudo em uma só.
    # Quando a tabela quebra, as páginas seguintes saem sem repetir o cabeçalho,
    # que é o caso difícil visto em memoriais de verdade.
    rows_per_page: int = 0

    def resolved_codes(self) -> list[str]:
        return self.codes or [f"P-{i + 1:02d}" for i in range(len(self.ring))]

    def resolved_confrontantes(self) -> list[str]:
        if self.confrontantes:
            return self.confrontantes
        return [f"Lote {chr(ord('A') + i)}" for i in range(len(self.ring))]


def ground_distances(spec: MemorialSpec) -> list[float]:
    mean_easting = sum(p[0] for p in spec.ring) / len(spec.ring)
    factor = combined_factor(mean_easting, spec.latitude_for_scale)
    return [grid_to_ground(d, factor) for d in segment_lengths(spec.ring)]


def write_memorial(path: str | Path, spec: MemorialSpec) -> Path:
    """Escreve o PDF e devolve o caminho."""
    path = Path(path)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle("Memorial Descritivo")

    header_rows, data_rows = _build_rows(spec)
    per_page = spec.rows_per_page or len(data_rows)
    chunks = [
        data_rows[start : start + per_page]
        for start in range(0, len(data_rows), per_page)
    ] or [[]]

    for index, chunk in enumerate(chunks):
        y = PAGE_HEIGHT - MARGIN
        if index == 0:
            y = _write_header(pdf, spec, y)
            y = _write_table(pdf, spec, header_rows, chunk, y - 10.0)
        else:
            # Continuação: sem cabeçalho, exatamente como o documento real.
            y = _write_table(pdf, spec, [], chunk, y)
        if index == len(chunks) - 1:
            _write_footer(pdf, spec, y - 20.0)
        pdf.showPage()

    pdf.save()
    return path


def _write_header(pdf: canvas.Canvas, spec: MemorialSpec, y: float) -> float:
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(MARGIN, y, "MEMORIAL DESCRITIVO")
    y -= 22.0

    pdf.setFont("Helvetica", 9)
    area = spec.declared_area_override
    if area is None:
        area = polygon_area(spec.ring)
    perimeter = sum(ground_distances(spec))

    lines = [
        f"Imóvel: {spec.owner}",
        f"Matrícula nº {spec.matricula} do Registro de Imóveis",
        f"Sistema Geodésico de Referência: {spec.datum}",
        f"Projeção: {spec.zone_text}",
        f"Área: {format_br(area, 2)} m²",
        f"Perímetro: {format_br(perimeter, spec.distance_decimals)} m",
    ]
    for line in lines:
        pdf.drawString(MARGIN, y, line)
        y -= 13.0
    return y


def _column_positions() -> list[float]:
    positions = [MARGIN]
    for _, width in COLUMN_LAYOUT:
        positions.append(positions[-1] + width)
    return positions


def _build_rows(spec: MemorialSpec) -> tuple[list[list[str]], list[list[str]]]:
    """Devolve as linhas de cabeçalho e as de dados, separadas."""
    codes = spec.resolved_codes()
    confrontantes = spec.resolved_confrontantes()
    azimuths = segment_azimuths(spec.ring)
    distances = ground_distances(spec)

    header: list[list[str]] = []
    if spec.two_row_header:
        # Cabeçalho agrupador em cima, campos embaixo — layout muito comum.
        header.append(["Vértice", "Coordenadas UTM", "", "Lado", "", "Confrontação"])
        header.append(["Código", "N (m)", "E (m)", "Azimute", "Distância (m)", "Limite"])
    else:
        header.append([label for label, _ in COLUMN_LAYOUT])

    data: list[list[str]] = []
    for index, point in enumerate(spec.ring):
        distance = spec.distance_overrides.get(index, distances[index])
        data.append(
            [
                codes[index],
                format_br(point[1], spec.coordinate_decimals),
                format_br(point[0], spec.coordinate_decimals),
                to_dms_string(azimuths[index], 2),
                format_br(distance, spec.distance_decimals),
                confrontantes[index],
            ]
        )

    if spec.include_totals:
        data.append(
            [
                "TOTAL",
                "",
                "",
                "",
                format_br(sum(distances), spec.distance_decimals),
                "",
            ]
        )

    return header, data


def _write_table(
    pdf: canvas.Canvas,
    spec: MemorialSpec,
    header: list[list[str]],
    data: list[list[str]],
    y: float,
) -> float:
    positions = _column_positions()
    rows = header + data

    table_top = y
    for row_index, row in enumerate(rows):
        is_header = row_index < len(header)
        pdf.setFont(
            "Helvetica-Bold" if is_header else "Helvetica",
            8 if is_header else 8.5,
        )
        for column_index, text in enumerate(row):
            if text:
                pdf.drawString(positions[column_index] + 3.0, y - 11.0, text)
        y -= ROW_HEIGHT

    if spec.draw_grid:
        _draw_grid(pdf, positions, table_top, y, len(rows))

    return y


def _draw_grid(
    pdf: canvas.Canvas,
    positions: list[float],
    top: float,
    bottom: float,
    row_count: int,
) -> None:
    pdf.setLineWidth(0.4)
    for index in range(row_count + 1):
        line_y = top - index * ROW_HEIGHT
        pdf.line(positions[0], line_y, positions[-1], line_y)
    for x in positions:
        pdf.line(x, top, x, bottom)


def _write_footer(pdf: canvas.Canvas, spec: MemorialSpec, y: float) -> None:
    pdf.setFont("Helvetica", 8)
    pdf.drawString(
        MARGIN,
        y,
        "Todas as coordenadas enunciadas neste memorial encontram-se representadas no "
        f"Sistema {spec.datum}.",
    )
    pdf.drawString(MARGIN, y - 12.0, "Responsável Técnico: Eng. Agrimensor - CREA 000000")


def write_scanned_like(path: str | Path, spec: MemorialSpec) -> Path:
    """PDF sem camada de texto útil, imitando um escaneado.

    Desenha a tabela apenas como vetores, sem nenhum texto: é o que a triagem
    deve classificar como página a ser rasterizada, não lida.
    """
    path = Path(path)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    positions = _column_positions()
    top = PAGE_HEIGHT - MARGIN - 100.0
    rows = len(spec.ring) + 2
    _draw_grid(pdf, positions, top, top - rows * ROW_HEIGHT, rows)
    # Ruído vetorial suficiente para parecer uma planta digitalizada.
    for index in range(500):
        offset = index * 0.7
        pdf.line(MARGIN, 60.0 + offset % 300.0, MARGIN + 200.0, 70.0 + offset % 280.0)
    pdf.showPage()
    pdf.save()
    return path
