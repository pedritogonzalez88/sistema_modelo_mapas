# ruff: noqa: F405
"""Proveedores de autenticación: fachada de compatibilidad."""
from app.auth.ldap import *  # noqa: F401,F403
from app.auth.providers_common import *  # noqa: F401,F403

__all__ = [
    "AuthProvider",
    "DiagnosticoLdap",
    "LdapProvider",
    "LocalProvider",
    "MOTIVOS_LDAP",
    "PasoDiagnostico",
    "ResultadoLdap",
    "ROLES_PRIORIDAD",
    "buscar_usuario",
    "cn_de",
    "configuracion_faltante",
    "construir_filtro",
    "elegir_rol",
    "fabrica_por_defecto",
    "normalizar_mapa",
]
