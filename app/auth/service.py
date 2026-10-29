"""Sesiones en servidor, login local y cambio de contraseña (Fase A1).

Diseño:

- El token de sesión (32 bytes) viaja en la cookie; en la base solo se guarda
  su ``sha256``. Quien copie la base no puede reutilizar sesiones.
- Expiración **absoluta** (``session_absolute_min``) fijada al crear la sesión
  e **inactividad** (``session_idle_min``) deslizante sobre ``last_seen_at``,
  que se actualiza como máximo una vez por minuto para no escribir en cada
  petición.
- El login aplica bloqueo por cuenta (``failed_attempts``/``locked_until``),
  resetea el contador al acertar y audita ``login_ok``, ``login_fail`` y
  ``login_locked`` con IP y agente de usuario.
- Usar ``datetime.utcnow()`` porque las columnas ``DateTime`` del esquema son
  *naive* (zona horaria implícita UTC), igual que el resto de la aplicación.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit
from app.auth.providers import AuthProvider, LocalProvider, buscar_usuario
from app.core.config import settings
from app.core.security import (
    hash_password,
    new_token,
    sha256_hex,
    validar_password,
    verify_password,
)
from app.models import LoginAttempt, Sesion, Usuario

__all__ = [
    "ResultadoLogin",
    "autenticar",
    "cambiar_password",
    "cargar_sesion",
    "crear_sesion",
    "limpiar_expiradas",
    "revocar_por_token",
    "revocar_sesion",
    "revocar_todas",
    "sesion_para_csrf",
    "token_csrf_de_sesion",
]

# Tope de intentos registrados por identificador (String(255)).
_MAX_IDENTIFICADOR = 255
# Granularidad del "toque" de last_seen_at.
_INTERVALO_TOQUE = timedelta(minutes=1)


@dataclass(frozen=True)
class ResultadoLogin:
    """Resultado de :func:`autenticar`.

    ``motivo`` es ``"ok"``, ``"credenciales"`` o ``"bloqueado"``. La vista usa
    ``espera_min`` para decir cuánto falta cuando la cuenta está bloqueada; el
    mensaje de credenciales es siempre genérico.
    """

    usuario: Usuario | None = None
    motivo: str = "ok"
    espera_min: int = 0

    @property
    def ok(self) -> bool:
        return self.usuario is not None


def proveedor() -> AuthProvider:
    """Proveedor activo. En A1 solo existe el local; LDAP llega en A5."""
    return LocalProvider()


def _ip_corta(ip: str | None) -> str | None:
    """Ajusta la IP a la columna (``String(45)``, suficiente para IPv6)."""
    return str(ip)[:45] if ip else None


# --- Tokens y cookies ----------------------------------------------------------
def token_csrf_de_sesion(token_hash: str) -> str:
    """Token CSRF derivado de la sesión (HMAC-SHA256, estable por sesión).

    Se deriva del hash del token y de ``EET_SECRET_KEY``: no se guarda una
    columna extra en ``sesiones`` (el esquema de A0 está congelado por sus
    pruebas) y el token cambia al rotar la sesión.
    """
    clave = (settings.secret_key or settings.auth_pepper or "eet-csrf").encode("utf-8")
    mensaje = f"csrf:{token_hash}".encode("utf-8")
    return hmac.new(clave, mensaje, hashlib.sha256).hexdigest()


def _marcar_csrf(sesion: Sesion) -> Sesion:
    """Expone ``sesion.csrf_token`` en memoria (no es una columna)."""
    sesion.csrf_token = token_csrf_de_sesion(sesion.token_hash)
    return sesion


# --- Sesiones ------------------------------------------------------------------
def crear_sesion(db: Session, usuario: Usuario, ip: str | None = None,
                 user_agent: str | None = None) -> tuple[str, Sesion]:
    """Crea una sesión nueva y devuelve ``(token_en_claro, fila)``.

    El token en claro solo existe aquí y en la cookie: nunca se registra ni se
    audita. La rotación (crear una sesión distinta en cada login) evita la
    fijación de sesión.
    """
    if usuario is None or usuario.id is None:
        raise ValueError("No se puede crear una sesión sin usuario persistido.")
    token = new_token(32)
    ahora = datetime.utcnow()
    sesion = Sesion(
        token_hash=sha256_hex(token),
        usuario_id=usuario.id,
        created_at=ahora,
        last_seen_at=ahora,
        expires_at=ahora + timedelta(minutes=int(settings.session_absolute_min)),
        ip=_ip_corta(ip),
        user_agent=(user_agent or "")[:300] or None,
    )
    db.add(sesion)
    db.commit()
    return token, _marcar_csrf(sesion)


def cargar_sesion(db: Session, token: str | None) -> Sesion | None:
    """Devuelve la sesión válida del token o ``None``.

    Una sesión es válida si el hash coincide, no está revocada, no superó la
    vida absoluta y no superó la inactividad permitida.
    """
    if not token:
        return None
    sesion = db.scalar(
        select(Sesion).where(Sesion.token_hash == sha256_hex(token))
    )
    if sesion is None or sesion.revoked_at is not None:
        return None
    ahora = datetime.utcnow()
    if sesion.expires_at is None or sesion.expires_at <= ahora:
        return None
    ultimo = sesion.last_seen_at or sesion.created_at or ahora
    if ultimo + timedelta(minutes=int(settings.session_idle_min)) <= ahora:
        return None
    if ahora - ultimo >= _INTERVALO_TOQUE:
        sesion.last_seen_at = ahora
        db.commit()
    return _marcar_csrf(sesion)


def sesion_para_csrf(db: Session, token: str | None) -> Sesion | None:
    """Sesión del token aunque haya expirado o esté revocada.

    Se usa solo para validar el CSRF del logout: el formulario se renderizó con
    el token de esa sesión y debe poder cerrarse aunque haya caducado. No
    autentica nada: ``request.state.usuario`` sigue vacío para esa petición.
    """
    if not token:
        return None
    sesion = db.scalar(
        select(Sesion).where(Sesion.token_hash == sha256_hex(token))
    )
    return _marcar_csrf(sesion) if sesion is not None else None


def revocar_sesion(db: Session, sesion: Sesion) -> None:
    """Revoca una sesión concreta (idempotente)."""
    if sesion is None:
        return
    if sesion.revoked_at is None:
        sesion.revoked_at = datetime.utcnow()
        db.commit()


def revocar_por_token(db: Session, token: str | None) -> int:
    """Revoca por token sin cargar el objeto (útil en el logout)."""
    if not token:
        return 0
    resultado = db.execute(
        update(Sesion)
        .where(Sesion.token_hash == sha256_hex(token), Sesion.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )
    db.commit()
    return int(resultado.rowcount or 0)


def revocar_todas(db: Session, usuario_id: int, excepto_id=None) -> int:
    """Revoca todas las sesiones del usuario salvo, si se indica, una."""
    consulta = update(Sesion).where(
        Sesion.usuario_id == usuario_id, Sesion.revoked_at.is_(None))
    if excepto_id is not None:
        consulta = consulta.where(Sesion.id != excepto_id)
    resultado = db.execute(consulta.values(revoked_at=datetime.utcnow()))
    db.commit()
    return int(resultado.rowcount or 0)


def limpiar_expiradas(db: Session, dias: int = 30) -> int:
    """Borra sesiones muertas hace más de ``dias`` (tarea de mantenimiento).

    Elimina expiradas, revocadas e inactivas antiguas. Nunca toca una sesión
    viva: el umbral se mide contra el momento en que dejó de ser válida.
    """
    limite = datetime.utcnow() - timedelta(days=max(0, int(dias)))
    resultado = db.execute(
        delete(Sesion).where(
            or_(
                Sesion.expires_at < limite,
                Sesion.revoked_at < limite,
                Sesion.last_seen_at < limite,
            )
        )
    )
    db.commit()
    return int(resultado.rowcount or 0)


# --- Login ---------------------------------------------------------------------
def _registrar_intento(db: Session, identificador: str, ip: str | None,
                       exito: bool, factor: str = "password") -> None:
    db.add(LoginAttempt(
        identificador=(identificador or "")[:_MAX_IDENTIFICADOR],
        ip=_ip_corta(ip),
        exito=exito,
        factor=factor,
    ))


def autenticar(db: Session, login: str, password: str, ip: str | None = None,
               user_agent: str | None = None) -> ResultadoLogin:
    """Autentica usuario/contraseña aplicando bloqueo por cuenta y auditoría.

    Nunca distingue "usuario inexistente" de "contraseña incorrecta": ambos
    casos devuelven ``motivo="credenciales"`` con el mismo coste de tiempo.
    """
    identificador = (login or "").strip().casefold()
    ahora = datetime.utcnow()
    usuario = buscar_usuario(db, identificador)

    # 1) Bloqueo vigente: ni siquiera se verifica la contraseña.
    if usuario is not None and usuario.locked_until is not None:
        if usuario.locked_until > ahora:
            espera = max(1, ceil((usuario.locked_until - ahora).total_seconds() / 60))
            _registrar_intento(db, identificador, ip, exito=False)
            audit.registrar(
                db, audit.actor_de(usuario), "login_locked", entidad="usuario",
                entidad_id=usuario.id,
                detalle={"motivo": "bloqueo_vigente", "espera_min": espera},
                ip=ip, user_agent=user_agent,
            )
            db.commit()
            return ResultadoLogin(motivo="bloqueado", espera_min=espera)
        # Bloqueo vencido: se abre un ciclo nuevo de intentos.
        usuario.locked_until = None
        usuario.failed_attempts = 0
        db.flush()

    # 2) Verificación de credenciales (con el hash de relleno si no existe).
    comprobado = proveedor().authenticate(db, identificador, password or "")

    if comprobado is None:
        accion = "login_fail"
        espera = 0
        if usuario is not None and usuario.auth_provider == "local":
            usuario.failed_attempts = (usuario.failed_attempts or 0) + 1
            if usuario.failed_attempts >= int(settings.login_max_intentos):
                usuario.locked_until = ahora + timedelta(
                    minutes=int(settings.login_bloqueo_min))
                usuario.failed_attempts = 0
                espera = int(settings.login_bloqueo_min)
                accion = "login_locked"
        _registrar_intento(db, identificador, ip, exito=False)
        audit.registrar(
            db, audit.actor_de(usuario), accion, entidad="usuario",
            entidad_id=usuario.id if usuario is not None else None,
            detalle={"identificador": identificador,
                     "motivo": "bloqueado" if espera else "credenciales_invalidas",
                     "espera_min": espera},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoLogin(
            motivo="bloqueado" if espera else "credenciales", espera_min=espera)

    # 3) Éxito: limpiar el bloqueo y dejar rastro del acceso.
    usuario_db, rehash = comprobado
    usuario_db.failed_attempts = 0
    usuario_db.locked_until = None
    usuario_db.last_login_at = ahora
    _registrar_intento(db, identificador, ip, exito=True)
    audit.registrar(
        db, audit.actor_de(usuario_db), "login_ok", entidad="usuario",
        entidad_id=usuario_db.id, detalle={"parametros_actualizados": bool(rehash)},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return ResultadoLogin(usuario=usuario_db, motivo="ok")


# --- Cambio de contraseña ------------------------------------------------------
def cambiar_password(db: Session, usuario: Usuario, actual: str, nueva: str,
                     confirmacion: str, *, sesion_actual=None,
                     ip: str | None = None,
                     user_agent: str | None = None) -> list[str]:
    """Valida y aplica un cambio de contraseña.

    Devuelve la lista de incumplimientos (vacía = cambio aplicado). La sesión
    en curso se conserva; todas las demás del usuario se revocan porque un
    cambio de contraseña debe invalidar el acceso robado.
    """
    if usuario is None:
        return ["La sesión no es válida."]
    if usuario.auth_provider != "local" or not usuario.password_hash:
        return ["La contraseña de esta cuenta se administra fuera del sistema."]

    errores: list[str] = []
    ok, _ = verify_password(usuario.password_hash, actual or "")
    if not ok:
        errores.append("La contraseña actual no es correcta.")
    if (nueva or "") != (confirmacion or ""):
        errores.append("La nueva contraseña y su confirmación no coinciden.")
    if ok and (nueva or "") and verify_password(usuario.password_hash, nueva)[0]:
        errores.append("La nueva contraseña debe ser distinta de la actual.")
    errores.extend(validar_password(nueva or "", usuario.username, usuario.email))
    if errores:
        return errores

    usuario.password_hash = hash_password(nueva)
    usuario.password_changed_at = datetime.utcnow()
    usuario.must_change_password = False
    db.flush()
    revocadas = revocar_todas(db, usuario.id, excepto_id=sesion_actual)
    audit.registrar(
        db, audit.actor_de(usuario), "password_changed", entidad="usuario",
        entidad_id=usuario.id, detalle={"sesiones_revocadas": revocadas},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return []
