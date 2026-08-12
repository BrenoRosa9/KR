"""Fila de trabalho sobre o próprio Postgres.

``SELECT ... FOR UPDATE SKIP LOCKED`` dá exclusão mútua entre workers sem
broker. Para o volume descrito — poucas análises por dia — isso substitui Redis
mais Celery com uma dependência a menos para instalar, monitorar, atualizar e
incluir no backup.

O que se perde: agendamento sofisticado, prioridades, fan-out. Nada disso é
necessário aqui, e reintroduzir um broker depois é uma troca localizada, porque
o resto do sistema só conhece :func:`enqueue` e :func:`claim`.
"""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Job, JobStatus

EXTRACT_AND_COMPARE = "extract_and_compare"
RECOMPARE = "recompare"

WORKER_NAME = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"


def enqueue(
    session: Session,
    kind: str,
    payload: dict,
    max_attempts: int = 3,
    delay_seconds: float = 0.0,
) -> Job:
    job = Job(
        kind=kind,
        payload=payload,
        max_attempts=max_attempts,
        run_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
    )
    session.add(job)
    session.flush()
    return job


def claim(session: Session, kinds: list[str] | None = None) -> Job | None:
    """Pega um trabalho da fila e o marca como em execução, atomicamente."""
    now = datetime.now(UTC)
    query = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.run_at <= now)
        .order_by(Job.run_at)
        .limit(1)
    )
    if kinds:
        query = query.where(Job.kind.in_(kinds))

    # SKIP LOCKED só existe no Postgres; em SQLite (testes) a serialização do
    # próprio arquivo já garante que dois workers não peguem o mesmo trabalho.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)

    job = session.scalar(query)
    if job is None:
        return None

    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.locked_at = now
    job.locked_by = WORKER_NAME
    session.commit()
    return job


def finish(session: Session, job: Job) -> None:
    job.status = JobStatus.DONE
    job.finished_at = datetime.now(UTC)
    job.error = None
    session.commit()


def fail(
    session: Session, job: Job, error: str, retry_delay_seconds: float = 30.0
) -> None:
    """Marca falha e reenfileira enquanto houver tentativa disponível."""
    job.error = error[:4000]
    if job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED
        job.locked_at = None
        job.locked_by = None
        job.run_at = datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)
    else:
        job.status = JobStatus.FAILED
        job.finished_at = datetime.now(UTC)
    session.commit()


def requeue_stale(session: Session, timeout_seconds: int) -> int:
    """Devolve à fila trabalhos presos em execução.

    Cobre o caso em que o worker morreu — contêiner reiniciado, servidor
    reinicializado — deixando um trabalho marcado como em execução para sempre.
    Sem isso, uma análise ficaria travada até alguém mexer no banco à mão.
    """
    threshold = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
    stale = session.scalars(
        select(Job).where(Job.status == JobStatus.RUNNING, Job.locked_at < threshold)
    ).all()

    for job in stale:
        if job.attempts < job.max_attempts:
            job.status = JobStatus.QUEUED
            job.locked_at = None
            job.locked_by = None
            job.run_at = datetime.now(UTC)
        else:
            job.status = JobStatus.FAILED
            job.error = (
                f"Interrompido: excedeu {timeout_seconds}s em execução sem concluir."
            )
            job.finished_at = datetime.now(UTC)
    session.commit()
    return len(stale)


def queue_depth(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in JobStatus:
        counts[str(status)] = (
            session.scalar(
                text("SELECT COUNT(*) FROM jobs WHERE status = :status"),
                {"status": str(status)},
            )
            or 0
        )
    return counts
