"""Conexão e sessão do banco."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")

# `pool_pre_ping` evita a falha clássica de conexão morta depois de o servidor
# ficar horas ocioso — cenário garantido numa aplicação de uso esporádico.
# O pool não se aplica ao SQLite, usado em teste e no desenvolvimento local.
_pool_options = (
    {}
    if _is_sqlite
    else {"pool_size": 5, "max_overflow": 5}
)
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
    **_pool_options,
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(connection, _record) -> None:  # type: ignore[no-untyped-def]
        """Faz o SQLite tolerar o worker e a API ao mesmo tempo.

        Sem WAL, um processo escrevendo bloqueia até a leitura do outro; sem
        ``busy_timeout``, quem chega segundo recebe “database is locked” na hora
        em vez de esperar. Uma extração demora minutos e escreve o tempo todo, o
        que derrubaria qualquer requisição concorrente. Em produção o banco é
        Postgres e nada disto se aplica.
        """
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """Dependência do FastAPI: uma sessão por requisição."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sessão transacional para uso fora do FastAPI (worker, scripts)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all() -> None:
    """Cria o esquema.

    Atalho consciente do MVP: enquanto o esquema muda todo dia, Alembic custa
    mais do que entrega. A troca para migração versionada é obrigatória antes de
    haver dados de cliente em produção, e está registrada no README.
    """
    Base.metadata.create_all(engine)
