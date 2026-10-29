"""Proveedores de autenticación (Fase A1).

``AuthProvider`` es la interfaz mínima que consumirá el servicio de sesiones.
En la Fase A1 solo existe ``LocalProvider`` (usuario/contraseña con Argon2id);
``LdapProvider`` se agrega en la Fase A5 sin cambiar este contrato.

El proveedor **nunca** decide la autorización ni crea la sesión: solo responde
"estas credenciales corresponden a este usuario" o ``None``.
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_dummy, verify_password
from app.models import Usuario

__all__ = ["AuthProvider", "LocalProvider", "buscar_usuario"]


def buscar_usuario(db: Session, login: str) -> Usuario | None:
    """Busca un usuario por ``username`` o ``email`` (sin distinguir mayúsculas).

    La comparación replica los índices funcionales ``lower(...)`` del esquema:
    si se buscara con ``==`` una cuenta con mayúsculas quedaría inaccesible.
    """
    clave = (login or "").strip().casefold()
    if not clave:
        return None
    return db.scalar(
        select(Usuario)
        .where(or_(func.lower(Usuario.username) == clave,
                   func.lower(Usuario.email) == clave))
        .order_by(Usuario.id)
        .limit(1)
    )


class AuthProvider(Protocol):
    """Contrato de un proveedor de identidad.

    ``authenticate`` devuelve ``(usuario, needs_rehash)`` o ``None``. El flag
    ``needs_rehash`` permite actualizar el hash cuando suben los parámetros de
    Argon2id; el llamador decide si confirma el cambio.
    """

    nombre: str

    def authenticate(self, db: Session, login: str, password: str,
                     ) -> tuple[Usuario, bool] | None: ...


class LocalProvider:
    """Autenticación local con Argon2id.

    Reglas de seguridad:

    - Si el identificador no existe se verifica contra ``DUMMY_HASH`` para que
      el tiempo de respuesta no revele si la cuenta existe.
    - Un usuario inactivo, sin hash o de otro proveedor también consume el
      tiempo del hash y devuelve ``None``: el mensaje al usuario es genérico.
    - Si el hash quedó con parámetros viejos se re-calcula en el mismo login
      (transparente para el usuario).
    """

    nombre = "local"

    def authenticate(self, db: Session, login: str, password: str,
                     ) -> tuple[Usuario, bool] | None:
        usuario = buscar_usuario(db, login)
        if usuario is None:
            verify_dummy(password or "")
            return None
        if (usuario.auth_provider != self.nombre or not usuario.is_active
                or not usuario.password_hash):
            # Mismo coste que una verificación real, mismo resultado visible.
            verify_dummy(password or "")
            return None
        ok, needs_rehash = verify_password(usuario.password_hash, password or "")
        if not ok:
            return None
        if needs_rehash:
            usuario.password_hash = hash_password(password)
        return usuario, needs_rehash
