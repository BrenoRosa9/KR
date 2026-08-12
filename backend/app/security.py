"""Autenticação: senhas, sessões e bloqueio por tentativa.

Sem biblioteca de identidade externa. Para uma dúzia de usuários internos,
Keycloak ou Auth0 seriam mais infraestrutura para manter do que o problema que
resolvem. Argon2id com sessão em banco cobre o necessário e permite revogar
acesso na hora.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .config import get_settings
from .models import AuditLog, Role, Session, User, as_utc

SESSION_COOKIE = "kr_session"

# Parâmetros acima do padrão da biblioteca: o custo é irrelevante num login
# esporádico e o ganho contra quebra offline de hash é real.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):  # pragma: no cover
        return False


class AuthError(Exception):
    """Falha de autenticação, sem distinguir a causa para quem chama.

    A mensagem é sempre a mesma para senha errada e usuário inexistente: dizer
    qual dos dois falhou entrega ao atacante metade do trabalho.
    """


def authenticate(session: DBSession, email: str, password: str) -> User:
    now = datetime.now(UTC)
    settings = get_settings()

    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.active:
        raise AuthError("Credenciais inválidas.")

    locked_until = as_utc(user.locked_until)
    if locked_until is not None and locked_until > now:
        remaining = int((locked_until - now).total_seconds() / 60) + 1
        raise AuthError(
            f"Conta temporariamente bloqueada por tentativas seguidas. "
            f"Tente novamente em {remaining} minuto(s)."
        )

    if not verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= settings.max_login_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_logins = 0
        session.commit()
        raise AuthError("Credenciais inválidas.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    session.commit()
    return user


def create_session(session: DBSession, user: User) -> Session:
    settings = get_settings()
    record = Session(
        token=secrets.token_urlsafe(32),
        user_id=user.id,
        expires_at=datetime.now(UTC)
        + timedelta(hours=settings.session_hours),
    )
    session.add(record)
    session.commit()
    return record


def resolve_session(session: DBSession, token: str | None) -> User | None:
    if not token:
        return None
    record = session.get(Session, token)
    if record is None:
        return None
    expires_at = as_utc(record.expires_at)
    if expires_at is None or expires_at <= datetime.now(UTC):
        session.delete(record)
        session.commit()
        return None
    user = record.user
    return user if user.active else None


def destroy_session(session: DBSession, token: str | None) -> None:
    if not token:
        return
    record = session.get(Session, token)
    if record is not None:
        session.delete(record)
        session.commit()


def purge_expired_sessions(session: DBSession) -> int:
    expired = session.scalars(
        select(Session).where(Session.expires_at <= datetime.now(UTC))
    ).all()
    for record in expired:
        session.delete(record)
    session.commit()
    return len(expired)


def can_edit(user: User) -> bool:
    return user.role in {Role.ADMIN, Role.ANALYST}


def record_audit(
    session: DBSession,
    user: User | None,
    action: str,
    entity: str = "",
    entity_id: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=detail,
            ip_address=ip_address,
        )
    )
