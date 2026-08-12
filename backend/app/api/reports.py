"""Entrega do laudo em HTML e PDF."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..deps import current_user
from ..models import Analysis, AnalysisStatus, User
from ..reporting.report import render_html, render_pdf
from ..storage import report_path

router = APIRouter(prefix="/api/analyses", tags=["reports"])


@router.get("/{analysis_id}/report.html", response_class=HTMLResponse)
def report_html(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HTMLResponse:
    analysis = _ready_analysis(session, analysis_id)
    return HTMLResponse(render_html(session, analysis))


@router.get("/{analysis_id}/report.pdf")
def report_pdf(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    analysis = _ready_analysis(session, analysis_id)
    html = render_html(session, analysis)
    pdf_bytes, error = render_pdf(html)

    if pdf_bytes is None:
        # 503 e não 500: a falha é de dependência do ambiente, não da requisição,
        # e o HTML continua disponível.
        raise HTTPException(status_code=503, detail=error)

    # Grava em disco para servir de novo sem re-renderizar.
    destination = report_path(str(analysis.id), "pdf")
    destination.write_bytes(pdf_bytes)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="laudo-{analysis.id}.pdf"'
        },
    )


def _ready_analysis(session: Session, analysis_id: uuid.UUID) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Análise não encontrada.")
    if analysis.status in {AnalysisStatus.PENDING, AnalysisStatus.EXTRACTING}:
        raise HTTPException(
            status_code=409,
            detail="A análise ainda está em processamento. Aguarde a conclusão.",
        )
    if analysis.compared_at is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A análise não chegou a ser comparada. Consulte o registro de "
                "estágios da extração para entender o que faltou."
            ),
        )
    return analysis
