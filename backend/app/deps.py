"""Dependências compartilhadas dos endpoints."""

from __future__ import annotations

from fastapi import Request


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
