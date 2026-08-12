"""Armazenamento de arquivos endereçado por conteúdo.

PDFs originais vão para ``blobs/<sha256[:2]>/<sha256>`` e nunca são alterados.
Derivados (páginas rasterizadas, PDF pós-OCR, relatórios) vão para ``cache/`` e
``reports/``, que podem ser apagados a qualquer momento e reconstruídos — razão
pela qual só ``blobs/`` e o banco entram na rotina de backup.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .config import Settings, get_settings

PDF_MAGIC = b"%PDF-"
CHUNK_SIZE = 1024 * 1024


class UploadRejected(Exception):
    """Arquivo recusado na porta de entrada.

    Todo PDF é tratado como hostil: um arquivo é validado por conteúdo, não por
    extensão nem por Content-Type, que o cliente controla.
    """


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    path: Path
    size_bytes: int
    deduplicated: bool


def store_upload(
    stream: BinaryIO, settings: Settings | None = None, filename: str = ""
) -> StoredBlob:
    """Grava o upload validando magic bytes e limite de tamanho durante a leitura."""
    settings = settings or get_settings()
    settings.ensure_directories()

    tag = hashlib.sha256(filename.encode()).hexdigest()[:12]
    temporary = settings.cache_dir / f"upload-{tag}.part"
    digest = hashlib.sha256()
    size = 0
    first_chunk = True

    try:
        with temporary.open("wb") as target:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                if first_chunk:
                    if not chunk.startswith(PDF_MAGIC):
                        raise UploadRejected(
                            "O arquivo não é um PDF (assinatura ausente no início do "
                            "conteúdo)."
                        )
                    first_chunk = False
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise UploadRejected(
                        f"Arquivo maior que o limite de {settings.max_upload_mb} MB."
                    )
                digest.update(chunk)
                target.write(chunk)

        if first_chunk:
            raise UploadRejected("Arquivo vazio.")

        sha256 = digest.hexdigest()
        destination = blob_path(sha256, settings)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            # Mesmo conteúdo já armazenado: descarta o temporário e reaproveita.
            temporary.unlink(missing_ok=True)
            return StoredBlob(
                sha256=sha256,
                path=destination,
                size_bytes=destination.stat().st_size,
                deduplicated=True,
            )

        shutil.move(str(temporary), str(destination))
        return StoredBlob(
            sha256=sha256, path=destination, size_bytes=size, deduplicated=False
        )
    finally:
        temporary.unlink(missing_ok=True)


def blob_path(sha256: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.blobs_dir / sha256[:2] / sha256


def cache_dir_for(sha256: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    directory = settings.cache_dir / sha256[:2] / sha256
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def report_path(
    analysis_id: str, extension: str, settings: Settings | None = None
) -> Path:
    settings = settings or get_settings()
    settings.ensure_directories()
    return settings.reports_dir / f"{analysis_id}.{extension}"
