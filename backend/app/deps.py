"""Dependências compartilhadas dos endpoints."""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import get_session
from .models import Role, User
from .security import SESSION_COOKIE, resolve_session


def current_user(
    session: Session = Depends(get_session),
    kr_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    user = resolve_session(session, kr_session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão inválida ou expirada.",
        )
    return user


def require_editor(user: User = Depends(current_user)) -> User:
    if user.role not in {Role.ADMIN, Role.ANALYST}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seu perfil permite apenas consulta.",
        )
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ação restrita a administradores.",
        )
    return user


def client_ip(request: Request) -> str | None:
    """IP de origem, para o registro de auditoria.

    Confia no ``X-Forwarded-For`` porque a aplicação só é exposta atrás do
    proxy reverso próprio. Se um dia for publicada direto, esta função precisa
    mudar — cabeçalho vindo do cliente não é confiável.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
