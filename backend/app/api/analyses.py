"""Análises: criação, histórico, detalhe, revisão e recomparação."""

from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.tolerance import PROFILES
from ..db import get_session
from ..deps import client_ip
from ..jobs import RECOMPARE, enqueue
from ..models import Analysis, Document, Extraction, Finding, Observation
from ..schemas import (
    AnalysisCreate,
    AnalysisDetail,
    AnalysisOut,
    CRSUpdate,
    DocumentOut,
    ExtractionOut,
    FindingOut,
    ObservationOut,
    ObservationUpdate,
    ProfileOut,
)
from ..security import record_audit
from ..services import (
    ServiceError,
    create_analysis,
    set_extraction_crs,
    update_observation,
)
from ..storage import UploadRejected, store_upload

router = APIRouter(prefix="/api", tags=["analyses"])


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles() -> list[ProfileOut]:
    """Mantido por compatibilidade; há um único critério: igualdade exata."""
    from ..core.tolerance import DEFAULT_PROFILE_KEY

    profile = PROFILES[DEFAULT_PROFILE_KEY]
    return [
        ProfileOut(
            key=profile.key,
            name=profile.name,
            coordinate_m=profile.coordinate_m,
            distance_m=profile.distance_m,
            distance_ppm=profile.distance_ppm,
            azimuth_arcsec=profile.azimuth_arcsec,
            angle_arcsec=profile.angle_arcsec,
            area_m2=profile.area_m2,
            area_relative=profile.area_relative,
            perimeter_m=profile.perimeter_m,
            min_closure_precision=profile.min_closure_precision
            if profile.min_closure_precision != float("inf")
            else 0.0,
        )
    ]


@router.post("/analyses", response_model=AnalysisOut, status_code=status.HTTP_201_CREATED)
def create(
    payload: AnalysisCreate,
    session: Session = Depends(get_session),
) -> Analysis:
    document_a = session.get(Document, payload.document_a_id)
    document_b = session.get(Document, payload.document_b_id)
    if document_a is None or document_b is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")

    try:
        return create_analysis(
            session, None, document_a, document_b, payload.profile, payload.title
        )
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/analyses/upload", response_model=AnalysisOut, status_code=status.HTTP_201_CREATED
)
def create_with_upload(
    request: Request,
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    profile: str = Query("exato"),
    title: str = Query(""),
    session: Session = Depends(get_session),
) -> Analysis:
    """Envia os dois PDFs e cria a análise em uma única requisição.

    Existe porque é exatamente o que a interface faz: o analista escolhe dois
    arquivos e clica em comparar. Fatiar isso em três chamadas só transferiria
    para o frontend a tarefa de lidar com falha parcial.
    """
    settings = get_settings()
    documents: list[Document] = []

    for upload in (file_a, file_b):
        try:
            blob = store_upload(
                upload.file, settings, filename=upload.filename or "upload.pdf"
            )
        except UploadRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        document = session.scalar(
            select(Document).where(Document.sha256 == blob.sha256)
        )
        if document is None:
            document = Document(
                sha256=blob.sha256,
                filename=(upload.filename or "documento.pdf")[:255],
                size_bytes=blob.size_bytes,
                uploaded_by=None,
            )
            session.add(document)
            session.flush()
        documents.append(document)

    record_audit(
        session,
        None,
        "document.upload_pair",
        "document",
        documents[0].sha256,
        {"a": documents[0].filename, "b": documents[1].filename},
        ip_address=client_ip(request),
    )
    session.commit()

    try:
        return create_analysis(
            session, None, documents[0], documents[1], profile, title
        )
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/analyses", response_model=list[AnalysisOut])
def history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    session: Session = Depends(get_session),
) -> list[Analysis]:
    query = select(Analysis).order_by(Analysis.created_at.desc())
    if status_filter:
        query = query.where(Analysis.status == status_filter)
    return list(session.scalars(query.limit(limit).offset(offset)).all())


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetail)
def detail(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> AnalysisDetail:
    analysis = _get_analysis(session, analysis_id)
    extractions = list(
        session.scalars(
            select(Extraction)
            .where(Extraction.analysis_id == analysis.id)
            .order_by(Extraction.role)
        ).all()
    )
    findings = list(
        session.scalars(
            select(Finding)
            .where(Finding.analysis_id == analysis.id)
            .order_by(Finding.ordinal)
        ).all()
    )

    return AnalysisDetail(
        analysis=AnalysisOut.model_validate(analysis),
        documents={
            "A": DocumentOut.model_validate(analysis.document_a),
            "B": DocumentOut.model_validate(analysis.document_b),
        },
        extractions=[ExtractionOut.model_validate(item) for item in extractions],
        findings=[FindingOut.model_validate(item) for item in findings],
        match=analysis.match_summary,
    )


@router.get(
    "/analyses/{analysis_id}/observations", response_model=list[ObservationOut]
)
def observations(
    analysis_id: uuid.UUID,
    role: str | None = Query(None, pattern="^[AB]$"),
    low_confidence_only: bool = Query(False),
    session: Session = Depends(get_session),
) -> list[Observation]:
    """Valores extraídos, para a tela de revisão."""
    analysis = _get_analysis(session, analysis_id)
    extraction_ids = [
        extraction.id
        for extraction in session.scalars(
            select(Extraction).where(Extraction.analysis_id == analysis.id)
        ).all()
        if role is None or extraction.role == role
    ]
    if not extraction_ids:
        return []

    query = (
        select(Observation)
        .where(Observation.extraction_id.in_(extraction_ids))
        .order_by(Observation.extraction_id, Observation.ordinal)
    )
    if low_confidence_only:
        profile = PROFILES.get(analysis.profile_name, PROFILES["padrao"])
        query = query.where(Observation.confidence < profile.low_confidence)

    return list(session.scalars(query).all())


@router.patch("/observations/{observation_id}", response_model=ObservationOut)
def edit_observation(
    observation_id: uuid.UUID,
    payload: ObservationUpdate,
    session: Session = Depends(get_session),
) -> Observation:
    if payload.value_num is None and payload.value_text is None:
        raise HTTPException(
            status_code=400, detail="Informe value_num ou value_text."
        )
    try:
        return update_observation(
            session,
            None,
            observation_id,
            value_num=payload.value_num,
            value_text=payload.value_text,
            recompare=payload.recompare,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/extractions/{extraction_id}/crs", response_model=ExtractionOut)
def update_crs(
    extraction_id: uuid.UUID,
    payload: CRSUpdate,
    session: Session = Depends(get_session),
) -> Extraction:
    """Confirma manualmente datum e fuso de um documento."""
    try:
        return set_extraction_crs(
            session,
            None,
            extraction_id,
            epsg=payload.epsg,
            datum_label=payload.datum_label,
            utm_zone=payload.utm_zone,
            hemisphere=payload.hemisphere,
            distances_are_ground=payload.distances_are_ground,
            average_height_m=payload.average_height_m,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/analyses/{analysis_id}/recompare", response_model=AnalysisOut)
def recompare(
    analysis_id: uuid.UUID,
    profile: str | None = Query(None),
    session: Session = Depends(get_session),
) -> Analysis:
    """Recompara sem reextrair.

    O parâmetro ``profile`` é aceito por compatibilidade, mas todos os perfis
    exigem igualdade exata dos números.
    """
    analysis = _get_analysis(session, analysis_id)

    if profile is not None:
        if profile not in PROFILES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Perfil desconhecido. Disponíveis: "
                    f"{', '.join(sorted(PROFILES))}."
                ),
            )
        analysis.profile_name = profile
        analysis.profile_snapshot = PROFILES[profile].__dict__.copy()
    elif not analysis.profile_name:
        from ..core.tolerance import DEFAULT_PROFILE_KEY

        analysis.profile_name = DEFAULT_PROFILE_KEY
        analysis.profile_snapshot = PROFILES[DEFAULT_PROFILE_KEY].__dict__.copy()

    enqueue(session, RECOMPARE, {"analysis_id": str(analysis.id)}, max_attempts=2)
    record_audit(
        session,
        None,
        "analysis.recompare",
        "analysis",
        str(analysis.id),
        {"profile": analysis.profile_name},
    )
    session.commit()
    return analysis


def _get_analysis(session: Session, analysis_id: uuid.UUID) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Análise não encontrada.")
    return analysis
