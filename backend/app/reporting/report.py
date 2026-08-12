"""Geração do laudo.

HTML é o formato de trabalho e o PDF é a entrega. A conversão usa WeasyPrint,
que depende de bibliotecas do sistema presentes na imagem Docker; quando elas
não existem, o relatório continua disponível em HTML em vez de a operação
falhar.

Todo laudo registra o critério de comparação (igualdade exata) e a lista de
correções humanas. Um laudo que não diz o que foi editado à mão não é
auditável.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.numbers import format_br
from ..models import Analysis, Extraction, Finding, Observation, User

TEMPLATE_DIR = Path(__file__).parent / "templates"

SEVERITY_LABELS = {"error": "Erro", "warning": "Atenção", "info": "Informativo"}
KIND_LABELS = {
    "systematic": "Padrão sistemático",
    "inter_document": "Divergência entre documentos",
    "internal": "Inconsistência interna",
    "structural": "Estrutura",
    "low_confidence": "Baixa confiança",
    "data_gap": "Lacuna de dados",
}
FIELD_LABELS = {
    "easting": "Coordenada E",
    "northing": "Coordenada N",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "azimuth": "Azimute",
    "distance": "Distância",
    "interior_angle": "Ângulo interno",
    "arc_radius": "Raio da curva",
    "arc_development": "Desenvolvimento da curva",
    "central_angle": "Ângulo central",
    "area": "Área",
    "perimeter": "Perímetro",
    "confrontant": "Confrontante",
    "matricula": "Matrícula",
    "cpf": "CPF",
    "cnpj": "CNPJ",
    "vertex_code": "Vértice",
    "datum": "Datum",
}


@dataclass
class ReportBundle:
    html: str
    pdf_bytes: bytes | None
    pdf_error: str | None = None


def _environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    environment.filters["br"] = lambda value, decimals=3: (
        format_br(float(value), decimals) if value is not None else "—"
    )
    return environment


def build_context(session: Session, analysis: Analysis) -> dict:
    findings = session.scalars(
        select(Finding)
        .where(Finding.analysis_id == analysis.id)
        .order_by(Finding.ordinal)
    ).all()
    extractions = session.scalars(
        select(Extraction)
        .where(Extraction.analysis_id == analysis.id)
        .order_by(Extraction.role)
    ).all()

    edits = []
    for extraction in extractions:
        edited = session.scalars(
            select(Observation).where(
                Observation.extraction_id == extraction.id,
                Observation.edited.is_(True),
            )
        ).all()
        for observation in edited:
            author = (
                session.get(User, observation.edited_by)
                if observation.edited_by
                else None
            )
            edits.append(
                {
                    "role": extraction.role,
                    "field": FIELD_LABELS.get(observation.field, observation.field),
                    "vertex_index": observation.vertex_index,
                    "original": observation.original_value_num
                    if observation.original_value_num is not None
                    else observation.original_value_text,
                    "current": observation.value_num
                    if observation.value_num is not None
                    else observation.value_text,
                    "raw_text": observation.raw_text,
                    "author": author.name if author else "—",
                    "at": observation.edited_at,
                }
            )

    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.kind, []).append(finding)

    return {
        "analysis": analysis,
        "generated_at": datetime.now(UTC),
        "findings": findings,
        "grouped": grouped,
        "extractions": {extraction.role: extraction for extraction in extractions},
        "edits": edits,
        "summary": analysis.summary or {},
        "match": analysis.match_summary or {},
        "profile": analysis.profile_snapshot or {},
        "severity_labels": SEVERITY_LABELS,
        "kind_labels": KIND_LABELS,
        "field_labels": FIELD_LABELS,
    }


def render_html(session: Session, analysis: Analysis) -> str:
    template = _environment().get_template("report.html")
    return template.render(**build_context(session, analysis))


def render_pdf(html: str, base_url: str | None = None) -> tuple[bytes | None, str | None]:
    """Converte o HTML em PDF, tolerando ausência das bibliotecas do sistema."""
    try:
        from weasyprint import HTML  # import tardio: depende de Pango/Cairo
    except Exception as exc:  # pragma: no cover - ambiente sem as libs
        return None, (
            "Geração de PDF indisponível neste ambiente "
            f"({type(exc).__name__}). O laudo permanece disponível em HTML."
        )

    try:
        return HTML(string=html, base_url=base_url).write_pdf(), None
    except Exception as exc:  # pragma: no cover
        return None, f"Falha ao gerar o PDF: {exc}"


def build_report(session: Session, analysis: Analysis) -> ReportBundle:
    html = render_html(session, analysis)
    pdf_bytes, pdf_error = render_pdf(html)
    return ReportBundle(html=html, pdf_bytes=pdf_bytes, pdf_error=pdf_error)
