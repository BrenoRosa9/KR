"""Comandos administrativos.

Executar com ``python -m app.cli <comando>``. Deliberadamente enxuto: criar o
primeiro usuário, redefinir senha, verificar integridade dos blobs e limpar
sessões expiradas. Tudo o mais é feito pela interface.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import re
import sys

from sqlalchemy import select

from .config import get_settings
from .db import create_all, session_scope
from .models import Document, Role, User
from .security import hash_password, purge_expired_sessions
from .storage import blob_path

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def command_init_db(_: argparse.Namespace) -> int:
    create_all()
    print("Esquema criado ou já existente.")
    return 0


def command_create_user(args: argparse.Namespace) -> int:
    # A tela de login valida o endereço como e-mail. Sem a mesma checagem aqui,
    # daria para criar um usuário que nunca conseguiria entrar.
    if not _EMAIL_RE.match(args.email.strip()):
        print(
            f"“{args.email}” não é um endereço de e-mail válido, e a tela de login "
            "só aceita e-mail. Use o endereço real da pessoa.",
            file=sys.stderr,
        )
        return 1

    password = args.password or getpass.getpass("Senha: ")
    if len(password) < 10:
        print(
            "Senha muito curta. Use ao menos 10 caracteres — este é o único fator de "
            "autenticação do sistema.",
            file=sys.stderr,
        )
        return 1

    with session_scope() as session:
        if session.scalar(select(User).where(User.email == args.email.lower())):
            print(f"Usuário {args.email} já existe.", file=sys.stderr)
            return 1
        session.add(
            User(
                email=args.email.strip().lower(),
                name=args.name,
                password_hash=hash_password(password),
                role=args.role,
            )
        )
    print(f"Usuário {args.email} criado com perfil {args.role}.")
    return 0


def command_reset_password(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Nova senha: ")
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"Usuário {args.email} não encontrado.", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        user.failed_logins = 0
        user.locked_until = None
    print("Senha redefinida e bloqueio removido.")
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

    create = subparsers.add_parser("create-user", help="cria um usuário")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument(
        "--role", default=Role.ANALYST, choices=[str(role) for role in Role]
    )
    create.add_argument("--password", default=None, help="omita para digitar oculto")
    create.set_defaults(func=command_create_user)

    reset = subparsers.add_parser("reset-password", help="redefine a senha")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password", default=None)
    reset.set_defaults(func=command_reset_password)

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
    sys.exit(main())
