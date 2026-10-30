"""Dependencias de identidad y autorización para los routers.

En la Fase A1 el ``AuthGateMiddleware`` deja el usuario en ``request.state``;
estas dependencias son el punto de entrada tipado para los routers y, desde la
Fase A2, el único lugar donde se decide si una petición puede ejecutar una
acción.

Contrato de :func:`require_permiso`:

- ``EET_AUTH_ENABLED=false`` → deja pasar (``None``): la aplicación se comporta
  como antes de la Fase A1 y los tests históricos siguen verdes.
- Con la bandera activa y sin sesión, el gate ya redirigió; si aun así no hay
  usuario se responde 401.
- Con usuario sin el permiso se responde ``403`` con el mensaje de negocio y se
  audita ``acceso_denegado`` (ruta, método y rol; nunca tokens ni datos de
  negocio).

Varios permisos en la misma dependencia se interpretan como **alternativa**
(basta uno), que es lo que necesita ``/entregas`` (``entregas.ver`` *o*
``entregas.propias``).
"""
from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import NoReturn

from fastapi import HTTPException, Request

from app.auth import audit
from app.auth.permissions import PERMISOS, tiene_permiso
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Sesion, Usuario

__all__ = [
    "MENSAJE_RED_INTERNA",
    "MENSAJE_SIN_PERMISO",
    "exigir_permiso",
    "get_current_session",
    "get_current_user",
    "negar_acceso",
    "require_permiso",
    "require_roles",
    "requiere_red_interna",
]

_MENSAJE_SIN_SESION = "Sesión requerida."
MENSAJE_SIN_PERMISO = "No tiene permiso para esta acción."
MENSAJE_RED_INTERNA = "Esta sección solo está disponible desde la red interna."


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


def _registrar_denegacion(request: Request, usuario: Usuario | None,
                          permiso: str, roles: set[str],
                          extra: dict | None = None) -> None:
    """Audita un acceso denegado en una transacción corta e independiente.

    Se abre una sesión propia (y no la del router) porque el 403 interrumpe la
    petición antes de llegar al endpoint: no hay unidad de trabajo abierta. Un
    fallo al auditar nunca debe enmascarar el 403, de ahí el ``except``.
    """
    detalle = {
        "ruta": request.url.path,
        "metodo": request.method,
        "permiso": permiso,
        "rol": getattr(usuario, "rol", None),
        "roles_autorizados": sorted(roles),
    }
    if extra:
        detalle.update(extra)
    try:
        with SessionLocal() as db:
            audit.registrar(
                db,
                audit.actor_de(usuario),
                "acceso_denegado",
                entidad="permiso",
                entidad_id=permiso,
                detalle=detalle,
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
            db.commit()
    except Exception:  # pragma: no cover - la auditoría no debe romper el 403
        return


def negar_acceso(request: Request, usuario: Usuario | None, permiso: str,
                 extra: dict | None = None) -> NoReturn:
    """Audita y responde 403 con el mensaje de negocio.

    Se usa cuando la denegación no depende solo del rol, sino del dato (por
    ejemplo, un asistente que pide la entrega de otra letra).
    """
    _registrar_denegacion(request, usuario, permiso,
                          set(PERMISOS.get(permiso, ())), extra)
    raise HTTPException(status_code=403, detail=MENSAJE_SIN_PERMISO)


def exigir_permiso(request: Request, *permisos: str) -> Usuario | None:
    """Valida permisos fuera de una dependencia (p. ej. según el formulario)."""
    if not settings.auth_enabled:
        return None
    usuario = get_current_user(request)
    if any(tiene_permiso(usuario, permiso) for permiso in permisos):
        return usuario
    principal = permisos[0] if permisos else ""
    roles: set[str] = set()
    for permiso in permisos:
        roles |= set(PERMISOS.get(permiso, ()))
    _registrar_denegacion(request, usuario, principal, roles)
    raise HTTPException(status_code=403, detail=MENSAJE_SIN_PERMISO)


def require_permiso(*permisos: str) -> Callable[..., Usuario | None]:
    """Dependencia FastAPI que exige al menos uno de ``permisos``.

    ``@router.get(..., dependencies=[Depends(require_permiso("qaqc.ver"))])``
    protege la lectura; la misma dependencia se usa por endpoint para las
    acciones sensibles.
    """
    exigidos = tuple(permisos)

    def _dependencia(request: Request) -> Usuario | None:
        return exigir_permiso(request, *exigidos)

    return _dependencia


def require_roles(*roles: str) -> Callable[..., Usuario]:
    """Dependencia por rol, conservada por compatibilidad con la Fase A1.

    La autorización nueva se expresa con :func:`require_permiso`; esta función
    se mantiene para llamadas existentes y para pruebas que la ejercitan.
    """
    permitidos = frozenset(roles)

    def _dependencia(request: Request) -> Usuario:
        if not settings.auth_enabled:
            raise HTTPException(status_code=401, detail=_MENSAJE_SIN_SESION)
        usuario = get_current_user(request)
        if permitidos and usuario.rol not in permitidos:
            raise HTTPException(status_code=403, detail=MENSAJE_SIN_PERMISO)
        return usuario

    return _dependencia


def requiere_red_interna(request: Request) -> None:
    """Restringe la administración y la auditoría a la red interna.

    Se aplica **solo** cuando la autenticación está activa y
    ``EET_ADMIN_INTERNAL_ONLY=true``; con la bandera de autenticación apagada
    la aplicación conserva el comportamiento anterior a la Fase A1.

    La IP se toma de ``request.client.host``. **Detrás de un reverse proxy hay
    que definir ``EET_TRUSTED_PROXY_IPS``** (Fase A6a) para que uvicorn
    reescriba el cliente con ``X-Forwarded-For``; sin esa configuración la IP
    visible sería la del proxy y todos los accesos quedarían fuera (o dentro)
    del rango interno por igual.

    Un host que no sea una dirección IP (por ejemplo el ``testclient`` de
    Starlette) se rechaza: con un CIDR amplio como ``0.0.0.0/0`` solo debe
    pasar una IP real perteneciente a esa red.
    """
    if not settings.auth_enabled or not settings.admin_internal_only:
        return

    host = request.client.host if request.client else None
    try:
        direccion = ipaddress.ip_address((host or "").strip())
    except ValueError:
        _registrar_denegacion(
            request, getattr(request.state, "usuario", None),
            "admin.red_interna", set(),
            {"motivo": "ip_no_parseable", "host": host},
        )
        raise HTTPException(
            status_code=403, detail=MENSAJE_RED_INTERNA) from None

    # Un socket dual-stack puede entregar IPv4 mapeada (``::ffff:10.x.x.x``):
    # se compara como IPv4 para que los CIDR internos sigan siendo válidos.
    if isinstance(direccion, ipaddress.IPv6Address) and direccion.ipv4_mapped:
        direccion = direccion.ipv4_mapped

    if not any(direccion in red for red in settings.internal_networks):
        _registrar_denegacion(
            request, getattr(request.state, "usuario", None),
            "admin.red_interna", set(),
            {"motivo": "fuera_de_rango", "host": host},
        )
        raise HTTPException(status_code=403, detail=MENSAJE_RED_INTERNA)
