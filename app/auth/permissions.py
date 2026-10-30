"""Matriz única de permisos (Fase A2).

Este módulo es la **única fuente de verdad** de la autorización: los routers
aplican :func:`app.auth.dependencies.require_permiso` con las mismas claves que
aquí se declaran y las plantillas consultan el global Jinja ``puede``. Ningún
rol se codifica a mano en un router ni en un template.

Reglas del diseño:

- Un permiso es una acción del negocio (``qaqc.decidir``), nunca una ruta:
  mover una URL no cambia la matriz.
- Un rol desconocido no hereda nada: si la clave no está en :data:`PERMISOS`
  el permiso se niega (cerrado por defecto).
- **Separación de deberes** (análisis §6.4): ``admin`` administra cuentas,
  auditoría y políticas, pero no dictamina fichas ni autoriza reemplazos.
- Con ``EET_AUTH_ENABLED=false`` todo se permite para no romper el
  comportamiento previo a la Fase A1 (rollout gradual).

La UI usa estos helpers solo para **ocultar** lo que el servidor ya bloquea;
la autorización efectiva siempre ocurre en la capa de routers.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings

__all__ = [
    "PERMISOS",
    "ROL_ETIQUETAS",
    "permisos_de",
    "puede",
    "tiene_permiso",
]

#: Matriz rol → permisos. La lista de roles de cada permiso es la permitida.
PERMISOS: dict[str, set[str]] = {
    # Dashboard: todos, pero el asistente solo ve su propio panel.
    "dashboard.ver": {"admin", "supervision", "cargador", "asistente", "consulta"},
    # Entregas: el asistente no entra al listado general, solo a las propias.
    "entregas.ver": {"admin", "supervision", "cargador", "consulta"},
    "entregas.propias": {"asistente"},
    "entregas.gestionar": {"supervision", "cargador"},
    # Cruces y QA/QC: el asistente queda fuera por independencia de la terna.
    "cruces.ver": {"supervision", "cargador", "consulta"},
    "qaqc.ver": {"supervision", "cargador", "consulta"},
    "qaqc.decidir": {"supervision"},
    # Reemplazos (4.5–4.9): proponer ≠ autorizar.
    "reemplazos.ver": {"supervision", "cargador", "consulta"},
    "reemplazos.proponer": {"supervision", "cargador"},
    "reemplazos.autorizar": {"supervision"},
    # Insumos del diseño: lectura amplia, carga operativa.
    "malla.ver": {"admin", "supervision", "cargador", "asistente", "consulta"},
    "malla.cargar": {"supervision", "cargador"},
    "mapa.ver": {"admin", "supervision", "cargador", "asistente", "consulta"},
    "mapa.cargar": {"supervision", "cargador"},
    # Cierre: el consolidado no lo ve el cargador (no exporta resultados).
    "exportar": {"supervision", "consulta"},
    "usuarios.gestionar": {"admin"},
    "auditoria.ver": {"admin", "supervision"},
}

#: Etiquetas de rol para la interfaz (única fuente; el shell la consume).
ROL_ETIQUETAS: dict[str, str] = {
    "admin": "Administración",
    "supervision": "Supervisión",
    "cargador": "Carga",
    "asistente": "Asistente",
    "consulta": "Consulta",
}


def permisos_de(usuario: Any) -> frozenset[str]:
    """Conjunto de permisos efectivos de un usuario (vacío si no hay usuario)."""
    rol = getattr(usuario, "rol", None) if usuario is not None else None
    if not rol:
        return frozenset()
    return frozenset(clave for clave, roles in PERMISOS.items() if rol in roles)


def tiene_permiso(usuario: Any, permiso: str) -> bool:
    """``True`` solo si el rol del usuario está autorizado para ``permiso``."""
    if usuario is None:
        return False
    roles = PERMISOS.get(permiso)
    if not roles:
        return False
    return getattr(usuario, "rol", None) in roles


def puede(request: Any, permiso: str) -> bool:
    """Helper para plantillas: ¿la petición actual puede ejecutar ``permiso``?

    Con la bandera apagada devuelve ``True`` (compatibilidad con el
    comportamiento anterior a la Fase A1). Con la bandera activa lee el usuario
    que dejó el ``AuthGateMiddleware`` en ``request.state``.
    """
    if not settings.auth_enabled:
        return True
    return tiene_permiso(getattr(request.state, "usuario", None), permiso)
