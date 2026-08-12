"""Upload e leitura dos PDFs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..deps import client_ip, current_user, require_editor
from ..models import Document, User
from ..schemas import DocumentOut
from ..security import record_audit
from ..storage import UploadRejected, blob_path, store_upload

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: User = Depends(require_editor),
) -> Document:
    """Recebe um PDF, valida por conteúdo e armazena endereçado pelo hash."""
    settings = get_settings()

    try:
        blob = store_upload(file.file, settings, filename=file.filename or "upload.pdf")
    except UploadRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    existing = session.scalar(select(Document).where(Document.sha256 == blob.sha256))
    if existing is not None:
        # Mesmo conteúdo já enviado: reaproveita o registro em vez de duplicar.
        return existing

    document = Document(
        sha256=blob.sha256,
        filename=(file.filename or "documento.pdf")[:255],
        size_bytes=blob.size_bytes,
        uploaded_by=user.id,
    )
    session.add(document)
    record_audit(
        session,
        user,
        "document.upload",
        "document",
        blob.sha256,
        {"filename": document.filename, "size": blob.size_bytes},
        ip_address=client_ip(request),
    )
    session.commit()
    return document


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return document


@router.get("/{document_id}/file")
def download_document(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> FileResponse:
    """Entrega o PDF original para o visualizador do frontend."""
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")

    path = blob_path(document.sha256)
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail=(
                "O arquivo não está mais no armazenamento, embora o registro exista. "
                "Verifique a integridade do diretório de blobs e o backup."
            ),
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=document.filename,
        headers={
            # Inline para o PDF.js abrir na própria página, sem download.
            "Content-Disposition": f'inline; filename="{document.filename}"',
            "Cache-Control": "private, max-age=3600",
        },
    )
