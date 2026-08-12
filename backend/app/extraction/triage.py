"""Triagem: que tipo de página é esta e onde vale procurar dados.

Rodar OCR em documento que já tem camada de texto desperdiça minutos por página
e *piora* a qualidade. Rodar extração de texto em página escaneada devolve
vazio silenciosamente. Por isso a primeira coisa que o pipeline faz é
classificar cada página, e todo estágio seguinte consulta essa classificação.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pdfplumber

# Limiares empíricos. Não são universais: quando o acervo real estiver
# disponível, calibrar contra ele é uma das primeiras tarefas do projeto.
MIN_CHARS_FOR_TEXT_LAYER = 20
HYBRID_CHAR_CEILING = 120
VECTOR_DENSITY_THRESHOLD = 400
LARGE_IMAGE_COVERAGE = 0.55


class PageClass(StrEnum):
    DIGITAL = "digital"
    SCANNED = "scanned"
    VECTOR_CAD = "vector_cad"
    HYBRID = "hybrid"
    EMPTY = "empty"


class Relevance(StrEnum):
    """Se vale a pena extrair dados desta página."""

    LIKELY_DATA = "likely_data"
    LIKELY_DRAWING = "likely_drawing"
    UNKNOWN = "unknown"


@dataclass
class PageProfile:
    number: int
    width: float
    height: float
    char_count: int
    word_count: int
    image_count: int
    image_coverage: float
    vector_count: int
    table_candidates: int
    classification: PageClass
    relevance: Relevance
    needs_ocr: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class DocumentProfile:
    path: str
    page_count: int
    pages: list[PageProfile] = field(default_factory=list)
    producer: str | None = None
    encrypted: bool = False

    @property
    def needs_ocr(self) -> bool:
        return any(page.needs_ocr for page in self.pages)

    @property
    def dominant_class(self) -> PageClass:
        if not self.pages:
            return PageClass.EMPTY
        counts: dict[PageClass, int] = {}
        for page in self.pages:
            counts[page.classification] = counts.get(page.classification, 0) + 1
        return max(counts, key=lambda key: counts[key])

    def data_pages(self) -> list[int]:
        """Páginas em que vale procurar tabela de vértices."""
        return [
            page.number
            for page in self.pages
            if page.relevance != Relevance.LIKELY_DRAWING
            and page.classification != PageClass.EMPTY
        ]


def profile_document(path: str | Path) -> DocumentProfile:
    """Classifica cada página sem renderizar nada — rápido mesmo em PDF pesado."""
    path = Path(path)
    with pdfplumber.open(path) as pdf:
        metadata = pdf.metadata or {}
        profile = DocumentProfile(
            path=str(path),
            page_count=len(pdf.pages),
            producer=metadata.get("Producer"),
        )
        for index, page in enumerate(pdf.pages, start=1):
            profile.pages.append(_profile_page(page, index))
    return profile


def _profile_page(page, number: int) -> PageProfile:
    width = float(page.width or 0.0)
    height = float(page.height or 0.0)
    page_area = width * height or 1.0

    chars = page.chars
    images = page.images
    vector_count = len(page.lines) + len(page.curves) + len(page.rects)
    image_coverage = min(
        1.0,
        sum(
            abs(float(image.get("x1", 0)) - float(image.get("x0", 0)))
            * abs(float(image.get("bottom", 0)) - float(image.get("top", 0)))
            for image in images
        )
        / page_area,
    )

    notes: list[str] = []
    classification = _classify(
        char_count=len(chars),
        image_count=len(images),
        image_coverage=image_coverage,
        vector_count=vector_count,
        notes=notes,
    )

    # `find_tables` é barato comparado a `extract_table`; serve como indício de
    # onde procurar sem pagar o custo da extração completa.
    try:
        table_candidates = len(page.find_tables())
    except Exception:  # pragma: no cover - páginas malformadas
        table_candidates = 0
        notes.append("Detecção de tabelas falhou nesta página.")

    relevance = _relevance(classification, len(chars), vector_count, table_candidates)

    return PageProfile(
        number=number,
        width=width,
        height=height,
        char_count=len(chars),
        word_count=len(page.extract_words()) if chars else 0,
        image_count=len(images),
        image_coverage=image_coverage,
        vector_count=vector_count,
        table_candidates=table_candidates,
        classification=classification,
        relevance=relevance,
        needs_ocr=classification in {PageClass.SCANNED, PageClass.HYBRID},
        notes=notes,
    )


def _classify(
    char_count: int,
    image_count: int,
    image_coverage: float,
    vector_count: int,
    notes: list[str],
) -> PageClass:
    if char_count < MIN_CHARS_FOR_TEXT_LAYER:
        if image_coverage >= LARGE_IMAGE_COVERAGE or image_count > 0:
            notes.append("Sem camada de texto sobre imagem de página inteira: OCR.")
            return PageClass.SCANNED
        if vector_count >= VECTOR_DENSITY_THRESHOLD:
            notes.append(
                "Muitos vetores e nenhum texto: provável texto explodido em curvas "
                "por exportação de CAD. Só OCR sobre rasterização resolve, com "
                "qualidade baixa."
            )
            return PageClass.VECTOR_CAD
        return PageClass.EMPTY

    if char_count <= HYBRID_CHAR_CEILING and image_coverage >= LARGE_IMAGE_COVERAGE:
        notes.append(
            "Pouco texto sobre imagem grande: provável página escaneada com carimbo "
            "digital. OCR complementar."
        )
        return PageClass.HYBRID

    if vector_count >= VECTOR_DENSITY_THRESHOLD:
        notes.append(
            "Texto presente com alta densidade de vetores: planta ou tabela desenhada "
            "linha a linha. Extração de tabela precisa de reconstrução geométrica."
        )
        return PageClass.VECTOR_CAD

    return PageClass.DIGITAL


def _relevance(
    classification: PageClass,
    char_count: int,
    vector_count: int,
    table_candidates: int,
) -> Relevance:
    if classification == PageClass.EMPTY:
        return Relevance.UNKNOWN
    if table_candidates > 0 or char_count > 400:
        return Relevance.LIKELY_DATA
    # Muito desenho e pouco texto: quase certamente uma planta, onde não há
    # tabela para extrair.
    if vector_count > VECTOR_DENSITY_THRESHOLD and char_count < 400:
        return Relevance.LIKELY_DRAWING
    return Relevance.UNKNOWN
