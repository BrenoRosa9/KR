"""Worker: consome a fila e executa extração e comparação.

Processo separado do servidor web de propósito. Um PDF de oitenta páginas
escaneadas ocupa CPU por minutos e memória em picos; deixar isso dentro do
processo que atende HTTP faz a interface travar e o navegador expirar.

Executar com: ``python -m app.worker``
"""

from __future__ import annotations

import logging
import signal
import sys
import time
import uuid
from types import FrameType

from .config import get_settings
from .db import session_scope
from .jobs import EXTRACT_AND_COMPARE, RECOMPARE, claim, fail, finish, requeue_stale
from .models import Job
from .services import run_extract_and_compare, run_recompare

logger = logging.getLogger("kr.worker")

_shutdown = False
STALE_CHECK_EVERY = 60.0


def _handle_signal(signum: int, frame: FrameType | None) -> None:
    """Encerramento limpo: termina o trabalho atual antes de sair.

    Sem isso, um ``docker compose up -d`` no meio de um processamento deixaria o
    trabalho marcado como em execução para sempre.
    """
    global _shutdown
    logger.info("Sinal %s recebido; encerrando após o trabalho atual.", signum)
    _shutdown = True


def run_job(job: Job) -> None:
    analysis_id = job.payload.get("analysis_id")
    if not analysis_id:
        raise ValueError("Trabalho sem analysis_id no payload.")

    identifier = uuid.UUID(analysis_id)
    with session_scope() as session:
        if job.kind == EXTRACT_AND_COMPARE:
            run_extract_and_compare(session, identifier)
        elif job.kind == RECOMPARE:
            run_recompare(session, identifier)
        else:
            raise ValueError(f"Tipo de trabalho desconhecido: {job.kind}")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    settings.ensure_directories()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "Worker iniciado. Intervalo de consulta: %ss", settings.worker_poll_seconds
    )
    last_stale_check = 0.0

    while not _shutdown:
        now = time.monotonic()
        if now - last_stale_check > STALE_CHECK_EVERY:
            last_stale_check = now
            with session_scope() as session:
                recovered = requeue_stale(session, settings.job_timeout_seconds)
            if recovered:
                logger.warning(
                    "%s trabalho(s) travado(s) devolvido(s) à fila.", recovered
                )

        with session_scope() as session:
            job = claim(session, kinds=[EXTRACT_AND_COMPARE, RECOMPARE])
            job_id = job.id if job else None
            job_kind = job.kind if job else None
            payload = dict(job.payload) if job else {}

        if job_id is None:
            time.sleep(settings.worker_poll_seconds)
            continue

        logger.info("Executando %s (%s), tentativa registrada.", job_kind, job_id)
        started = time.monotonic()
        try:
            run_job(_DetachedJob(job_id, job_kind or "", payload))
        except Exception as exc:  # noqa: BLE001 - o worker não pode morrer por um job
            logger.exception("Trabalho %s falhou.", job_id)
            with session_scope() as session:
                stored = session.get(Job, job_id)
                if stored is not None:
                    fail(session, stored, f"{type(exc).__name__}: {exc}")
        else:
            elapsed = time.monotonic() - started
            logger.info("Trabalho %s concluído em %.1fs.", job_id, elapsed)
            with session_scope() as session:
                stored = session.get(Job, job_id)
                if stored is not None:
                    finish(session, stored)

    logger.info("Worker encerrado.")
    return 0


class _DetachedJob:
    """Cópia do trabalho sem vínculo com a sessão que o reivindicou.

    O trabalho é executado em uma transação própria; manter o objeto ORM da
    sessão anterior deixaria a conexão aberta durante todo o processamento, que
    pode levar minutos.
    """

    def __init__(self, identifier: uuid.UUID, kind: str, payload: dict) -> None:
        self.id = identifier
        self.kind = kind
        self.payload = payload


if __name__ == "__main__":
    sys.exit(main())
