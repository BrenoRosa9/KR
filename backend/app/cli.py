"""Comandos administrativos.

Executar com ``python -m app.cli <comando>``. Deliberadamente enxuto: criar o
esquema, verificar integridade dos blobs e limpar sessões antigas. Tudo o mais
é feito pela interface.
"""

from __future__ import annotations

import argparse
import hashlib

from sqlalchemy import select

from .config import get_settings
from .db import create_all, session_scope
from .models import Document
from .security import purge_expired_sessions
from .storage import blob_path


def command_init_db(_: argparse.Namespace) -> int:
    create_all()
    print("Esquema criado ou já existente.")
    return 0


def command_verify_blobs(_: argparse.Namespace) -> int:
    """Confere se cada documento registrado existe em disco com o hash correto.

    Vale rodar depois de restaurar backup: o banco e os arquivos são
    componentes separados, e nada garante por si que voltaram consistentes.
    """
    settings = get_settings()
    missing = 0
    corrupt = 0
    checked = 0

    with session_scope() as session:
        for document in session.scalars(select(Document)).all():
            checked += 1
            path = blob_path(document.sha256, settings)
            if not path.exists():
                missing += 1
                print(f"AUSENTE  {document.sha256}  {document.filename}")
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != document.sha256:
                corrupt += 1
                print(f"CORROMPIDO  {document.sha256}  {document.filename}")

    print(
        f"\n{checked} documento(s) verificado(s): {missing} ausente(s), "
        f"{corrupt} corrompido(s)."
    )
    return 1 if (missing or corrupt) else 0


def command_purge_sessions(_: argparse.Namespace) -> int:
    with session_scope() as session:
        removed = purge_expired_sessions(session)
    print(f"{removed} sessão(ões) expirada(s) removida(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="cria o esquema").set_defaults(
        func=command_init_db
    )

    subparsers.add_parser(
        "verify-blobs", help="confere integridade dos PDFs armazenados"
    ).set_defaults(func=command_verify_blobs)

    subparsers.add_parser(
        "purge-sessions", help="remove sessões expiradas"
    ).set_defaults(func=command_purge_sessions)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
