"""Modelo de dados.

A decisão estrutural do projeto está na tabela :class:`Observation`: toda
grandeza extraída é uma linha, com valor normalizado, string bruta, precisão,
confiança e localização exata na página. Comparação, revisão, rastreabilidade e
registro de baixa confiança passam a ser consultas sobre uma única tabela, em
vez de quatro subsistemas.

Consequência prática: as observações são a fonte da verdade. O :class:`Parcel`
usado pelo motor de comparação é reconstruído a partir delas a cada execução, de
modo que uma correção humana em uma célula se propaga sozinha para todo o
recálculo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB no Postgres, JSON em SQLite: os testes rodam sem banco de verdade.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Garante datetime com fuso na leitura.

    Nem todo backend devolve o fuso que recebeu — SQLite não guarda offset, e
    uma coluna criada sem ``timezone=True`` no Postgres tem o mesmo efeito.
    Comparar o valor lido com ``datetime.now(timezone.utc)`` levanta
    ``TypeError`` nesses casos, e o lugar onde isso acontece é a validação de
    sessão: a falha derruba toda requisição autenticada. Assumir UTC no que vem
    sem fuso é correto porque tudo é gravado em UTC.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    AWAITING_REVIEW = "awaiting_review"
    COMPARED = "compared"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.ANALYST)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Session(Base):
    """Sessão em banco, e não apenas cookie assinado, para permitir revogação."""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Document(Base):
    """PDF enviado, endereçado por conteúdo.

    O SHA-256 é a chave natural: o mesmo arquivo reenviado não gera cópia nem
    reprocessamento, e a deduplicação sai de graça.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    producer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    triage: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    @property
    def relative_path(self) -> str:
        """Caminho no diretório de blobs, particionado para não criar um
        diretório com dezenas de milhares de entradas."""
        return f"{self.sha256[:2]}/{self.sha256}"


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(
        String(30), default=AnalysisStatus.PENDING, index=True
    )
    document_a_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id"))
    document_b_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id"))
    profile_name: Mapped[str] = mapped_column(String(40), default="exato")
    # Snapshot do critério usado na análise (histórico: era o perfil de tolerância).
    profile_snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    match_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    compared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    document_a: Mapped[Document] = relationship(foreign_keys=[document_a_id])
    document_b: Mapped[Document] = relationship(foreign_keys=[document_b_id])
    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class Extraction(Base):
    """Resultado da extração de um documento dentro de uma análise."""

    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id"))
    role: Mapped[str] = mapped_column(String(1))  # "A" ou "B"
    label: Mapped[str] = mapped_column(String(255), default="")

    crs_epsg: Mapped[str | None] = mapped_column(String(20), nullable=True)
    datum_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    utm_zone: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hemisphere: Mapped[str | None] = mapped_column(String(1), nullable=True)
    number_convention: Mapped[str] = mapped_column(String(12), default="br")
    distances_are_ground: Mapped[bool] = mapped_column(Boolean, default=True)
    average_height_m: Mapped[float] = mapped_column(Float, default=0.0)

    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table_strategy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    segment_convention: Mapped[str] = mapped_column(String(12), default="leading")

    stages: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    errors: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    analysis: Mapped[Analysis] = relationship(back_populates="extractions")
    document: Mapped[Document] = relationship()
    observations: Mapped[list[Observation]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="Observation.ordinal",
    )


class Observation(Base):
    """Uma grandeza lida de um documento, com tudo que a torna auditável."""

    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("extractions.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(30), index=True)
    # Índices do vértice e do segmento a que a observação pertence. Nulos para
    # grandezas do imóvel como um todo (área, perímetro, matrícula).
    vertex_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    segment_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)

    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(String(10), default="")
    halfwidth: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(20), default="table_cell")
    table_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")

    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    original_value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    extraction: Mapped[Extraction] = relationship(back_populates="observations")


Index(
    "ix_observations_extraction_field",
    Observation.extraction_id,
    Observation.field,
    Observation.vertex_index,
)


class Finding(Base):
    """Um achado da comparação, com procedência dos dois lados."""

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    field: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    message: Mapped[str] = mapped_column(Text)
    value_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(10), default="")
    scope: Mapped[str] = mapped_column(String(4), default="AB")
    provenance_a: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    provenance_b: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)

    analysis: Mapped[Analysis] = relationship(back_populates="findings")


class Job(Base):
    """Fila de trabalho no próprio Postgres.

    Volume baixo não justifica Redis mais Celery. ``FOR UPDATE SKIP LOCKED``
    resolve concorrência de workers com uma dependência a menos para instalar,
    monitorar e atualizar.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(12), default=JobStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AuditLog(Base):
    """Quem fez o quê. É o que dá defensabilidade ao laudo."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
