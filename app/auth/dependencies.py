"""Dependencias de identidad para los routers.

En la Fase A1 el ``AuthGateMiddleware`` ya deja el usuario en
``request.state``; estas dependencias son el punto de entrada tipado para los
routers y para la Fase A2 (autorización por rol).

``require_roles`` se entrega con la firma definitiva aunque todavía no se
aplique en ninguna ruta: la matriz de permisos es la Fase A2 y su lugar natural
es ``app/auth/permissions.py``.
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request

from app.core.config import settings
from app.models import Sesion, Usuario

__all__ = ["get_current_user", "get_current_session", "require_roles"]

_MENSAJE_SIN_SESION = "Sesión requerida."


def get_current_user(request: Request) -> Usuario:
    """Usuario autenticado de la petición actual."""
    usuario = getattr(request.state, "usuario", None)
    if usuario is None:
        raise HTTPException(status_code=401, detail=_MENSAJE_SIN_SESION)
    return usuario


def get_current_session(request: Request) -> Sesion:
    """Sesión de servidor asociada a la cookie actual."""
    sesion = getattr(request.state, "sesion", None)
    if sesion is None:
        raise HTTPException(status_code=401, detail=_MENSAJE_SIN_SESION)
    return sesion


def require_roles(*roles: str) -> Callable[..., Usuario]:
    """Dependencia que exige sesión válida y, si se indican, un rol permitido.

    Con la bandera apagada no hay usuario en ``request.state``: la dependencia
    responde 401 en lugar de asumir un rol, para que activarla por error no
    degrade los permisos.
    """
    permitidos = frozenset(roles)

    def _dependencia(request: Request) -> Usuario:
        if not settings.auth_enabled:
            raise HTTPException(status_code=401, detail=_MENSAJE_SIN_SESION)
        usuario = get_current_user(request)
        if permitidos and usuario.rol not in permitidos:
            raise HTTPException(status_code=403, detail="No tiene permiso para esta acción.")
        return usuario

    return _dependencia
