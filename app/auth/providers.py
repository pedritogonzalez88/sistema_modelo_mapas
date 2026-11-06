"""Proveedores de autenticación: fachada de compatibilidad."""
from app.auth.ldap import *
from app.auth.providers_common import *

__all__ = [
    "MOTIVOS_LDAP",
    "ROLES_PRIORIDAD",
    "AuthProvider",
    "DiagnosticoLdap",
    "LdapProvider",
    "LocalProvider",
    "PasoDiagnostico",
    "ResultadoLdap",
    "buscar_usuario",
    "cn_de",
    "configuracion_faltante",
    "construir_filtro",
    "elegir_rol",
    "fabrica_por_defecto",
    "normalizar_mapa",
]
