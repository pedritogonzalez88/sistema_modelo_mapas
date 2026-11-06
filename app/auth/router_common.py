"""Utilidades compartidas por los routers de autenticación."""
from fastapi import Request
from fastapi.responses import HTMLResponse


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _agente(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _sin_cache(respuesta: HTMLResponse) -> HTMLResponse:
    """``no-store`` para pantallas con datos de un solo uso o de sesión."""
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta
