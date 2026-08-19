"""Orquestração das análises.

Ponto de projeto: a API nunca executa extração nem comparação. Ela grava o
arquivo, cria a análise, enfileira o trabalho e responde. Tudo o que é caro
acontece no worker, em estágios persistidos individualmente.

Isso resolve de uma vez três requisitos que pareceriam independentes: um PDF
pesado não derruba a requisição HTTP, cada estágio pode ser reexecutado depois
de uma correção humana, e o histórico de análises é consequência do modelo de
dados em vez de uma funcionalidade separada.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .core.compare import compare_parcels
from .core.tolerance import PROFILES, ToleranceProfile
from .extraction.pipeline import extract_document
from .jobs import EXTRACT_AND_COMPARE, RECOMPARE, enqueue
from .models import Analysis, AnalysisStatus, Document, Extraction, Observation, User
from .repository import (
    parcel_from_extraction,
    persist_comparison,
    persist_extraction,
)
from .security import record_audit
from .storage import blob_path, cache_dir_for


class ServiceError(Exception):
    pass


def resolve_profile(name: str) -> ToleranceProfile:
    """Resolve o perfil pela chave, aceitando também o rótulo de exibição.

    Análises antigas podem ter gravado ``padrao``/``rigoroso``; todos resolvem
    para o critério atual de igualdade exata.
    """
    from .core.tolerance import DEFAULT_PROFILE_KEY

    if not name:
        return PROFILES[DEFAULT_PROFILE_KEY]
    profile = PROFILES.get(name)
    if profile is None:
        profile = next((item for item in PROFILES.values() if item.name == name), None)
    if profile is None:
        return PROFILES[DEFAULT_PROFILE_KEY]
    return profile


def create_analysis(
    session: Session,
    user: User | None,
    document_a: Document,
    document_b: Document,
    profile_name: str = "exato",
    title: str = "",
) -> Analysis:
    """Cria a análise e enfileira o processamento."""
    profile = resolve_profile(profile_name)

    if document_a.id == document_b.id:
        raise ServiceError(
            "Os dois documentos são o mesmo arquivo (conteúdo idêntico). "
            "Não há o que comparar."
        )

    analysis = Analysis(
        title=title or f"{document_a.filename} × {document_b.filename}",
        status=AnalysisStatus.PENDING,
        document_a_id=document_a.id,
        document_b_id=document_b.id,
        profile_name=profile_name,
        profile_snapshot=profile.__dict__.copy(),
        created_by=user.id if user else None,
    )
    session.add(analysis)
    session.flush()

    enqueue(
        session,
        EXTRACT_AND_COMPARE,
        {"analysis_id": str(analysis.id)},
        max_attempts=get_settings().worker_max_attempts,
    )
    record_audit(
        session,
        user,
        "analysis.create",
        "analysis",
        str(analysis.id),
        {"profile": profile_name},
    )
    session.commit()
    return analysis


def run_extract_and_compare(session: Session, analysis_id: uuid.UUID) -> Analysis:
    """Executa a extração dos dois documentos e a comparação."""
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise ServiceError(f"Análise {analysis_id} não encontrada.")

    settings = get_settings()
    analysis.status = AnalysisStatus.EXTRACTING
    analysis.error = None
    session.commit()

    # Reexecução limpa: descarta extrações anteriores para não acumular
    # observações órfãs de uma tentativa que falhou no meio.
    for existing in list(analysis.extractions):
        session.delete(existing)
    session.flush()

    try:
        for role, document in (("A", analysis.document_a), ("B", analysis.document_b)):
            result = extract_document(
                path=blob_path(document.sha256, settings),
                document_id=str(document.id),
                label=document.filename,
                ocr_enabled=settings.ocr_enabled,
                ocr_output_dir=cache_dir_for(document.sha256, settings),
            )
            if document.page_count is None and result.profile is not None:
                document.page_count = result.profile.page_count
                document.producer = result.profile.producer
                document.triage = _triage_payload(result)

            persist_extraction(session, analysis.id, document.id, role, result)
        session.commit()
    except Exception as exc:  # noqa: BLE001 - a falha precisa virar estado visível
        analysis.status = AnalysisStatus.FAILED
        analysis.error = f"Falha na extração: {exc}"
        session.commit()
        raise

    return compare_analysis(session, analysis)


def compare_analysis(session: Session, analysis: Analysis) -> Analysis:
    """Compara as extrações atuais da análise, sejam originais ou revisadas."""
    profile = resolve_profile(analysis.profile_name)
    extraction_a = _extraction_for(session, analysis, "A")
    extraction_b = _extraction_for(session, analysis, "B")

    if extraction_a is None or extraction_b is None:
        analysis.status = AnalysisStatus.FAILED
        analysis.error = "Extração ausente para um dos documentos."
        session.commit()
        return analysis

    parcel_a = parcel_from_extraction(session, extraction_a)
    parcel_b = parcel_from_extraction(session, extraction_b)

    if not parcel_a.vertices or not parcel_b.vertices:
        analysis.status = AnalysisStatus.FAILED
        analysis.error = (
            "Nenhum vértice foi extraído de um dos documentos. Verifique o registro "
            "de estágios da extração; o layout pode não ser suportado ainda."
        )
        session.commit()
        return analysis

    result = compare_parcels(parcel_a, parcel_b, profile)
    persist_comparison(session, analysis, result, profile)

    # "Comparado" não significa "aprovado": lacuna de dados e valor de baixa
    # confiança devolvem a análise para revisão humana antes do laudo.
    needs_review = any(
        finding.kind in {"data_gap", "low_confidence"} for finding in result.findings
    )
    analysis.status = (
        AnalysisStatus.AWAITING_REVIEW if needs_review else AnalysisStatus.COMPARED
    )
    session.commit()
    return analysis


def run_recompare(session: Session, analysis_id: uuid.UUID) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise ServiceError(f"Análise {analysis_id} não encontrada.")
    return compare_analysis(session, analysis)


def update_observation(
    session: Session,
    user: User | None,
    observation_id: uuid.UUID,
    value_num: float | None = None,
    value_text: str | None = None,
    recompare: bool = True,
) -> Observation:
    """Aplica uma correção humana a um valor extraído.

    O valor original é preservado na própria linha. Um laudo precisa poder
    mostrar o que estava escrito no documento e o que o revisor corrigiu — se a
    edição sobrescrevesse a leitura, a rastreabilidade morreria na primeira
    correção.
    """
    observation = session.get(Observation, observation_id)
    if observation is None:
        raise ServiceError("Observação não encontrada.")

    if not observation.edited:
        observation.original_value_num = observation.value_num
        observation.original_value_text = observation.value_text

    if value_num is not None:
        observation.value_num = value_num
    if value_text is not None:
        observation.value_text = value_text

    observation.edited = True
    observation.confidence = 1.0
    observation.edited_by = user.id if user else None
    observation.edited_at = datetime.now(UTC)

    record_audit(
        session,
        user,
        "observation.edit",
        "observation",
        str(observation.id),
        {
            "field": observation.field,
            "from": observation.original_value_num or observation.original_value_text,
            "to": value_num if value_num is not None else value_text,
        },
    )
    session.commit()

    if recompare:
        extraction = session.get(Extraction, observation.extraction_id)
        if extraction is not None:
            enqueue(
                session,
                RECOMPARE,
                {"analysis_id": str(extraction.analysis_id)},
                max_attempts=2,
            )
            session.commit()
    return observation


def set_extraction_crs(
    session: Session,
    user: User | None,
    extraction_id: uuid.UUID,
    epsg: str,
    datum_label: str,
    utm_zone: int | None,
    hemisphere: str | None,
    distances_are_ground: bool | None = None,
    average_height_m: float | None = None,
) -> Extraction:
    """Confirma manualmente o sistema de referência de um documento.

    É a contrapartida da regra de nunca assumir datum: quando o documento não
    declara, alguém precisa declarar, com registro no log de auditoria.
    """
    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise ServiceError("Extração não encontrada.")

    extraction.crs_epsg = epsg
    extraction.datum_label = datum_label
    extraction.utm_zone = utm_zone
    extraction.hemisphere = hemisphere
    if distances_are_ground is not None:
        extraction.distances_are_ground = distances_are_ground
    if average_height_m is not None:
        extraction.average_height_m = average_height_m

    record_audit(
        session,
        user,
        "extraction.set_crs",
        "extraction",
        str(extraction.id),
        {"epsg": epsg, "zone": utm_zone, "hemisphere": hemisphere},
    )
    enqueue(
        session, RECOMPARE, {"analysis_id": str(extraction.analysis_id)}, max_attempts=2
    )
    session.commit()
    return extraction


def _extraction_for(
    session: Session, analysis: Analysis, role: str
) -> Extraction | None:
    return session.scalar(
        select(Extraction).where(
            Extraction.analysis_id == analysis.id, Extraction.role == role
        )
    )


def _triage_payload(result) -> dict:
    profile = result.profile
    if profile is None:
        return {}
    return {
        "dominant_class": str(profile.dominant_class),
        "needs_ocr": profile.needs_ocr,
        "pages": [
            {
                "number": page.number,
                "classification": str(page.classification),
                "relevance": str(page.relevance),
                "char_count": page.char_count,
                "image_count": page.image_count,
                "vector_count": page.vector_count,
                "table_candidates": page.table_candidates,
                "notes": page.notes,
            }
            for page in profile.pages
        ],
    }
