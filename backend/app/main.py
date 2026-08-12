"""Aplicação FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import __version__
from .api import analyses, auth, documents, reports
from .config import get_settings
from .db import create_all, get_session
from .jobs import queue_depth
from .schemas import HealthOut

logger = logging.getLogger("kr.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_directories()
    create_all()
    logger.info(
        "API iniciada em modo %s. Armazenamento: %s",
        settings.environment,
        settings.storage_root,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # Em produção o frontend é servido pelo mesmo proxy reverso, então não há
    # origem cruzada. A lista só é preenchida no desenvolvimento, em que o Vite
    # roda em outra porta.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(analyses.router)
    app.include_router(reports.router)

    @app.get("/api/health", response_model=HealthOut)
    def health(session: Session = Depends(get_session)) -> HealthOut:
        """Sonda para o proxy reverso e para a verificação manual pós-atualização."""
        database_ok = True
        depth: dict[str, int] = {}
        try:
            session.execute(text("SELECT 1"))
            depth = queue_depth(session)
        except Exception:  # pragma: no cover - banco fora do ar
            database_ok = False

        return HealthOut(
            status="ok" if database_ok else "degraded",
            database=database_ok,
            queue=depth,
            version=__version__,
        )

    return app


app = create_app()
