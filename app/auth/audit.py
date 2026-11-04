"""Auditoría con actor real (Fase A1).

Reglas:

- ``actor`` es ``"usuario:<id>"``, ``"cli"`` o ``"sistema"``. Nunca un rol
  fijo: la trazabilidad exige saber *quién* hizo la acción.
- Nunca se guardan contraseñas, hashes, tokens, cookies ni secretos. Además de
  la disciplina en las llamadas, :func:`_detalle_seguro` elimina cualquier
  clave sospechosa del detalle como defensa en profundidad.
- ``registrar`` hace ``flush`` pero **no** confirma: la transacción la cierra el
  caso de uso (login, cambio de contraseña, CLI) para que el evento y el cambio
  de datos sean atómicos.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Auditoria, Usuario

ACTOR_CLI = "cli"
ACTOR_SISTEMA = "sistema"

# Fragmentos de nombre de campo que delatan un secreto: si aparecen, el valor
# se descarta del detalle aunque el llamador lo haya incluido por error.
_CLAVES_RESERVADAS = (
    "password", "contrasena", "contraseña", "passwd",
    "hash", "token", "secret", "secreto", "cookie", "csrf", "pepper",
)

# Tope del modelo (String(300)) para el agente de usuario.
_MAX_USER_AGENT = 300


def actor_de(usuario: Usuario | None) -> str:
    """Actor canónico de una fila de usuario (``usuario:<id>``)."""
    identificador = getattr(usuario, "id", None)
    return f"usuario:{identificador}" if identificador is not None else ACTOR_SISTEMA


def actor_de_request(request) -> str:
    """Actor de la petición actual; ``sistema`` si no hay sesión."""
    estado = getattr(request, "state", None)
    return actor_de(getattr(estado, "usuario", None) if estado is not None else None)


def _clave_reservada(clave: str) -> bool:
    minuscula = str(clave).casefold()
    return any(fragmento in minuscula for fragmento in _CLAVES_RESERVADAS)


def _detalle_seguro(detalle: Any) -> dict:
    """Copia del detalle sin claves sensibles y con valores serializables."""
    if not isinstance(detalle, dict):
        return {}
    limpio: dict = {}
    for clave, valor in detalle.items():
        if _clave_reservada(clave):
            continue
        if isinstance(valor, dict):
            limpio[str(clave)] = _detalle_seguro(valor)
        elif isinstance(valor, (list, tuple)):
            limpio[str(clave)] = [
                _detalle_seguro(item) if isinstance(item, dict) else item
                for item in valor
            ]
        else:
            limpio[str(clave)] = valor
    return limpio


def registrar(db: Session, actor: str | None, accion: str, entidad: str | None = None,
              entidad_id: Any = None, detalle: dict | None = None,
              ip: str | None = None, user_agent: str | None = None) -> Auditoria:
    """Agrega una fila de auditoría y la deja lista para confirmar.

    ``entidad`` es obligatoria en el modelo; se usa ``""`` cuando la acción no
    tiene una entidad concreta (por ejemplo, un intento de login fallido).
    """
    if not accion:
        raise ValueError("La acción de auditoría no puede estar vacía.")
    ip_limpia = str(ip)[:45] if ip else None
    agente_limpio = (user_agent or "")[: _MAX_USER_AGENT] or None
    fila = Auditoria(
        actor=(actor or ACTOR_SISTEMA)[:200],
        accion=accion[:100],
        entidad=(entidad or "")[:100],
        entidad_id=None if entidad_id is None else str(entidad_id)[:100],
        detalle=_detalle_seguro(detalle),
        ip=ip_limpia,
        user_agent=agente_limpio,
    )
    db.add(fila)
    db.flush()
    return fila
