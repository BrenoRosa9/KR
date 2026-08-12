"""Configuração por variável de ambiente.

Nada de segredo em código. O arquivo ``.env`` fica fora do Git e é o único lugar
onde credenciais existem em texto claro, no servidor.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="KR_", extra="ignore"
    )

    app_name: str = "Comparador de Documentos Técnicos"
    environment: str = "development"
    debug: bool = False

    database_url: str = "postgresql+psycopg://kr:kr@db:5432/kr"

    # Raiz do armazenamento. `blobs` é imutável e vai para o backup; `cache`
    # guarda derivados regeneráveis (páginas rasterizadas, PDF pós-OCR) e fica
    # deliberadamente fora do backup.
    storage_root: Path = Path("/data")
    max_upload_mb: int = 200

    session_secret: str = "troque-este-valor-em-producao"
    session_hours: int = 12
    # Bloqueio temporário depois de tentativas seguidas de senha errada.
    max_login_attempts: int = 8
    login_lockout_minutes: int = 15

    ocr_enabled: bool = True
    ocr_language: str = "por"

    # Camada de IA: desligada por padrão. Ligar é uma decisão consciente, com
    # implicação de LGPD quando o provedor é externo.
    vision_enabled: bool = False
    vision_provider: str = "none"
    vision_api_key: str = ""
    vision_model: str = ""
    vision_base_url: str = ""

    worker_poll_seconds: float = 2.0
    worker_max_attempts: int = 3
    job_timeout_seconds: int = 1800

    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> Any:
        """Aceita JSON, URL única ou lista separada por vírgula.

        Em PowerShell é fácil exportar ``KR_CORS_ORIGINS=http://localhost:5173``
        sem aspas JSON; pydantic-settings então quebra no ``json.loads``.
        """
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return value
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @property
    def blobs_dir(self) -> Path:
        return self.storage_root / "blobs"

    @property
    def cache_dir(self) -> Path:
        return self.storage_root / "cache"

    @property
    def reports_dir(self) -> Path:
        return self.storage_root / "reports"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_directories(self) -> None:
        for directory in (self.blobs_dir, self.cache_dir, self.reports_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
