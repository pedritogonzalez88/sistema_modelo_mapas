"""Datos semilla de las cuentas nominales del sistema.

Este módulo es la **única fuente de verdad** de las seis personas que operan
el Validador EET (terna de asistentes, responsable de carga, supervisión y
administración) y de la política de segundo factor que corresponde a cada rol.

Lo consumen:

- :func:`app.services.persist.seed`, que crea las cuentas en una base vacía;
- ``python -m app.cli seed-usuarios``, que las completa o actualiza sobre una
  base que ya tiene filas genéricas.

No importa modelos ni configuración: es una tabla de datos pura y se puede
probar sin base de datos.

Reglas:

- El rol ``asistente`` exige ``letra_terna`` (A/B/C); el resto de roles la
  dejan en ``None``.
- Los roles críticos (``admin`` y ``supervision``) usan TOTP obligatorio; el
  resto admite cualquiera de los dos factores (``any``).
"""
from __future__ import annotations

#: Roles con TOTP obligatorio por política (análisis de autenticación §2 y §6.4).
ROLES_CON_TOTP = ("admin", "supervision")

#: Política por defecto del resto de roles: TOTP o correo, a elección.
MFA_POLICY_POR_DEFECTO = "any"

#: Las seis cuentas nominales, en el orden de las filas semilla históricas.
USUARIOS_SEMILLA: tuple[dict, ...] = (
    {
        "nombre": "Juan Pérez",
        "username": "juan.perez",
        "email": "juan.perez@aep.gov.py",
        "rol": "asistente",
        "letra_terna": "A",
    },
    {
        "nombre": "María González",
        "username": "maria.gonzalez",
        "email": "maria.gonzalez@aep.gov.py",
        "rol": "asistente",
        "letra_terna": "B",
    },
    {
        "nombre": "Carlos Benítez",
        "username": "carlos.benitez",
        "email": "carlos.benitez@aep.gov.py",
        "rol": "asistente",
        "letra_terna": "C",
    },
    {
        "nombre": "Lucía Fernández",
        "username": "lucia.fernandez",
        "email": "lucia.fernandez@aep.gov.py",
        "rol": "cargador",
        "letra_terna": None,
    },
    {
        "nombre": "Roberto Giménez",
        "username": "roberto.gimenez",
        "email": "roberto.gimenez@aep.gov.py",
        "rol": "supervision",
        "letra_terna": None,
    },
    {
        "nombre": "Daniel Meza",
        "username": "daniel.meza",
        "email": "daniel.meza@aep.gov.py",
        "rol": "admin",
        "letra_terna": None,
    },
)


def mfa_policy_para_rol(rol: str | None) -> str:
    """Política de segundo factor que impone el rol.

    ``totp`` para los roles críticos y ``any`` para el resto. Un rol
    desconocido o vacío cae en ``any``: nunca se degrada a ``disabled``.
    """
    return ("totp" if (rol or "").strip().casefold() in ROLES_CON_TOTP
            else MFA_POLICY_POR_DEFECTO)
