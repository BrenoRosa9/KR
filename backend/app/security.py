"""Registro de auditoria.

A ferramenta roda na rede interna da empresa, sem login. Quem fez o quê
continua sendo gravado quando há um usuário associado; caso contrário fica o
IP da requisição.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .models import AuditLog, Session, User


def purge_expired_sessions(session: DBSession) -> int:
    expired = session.scalars(
        select(Session).where(Session.expires_at <= datetime.now(UTC))
    ).all()
    for record in expired:
        session.delete(record)
    session.commit()
    return len(expired)


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
