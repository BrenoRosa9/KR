"""Login, logout e identidade da sessão."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..deps import client_ip, current_user
from ..models import User
from ..schemas import LoginRequest, UserOut
from ..security import (
    SESSION_COOKIE,
    AuthError,
    authenticate,
    create_session,
    destroy_session,
    record_audit,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> User:
    try:
        user = authenticate(session, payload.email, payload.password)
    except AuthError as exc:
        record_audit(
            session,
            None,
            "auth.login_failed",
            "user",
            payload.email,
            ip_address=client_ip(request),
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    record = create_session(session, user)
    settings = get_settings()

    # HttpOnly bloqueia leitura por JavaScript; SameSite=lax barra envio em
    # requisição cross-site, o que cobre CSRF nos métodos que importam aqui.
    response.set_cookie(
        key=SESSION_COOKIE,
        value=record.token,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    record_audit(
        session,
        user,
        "auth.login",
        "user",
        str(user.id),
        ip_address=client_ip(request),
    )
    session.commit()
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    destroy_session(session, token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user
