"""Sesiones en servidor, login local, cambio de contraseña y MFA (A1 y A3).

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
- Usar :func:`app.core.security.utcnow` (UTC *naive*, sin ``DeprecationWarning``)
  porque las columnas ``DateTime`` del esquema son *naive* (zona horaria
  implícita UTC), igual que el resto de la aplicación.

Fase A3 (segundo factor):

- El secreto TOTP se guarda cifrado con :class:`app.core.security.SecretBox`
  (``secret_enc`` + ``key_id``) y jamás se registra ni se devuelve después del
  enrolamiento.
- El challenge posterior a la contraseña es una fila de ``auth_tokens`` con
  ``purpose='mfa'``: solo se guarda el ``sha256`` del token que viaja en la
  cookie ``eet_mfa``, expira en 5 minutos y admite 5 intentos.
- ``mfa_totp.last_used_step`` impide reutilizar un código ya aceptado (replay)
  incluso dentro de la ventana de ±1 paso.
- Los códigos de respaldo se guardan como ``hmac_code(código, usuario, pepper)``,
  se muestran una sola vez y se consumen con ``used_at``.

Fase A4 (correo y recuperación):

- El código de 6 dígitos viaja solo por correo. En la base queda
  ``hmac_code(código, usuario, pepper)`` dentro de ``mfa_email_codes``, con
  10 minutos de vida y 5 intentos. Un código nuevo invalida los anteriores.
- El enlace de restablecimiento es un token de 32 bytes: en ``auth_tokens``
  solo se guarda su ``sha256`` (purpose ``reset``), vence a los 30 minutos, se
  consume una vez y revoca todas las sesiones al aplicarse.
- ``solicitar_reset`` responde siempre igual (sin enumeración) y aplica límites
  de 3 solicitudes por hora por cuenta y 10 por IP. Los usuarios LDAP nunca se
  restablecen localmente y tampoco se delatan.
- El cuerpo sensible de cada correo se cifra en ``email_outbox`` (ver
  :mod:`app.auth.mailer`).

Fase A5 (LDAP/AD):

- El proveedor se elige por ``usuarios.auth_provider``. Las cuentas ``ldap``
  pasan por :class:`app.auth.providers.LdapProvider` solo si
  ``EET_LDAP_ENABLED=true``; con la bandera apagada el intento falla de forma
  genérica y queda auditado como ``ldap_disabled`` (no se abre red).
- Un fallo de LDAP **no** cae a autenticación local (*fail closed*). El bloqueo
  por cuenta sigue siendo local: AD administra sus propias políticas de
  contraseña y permitir bloquear cuentas de dominio desde aquí sería un vector
  de denegación de servicio. Todos los intentos quedan en ``login_attempts``.
- Las cuentas LDAP no usan Argon2id; para no delatar por tiempo un fallo muy
  rápido se aplica un retardo mínimo (:data:`RETARDO_MINIMO_LDAP`).
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import time
from html import escape
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from urllib.parse import quote
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit, mailer
from app.auth.providers import (
    AuthProvider,
    LdapProvider,
    LocalProvider,
    buscar_usuario,
)
from app.core.config import settings
from app.core.security import (
    constant_time_eq,
    generar_codigos_respaldo,
    generar_secreto,
    hash_password,
    hmac_code,
    new_token,
    normalizar_codigo,
    secret_box,
    sha256_hex,
    totp_uri,
    utcnow,
    validar_password,
    verificar_totp,
    verify_password,
)
from app.models import (
    Auditoria,
    AuthToken,
    LoginAttempt,
    MfaEmailCode,
    MfaTotp,
    RecoveryCode,
    Sesion,
    Usuario,
)

__all__ = [
    "CODIGOS_RESPALDO",
    "MAX_ENVIOS_CORREO_HORA",
    "MAX_ENVIOS_CORREO_POR_IP_HORA",
    "MAX_FALLOS_MFA_POR_IP",
    "MAX_FILAS_CSV_AUDITORIA",
    "MAX_INTENTOS_CODIGO_CORREO",
    "MAX_INTENTOS_MFA",
    "MAX_RESET_POR_IP_HORA",
    "MAX_RESET_POR_USUARIO_HORA",
    "LETRAS_TERNA",
    "MFA_POR_ROL",
    "PAGINA_AUDITORIA",
    "PAGINA_USUARIOS",
    "POLITICAS_MFA",
    "ROLES_USUARIO",
    "VENTANA_RATE_LIMIT",
    "EstadoMfa",
    "PaginaAuditoria",
    "PaginaUsuarios",
    "ResultadoAlta",
    "ResultadoLogin",
    "ResultadoMfa",
    "autenticar",
    "cambiar_estado_usuario",
    "cambiar_password",
    "cambiar_rol_usuario",
    "cargar_desafio",
    "cargar_sesion",
    "codigo_email_vigente",
    "codigos_restantes",
    "confirmar_enrolamiento",
    "crear_desafio",
    "crear_sesion",
    "crear_usuario_admin",
    "emitir_codigo_email",
    "estado_mfa",
    "exportar_auditoria",
    "fallos_por_ip",
    "generar_codigo_email",
    "generar_password_temporal",
    "iniciar_enrolamiento",
    "ip_bloqueada",
    "limpiar_expiradas",
    "listar_auditoria",
    "listar_usuarios",
    "mfa_etiqueta",
    "puede_enviar_codigo",
    "puede_enviar_codigo_por_ip",
    "regenerar_respaldo",
    "reset_mfa_usuario",
    "reset_password_usuario",
    "restablecer_password",
    "revocar_por_token",
    "revocar_sesion",
    "revocar_sesion_de",
    "revocar_todas",
    "sesion_para_csrf",
    "sesiones_activas",
    "solicitar_reset",
    "token_csrf_de_sesion",
    "validar_token_reset",
    "verificar_desafio",
    "verificar_desafio_email",
]

# Tope de intentos registrados por identificador (String(255)).
_MAX_IDENTIFICADOR = 255
# Granularidad del "toque" de last_seen_at.
_INTERVALO_TOQUE = timedelta(minutes=1)

#: Políticas de segundo factor admitidas en ``usuarios.mfa_policy``.
POLITICAS_MFA = ("disabled", "email", "totp", "any")
#: Intentos permitidos en el challenge antes de invalidarlo.
MAX_INTENTOS_MFA = 5
#: Vida del challenge posterior a la contraseña.
VIGENCIA_DESAFIO = timedelta(minutes=5)
#: Códigos de respaldo emitidos en cada enrolamiento o regeneración.
CODIGOS_RESPALDO = 10

#: Vida del código enviado por correo (Fase A4).
VIGENCIA_CODIGO_CORREO = timedelta(minutes=10)
#: Intentos admitidos por código de correo antes de invalidarlo.
MAX_INTENTOS_CODIGO_CORREO = 5
#: Espera mínima entre envíos de códigos.
COOLDOWN_ENVIO_CORREO = timedelta(seconds=60)
#: Códigos máximos por usuario en una hora.
MAX_ENVIOS_CORREO_HORA = 5

#: Vida del enlace de restablecimiento de contraseña.
VIGENCIA_RESET = timedelta(minutes=30)
#: Solicitudes de restablecimiento por usuario en una hora.
MAX_RESET_POR_USUARIO_HORA = 3
#: Solicitudes de restablecimiento por IP en una hora.
MAX_RESET_POR_IP_HORA = 10

#: Ventana común del rate limiting por IP (Fase A6a).
VENTANA_RATE_LIMIT = timedelta(minutes=15)
#: Fallos de segundo factor por IP en la ventana antes de cortar el acceso.
MAX_FALLOS_MFA_POR_IP = 10
#: Códigos por hora que se pueden pedir desde una misma IP.
MAX_ENVIOS_CORREO_POR_IP_HORA = 10

#: Retardo mínimo de un intento LDAP fallido. Argon2id tarda decenas de ms;
#: un bind que falla en 1 ms delataría que la cuenta no existe o que el DC no
#: respondió. En pruebas se pone en 0 con ``monkeypatch``.
RETARDO_MINIMO_LDAP = 0.25

#: Texto neutro de la recuperación: idéntico exista o no la cuenta.
RESPUESTA_RECUPERACION = (
    "Si la cuenta existe, enviamos instrucciones al correo registrado."
)


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


@dataclass(frozen=True)
class EstadoMfa:
    """Estado del segundo factor de un usuario (Fases A3 y A4).

    ``requiere_desafio`` es el caso "hay un segundo factor viable y la política
    lo exige": el login no crea sesión y pasa por ``/auth/mfa``.
    ``metodo`` indica el factor por defecto: ``"totp"`` (autenticador) o
    ``"email"`` (código al correo). Puede quedar ``None`` cuando no hay canal
    disponible (sin TOTP y sin correo), en cuyo caso no se bloquea el acceso.
    ``requiere_enrolamiento`` es el caso "su política exige TOTP y todavía no
    se enroló": el login crea sesión, pero el gate solo deja ver
    ``/auth/seguridad`` hasta completar la activación.
    """

    policy: str = "any"
    enrolado: bool = False
    requiere_desafio: bool = False
    requiere_enrolamiento: bool = False
    #: Fecha del enrolamiento en formato legible (solo para la vista).
    enrolado_desde: str | None = None
    #: Métodos realmente disponibles para este usuario.
    puede_totp: bool = False
    puede_email: bool = False
    #: Factor que pide el login: ``"totp"``, ``"email"`` o ``None``.
    metodo: str | None = None


@dataclass(frozen=True)
class ResultadoMfa:
    """Resultado de :func:`verificar_desafio`.

    ``motivo`` es ``"ok"``, ``"invalido"``, ``"limite"`` o ``"ausente"``.
    ``metodo`` distingue ``"totp"`` de ``"respaldo"`` para la auditoría.
    """

    motivo: str = "ausente"
    usuario: Usuario | None = None
    metodo: str | None = None

    @property
    def ok(self) -> bool:
        return self.motivo == "ok" and self.usuario is not None


def proveedor(usuario: Usuario | None = None) -> AuthProvider:
    """Proveedor que corresponde al intento de login (Fase A5).

    - ``usuario.auth_provider == "ldap"`` y LDAP activo → ``LdapProvider``.
    - Login inexistente con ``ldap_auto_provision`` y LDAP activo → ``LdapProvider``.
    - Cualquier otro caso → ``LocalProvider`` (incluye las cuentas LDAP cuando
      la bandera está apagada: se falla genérico **sin** instanciar LDAP).
    """
    if not settings.ldap_enabled:
        return LocalProvider()
    if usuario is not None:
        return LdapProvider() if usuario.auth_provider == "ldap" else LocalProvider()
    if settings.ldap_auto_provision:
        return LdapProvider()
    return LocalProvider()


def _esperar_retardo_ldap(inicio: float) -> None:
    """Completa el tiempo mínimo de un fallo LDAP (si fue demasiado rápido)."""
    if RETARDO_MINIMO_LDAP <= 0:
        return
    restante = RETARDO_MINIMO_LDAP - (time.monotonic() - inicio)
    if restante > 0:
        time.sleep(restante)


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
                 user_agent: str | None = None,
                 mfa_at: datetime | None = None) -> tuple[str, Sesion]:
    """Crea una sesión nueva y devuelve ``(token_en_claro, fila)``.

    El token en claro solo existe aquí y en la cookie: nunca se registra ni se
    audita. La rotación (crear una sesión distinta en cada login) evita la
    fijación de sesión. ``mfa_at`` se completa cuando la sesión nace después de
    superar el segundo factor.
    """
    if usuario is None or usuario.id is None:
        raise ValueError("No se puede crear una sesión sin usuario persistido.")
    token = new_token(32)
    ahora = utcnow()
    sesion = Sesion(
        token_hash=sha256_hex(token),
        usuario_id=usuario.id,
        created_at=ahora,
        last_seen_at=ahora,
        expires_at=ahora + timedelta(minutes=int(settings.session_absolute_min)),
        mfa_at=mfa_at,
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
    ahora = utcnow()
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
        sesion.revoked_at = utcnow()
        db.commit()


def revocar_por_token(db: Session, token: str | None) -> int:
    """Revoca por token sin cargar el objeto (útil en el logout)."""
    if not token:
        return 0
    resultado = db.execute(
        update(Sesion)
        .where(Sesion.token_hash == sha256_hex(token), Sesion.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    db.commit()
    return int(resultado.rowcount or 0)


def revocar_todas(db: Session, usuario_id: int, excepto_id=None) -> int:
    """Revoca todas las sesiones del usuario salvo, si se indica, una."""
    consulta = update(Sesion).where(
        Sesion.usuario_id == usuario_id, Sesion.revoked_at.is_(None))
    if excepto_id is not None:
        consulta = consulta.where(Sesion.id != excepto_id)
    resultado = db.execute(consulta.values(revoked_at=utcnow()))
    db.commit()
    return int(resultado.rowcount or 0)


def limpiar_expiradas(db: Session, dias: int = 30) -> int:
    """Borra sesiones muertas hace más de ``dias`` (tarea de mantenimiento).

    Elimina expiradas, revocadas e inactivas antiguas. Nunca toca una sesión
    viva: el umbral se mide contra el momento en que dejó de ser válida.
    """
    limite = utcnow() - timedelta(days=max(0, int(dias)))
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


# --- Rate limiting por IP (Fase A6a) -------------------------------------------
def fallos_por_ip(db: Session, ip: str | None, *, factor: str | None = None,
                  ventana: timedelta = VENTANA_RATE_LIMIT) -> int:
    """Fallos registrados desde ``ip`` en la ventana (opcionalmente por factor).

    Sin IP no hay límite: las pruebas y las tareas internas invocan el servicio
    sin cliente, y bloquear ``None`` cerraría el login entero.
    """
    if not ip:
        return 0
    condiciones = [
        LoginAttempt.ip == _ip_corta(ip),
        LoginAttempt.exito.is_(False),
        LoginAttempt.created_at >= utcnow() - ventana,
    ]
    if factor is not None:
        condiciones.append(LoginAttempt.factor == factor)
    total = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(*condiciones))
    return int(total or 0)


def ip_bloqueada(db: Session, ip: str | None, maximo: int, *,
                 factor: str | None = None,
                 ventana: timedelta = VENTANA_RATE_LIMIT) -> bool:
    """``True`` si ``ip`` ya agotó ``maximo`` fallos en la ventana."""
    return fallos_por_ip(db, ip, factor=factor, ventana=ventana) >= int(maximo)


def autenticar(db: Session, login: str, password: str, ip: str | None = None,
               user_agent: str | None = None) -> ResultadoLogin:
    """Autentica usuario/contraseña aplicando bloqueo por cuenta y auditoría.

    Nunca distingue "usuario inexistente" de "contraseña incorrecta": ambos
    casos devuelven ``motivo="credenciales"`` con el mismo coste de tiempo.

    Fase A6a: antes de verificar credenciales se mira el techo **por IP**
    (``login_ip_max_15min`` fallos en 15 minutos). Es la única defensa contra el
    *password spraying* repartido entre muchas cuentas, donde el bloqueo por
    cuenta nunca se activa. El mensaje es el mismo que el del bloqueo por
    cuenta: no delata si el identificador existe.
    """
    identificador = (login or "").strip().casefold()
    ahora = utcnow()

    # 0) Techo por IP: ni siquiera se busca el usuario ni se verifica la clave.
    if ip and ip_bloqueada(db, ip, int(settings.login_ip_max_15min)):
        audit.registrar(
            db, audit.ACTOR_SISTEMA, "login_ip_locked", entidad="ip",
            detalle={"motivo": "fallos_por_ip",
                     "maximo": int(settings.login_ip_max_15min),
                     "ventana_min": int(VENTANA_RATE_LIMIT.total_seconds() // 60)},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoLogin(motivo="bloqueado",
                              espera_min=max(1, int(settings.login_bloqueo_min)))

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

    # 2) Verificación con el proveedor que corresponde a la cuenta (A5).
    inicio = time.monotonic()
    activo = proveedor(usuario)
    motivo_ldap: str | None = None
    if isinstance(activo, LdapProvider):
        resultado_ldap = activo.autenticar_detallado(
            db, identificador, password or "")
        motivo_ldap = resultado_ldap.motivo
        comprobado = ((resultado_ldap.usuario, False)
                      if resultado_ldap.usuario is not None else None)
        if comprobado is None:
            _esperar_retardo_ldap(inicio)
    else:
        comprobado = activo.authenticate(db, identificador, password or "")
        # Algunos proveedores devuelven ``(None, False)`` en lugar de ``None``.
        if comprobado is not None and comprobado[0] is None:
            comprobado = None

    if comprobado is None:
        accion = "login_fail"
        espera = 0
        # El bloqueo por cuenta es local: AD administra su propia política y
        # bloquear cuentas de dominio desde aquí sería un DoS trivial.
        if usuario is not None and usuario.auth_provider == "local":
            usuario.failed_attempts = (usuario.failed_attempts or 0) + 1
            if usuario.failed_attempts >= int(settings.login_max_intentos):
                usuario.locked_until = ahora + timedelta(
                    minutes=int(settings.login_bloqueo_min))
                usuario.failed_attempts = 0
                espera = int(settings.login_bloqueo_min)
                accion = "login_locked"
        # Cuenta de dominio con el directorio apagado: queda rastro explícito.
        if (usuario is not None and usuario.auth_provider == "ldap"
                and not settings.ldap_enabled):
            audit.registrar(
                db, audit.actor_de(usuario), "ldap_disabled",
                entidad="usuario", entidad_id=usuario.id,
                detalle={"motivo": "directorio_apagado"},
                ip=ip, user_agent=user_agent,
            )
        _registrar_intento(db, identificador, ip, exito=False)
        detalle_fallo = {
            "identificador": identificador,
            "motivo": "bloqueado" if espera else "credenciales_invalidas",
            "espera_min": espera,
        }
        if motivo_ldap is not None:
            detalle_fallo["proveedor"] = "ldap"
            detalle_fallo["motivo_ldap"] = motivo_ldap
        audit.registrar(
            db, audit.actor_de(usuario), accion, entidad="usuario",
            entidad_id=usuario.id if usuario is not None else None,
            detalle=detalle_fallo,
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
    detalle_ok = {"parametros_actualizados": bool(rehash)}
    if motivo_ldap is not None:
        detalle_ok["proveedor"] = "ldap"
    audit.registrar(
        db, audit.actor_de(usuario_db), "login_ok", entidad="usuario",
        entidad_id=usuario_db.id, detalle=detalle_ok,
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
    usuario.password_changed_at = utcnow()
    usuario.must_change_password = False
    db.flush()
    revocadas = revocar_todas(db, usuario.id, excepto_id=sesion_actual)
    audit.registrar(
        db, audit.actor_de(usuario), "password_changed", entidad="usuario",
        entidad_id=usuario.id, detalle={"sesiones_revocadas": revocadas},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    _notificar_password_cambiada(usuario, ip)
    return []


# --- Segundo factor TOTP (Fase A3) ---------------------------------------------
def _fila_totp(db: Session, usuario_id: int) -> MfaTotp | None:
    """Fila de secreto TOTP del usuario (confirmada o pendiente)."""
    return db.get(MfaTotp, usuario_id)


def secreto_totp(fila: MfaTotp | None) -> str | None:
    """Descifra el secreto de la fila; ``None`` si no se puede leer.

    Un fallo de descifrado (clave rotada y perdida, blob alterado) se trata
    como "sin segundo factor disponible" en lugar de romper la petición: el
    usuario conserva los códigos de respaldo y el reset asistido.
    """
    if fila is None:
        return None
    try:
        return secret_box().decrypt(fila.secret_enc, fila.key_id)
    except (ValueError, RuntimeError):
        return None


def estado_mfa(db: Session, usuario: Usuario | None) -> EstadoMfa:
    """Estado del segundo factor: política, canales disponibles y qué exige.

    Orden de decisión (Fase A4):

    1. TOTP confirmado con política ``totp`` o ``any`` → autenticador.
    2. Política ``email`` o ``any`` y correo registrado → código por correo.
    3. Política ``totp`` sin TOTP → enrolamiento forzado (A3).
    4. Sin canal viable → no se exige segundo factor (no se bloquea el acceso).
    """
    if usuario is None:
        return EstadoMfa()
    policy = (usuario.mfa_policy or "any").strip().lower()
    fila = _fila_totp(db, usuario.id)
    enrolado = bool(fila is not None and fila.confirmed_at is not None)
    correo = bool((usuario.email or "").strip())

    metodo: str | None = None
    if enrolado and policy in ("totp", "any"):
        metodo = "totp"
    elif policy in ("email", "any") and correo:
        metodo = "email"

    return EstadoMfa(
        policy=policy,
        enrolado=enrolado,
        requiere_desafio=metodo is not None,
        requiere_enrolamiento=(policy == "totp" and not enrolado),
        enrolado_desde=(fila.confirmed_at.strftime("%Y-%m-%d %H:%M")
                        if enrolado and fila.confirmed_at else None),
        puede_totp=enrolado,
        puede_email=correo and policy in ("email", "any"),
        metodo=metodo,
    )


def codigos_restantes(db: Session, usuario_id: int) -> int:
    """Cantidad de códigos de respaldo sin usar."""
    total = db.scalar(
        select(func.count()).select_from(RecoveryCode).where(
            RecoveryCode.usuario_id == usuario_id,
            RecoveryCode.used_at.is_(None),
        )
    )
    return int(total or 0)


def _emitir_codigos_respaldo(db: Session, usuario: Usuario) -> list[str]:
    """Invalida los códigos sin usar y emite :data:`CODIGOS_RESPALDO` nuevos.

    Solo se persiste el HMAC con el pepper: ni la base ni los logs permiten
    reconstruir los códigos que se muestran una única vez en pantalla.
    """
    db.execute(
        delete(RecoveryCode).where(
            RecoveryCode.usuario_id == usuario.id,
            RecoveryCode.used_at.is_(None),
        )
    )
    codigos = generar_codigos_respaldo(CODIGOS_RESPALDO)
    ahora = utcnow()
    for codigo in codigos:
        db.add(RecoveryCode(
            usuario_id=usuario.id,
            code_hash=hmac_code(codigo, usuario.id, settings.auth_pepper),
            created_at=ahora,
        ))
    db.flush()
    return codigos


def iniciar_enrolamiento(db: Session, usuario: Usuario) -> tuple[str, str] | None:
    """Genera un secreto TOTP pendiente y devuelve ``(secreto, uri)``.

    Devuelve ``None`` si el usuario ya tiene TOTP confirmado: re-enrolar sin
    verificación previa dejaría la cuenta sin segundo factor si el usuario
    abandona el asistente a mitad de camino (ver ``docs/fase-a3-totp.md``).
    """
    if usuario is None or usuario.id is None:
        return None
    fila = _fila_totp(db, usuario.id)
    if fila is not None and fila.confirmed_at is not None:
        return None

    secreto = generar_secreto()
    blob, key_id = secret_box().encrypt(secreto)
    if fila is None:
        db.add(MfaTotp(
            usuario_id=usuario.id, secret_enc=blob, key_id=key_id,
            confirmed_at=None, last_used_step=None,
        ))
    else:
        # Se reemplaza el pendiente anterior: el usuario pudo haber perdido el
        # QR y vuelve a empezar. Nunca se toca un secreto ya confirmado.
        fila.secret_enc = blob
        fila.key_id = key_id
        fila.confirmed_at = None
        fila.last_used_step = None
    db.commit()
    nombre = usuario.email or usuario.username or "usuario"
    return secreto, totp_uri(secreto, nombre, settings.mfa_issuer)


def pendiente_enrolamiento(db: Session, usuario: Usuario) -> tuple[str, str] | None:
    """``(secreto, uri)`` del enrolamiento pendiente, si lo hay.

    Se usa para volver a pintar el QR cuando la confirmación falla: el secreto
    pendiente no se descarta por un código mal tipeado.
    """
    fila = _fila_totp(db, usuario.id)
    if fila is None or fila.confirmed_at is not None:
        return None
    secreto = secreto_totp(fila)
    if secreto is None:
        return None
    nombre = usuario.email or usuario.username or "usuario"
    return secreto, totp_uri(secreto, nombre, settings.mfa_issuer)


def confirmar_enrolamiento(db: Session, usuario: Usuario, codigo: str, *,
                           sesion: Sesion | None = None, ip: str | None = None,
                           user_agent: str | None = None,
                           ) -> tuple[list[str] | None, str | None]:
    """Activa el TOTP con un código válido del autenticador.

    Devuelve ``(codigos_de_respaldo, error)``; exactamente uno de los dos es
    ``None``. Al confirmar, la sesión en curso queda marcada con ``mfa_at``
    porque el usuario acaba de demostrar el segundo factor.
    """
    fila = _fila_totp(db, usuario.id)
    if fila is None or fila.confirmed_at is not None:
        return None, ("No hay una activación pendiente. Inicie la activación "
                      "del autenticador desde esta misma página.")
    secreto = secreto_totp(fila)
    if secreto is None:
        return None, ("No se pudo leer el secreto pendiente. Inicie de nuevo "
                      "la activación del autenticador.")
    paso = verificar_totp(secreto, codigo, None, settings.mfa_window)
    if paso is None:
        return None, ("El código no es válido. Verifique que el reloj del "
                      "teléfono esté en hora automática e intente otra vez.")

    ahora = utcnow()
    fila.confirmed_at = ahora
    fila.last_used_step = paso
    usuario.mfa_enrolled_at = ahora
    if sesion is not None and sesion.mfa_at is None:
        sesion.mfa_at = ahora
    db.flush()
    codigos = _emitir_codigos_respaldo(db, usuario)
    audit.registrar(
        db, audit.actor_de(usuario), "mfa_enrolled", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"metodo": "totp", "codigos_respaldo": len(codigos)},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    _notificar_enrolamiento(usuario)
    return codigos, None


def regenerar_respaldo(db: Session, usuario: Usuario, codigo: str, *,
                       ip: str | None = None,
                       user_agent: str | None = None,
                       ) -> tuple[list[str] | None, str | None]:
    """Emite 10 códigos nuevos exigiendo un código TOTP vigente.

    El TOTP usado se registra en ``last_used_step``: no se puede reutilizar el
    mismo código para regenerar dos veces.
    """
    fila = _fila_totp(db, usuario.id)
    if fila is None or fila.confirmed_at is None:
        return None, "Primero active el autenticador."
    secreto = secreto_totp(fila)
    if secreto is None:
        return None, ("No se pudo leer el secreto del autenticador; use un "
                      "código de respaldo o solicite un reset asistido.")
    paso = verificar_totp(secreto, codigo, fila.last_used_step, settings.mfa_window)
    if paso is None:
        return None, ("Código incorrecto o ya utilizado. Espere al próximo "
                      "código del autenticador e intente otra vez.")
    fila.last_used_step = paso
    db.flush()
    codigos = _emitir_codigos_respaldo(db, usuario)
    audit.registrar(
        db, audit.actor_de(usuario), "mfa_recovery_regenerated",
        entidad="usuario", entidad_id=usuario.id,
        detalle={"codigos_respaldo": len(codigos)}, ip=ip, user_agent=user_agent,
    )
    db.commit()
    return codigos, None


def consumir_codigo_respaldo(db: Session, usuario: Usuario, codigo: str) -> bool:
    """Consume un código de respaldo válido; ``True`` si se usó uno.

    Se comparan todos los hashes sin cortar el bucle para que el tiempo no
    revele cuál coincidió. El código queda marcado con ``used_at``.
    """
    normalizado = normalizar_codigo(codigo or "")
    if len(normalizado) != 9 or normalizado[4] != "-":
        return False
    esperado = hmac_code(normalizado, usuario.id, settings.auth_pepper)
    filas = db.scalars(
        select(RecoveryCode).where(
            RecoveryCode.usuario_id == usuario.id,
            RecoveryCode.used_at.is_(None),
        )
    ).all()
    elegido: RecoveryCode | None = None
    for fila in filas:
        if constant_time_eq(fila.code_hash, esperado):
            elegido = fila
    if elegido is None:
        return False
    elegido.used_at = utcnow()
    return True


def _verificar_codigo(db: Session, usuario: Usuario, codigo: str) -> str | None:
    """Valida TOTP (antirreplay) o código de respaldo; devuelve el método."""
    fila = _fila_totp(db, usuario.id)
    if fila is not None and fila.confirmed_at is not None:
        secreto = secreto_totp(fila)
        if secreto:
            paso = verificar_totp(secreto, codigo, fila.last_used_step,
                                  settings.mfa_window)
            if paso is not None:
                fila.last_used_step = paso
                return "totp"
    if consumir_codigo_respaldo(db, usuario, codigo):
        return "respaldo"
    return None


# --- Challenge de segundo factor ----------------------------------------------
def crear_desafio(db: Session, usuario: Usuario, ip: str | None = None) -> str:
    """Crea el challenge pendiente y devuelve el token en claro.

    Solo se guarda el ``sha256``. Un desafío nuevo invalida los anteriores del
    mismo usuario: así no se acumulan tokens vivos si el usuario reintenta el
    login desde otra pestaña.
    """
    if usuario is None or usuario.id is None:
        raise ValueError("El challenge requiere un usuario persistido.")
    ahora = utcnow()
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.usuario_id == usuario.id,
            AuthToken.purpose == "mfa",
            AuthToken.used_at.is_(None),
        )
        .values(used_at=ahora)
    )
    token = new_token(32)
    db.add(AuthToken(
        usuario_id=usuario.id,
        purpose="mfa",
        token_hash=sha256_hex(token),
        expires_at=ahora + VIGENCIA_DESAFIO,
        mfa_attempts=0,
        ip=_ip_corta(ip),
    ))
    db.commit()
    return token


def cargar_desafio(db: Session, token: str | None) -> AuthToken | None:
    """Challenge vigente del token de la cookie ``eet_mfa`` (o ``None``)."""
    if not token:
        return None
    fila = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == sha256_hex(token),
            AuthToken.purpose == "mfa",
        )
    )
    if fila is None or fila.used_at is not None:
        return None
    if fila.expires_at is None or fila.expires_at <= utcnow():
        return None
    return fila


def verificar_desafio(db: Session, desafio: AuthToken | None, codigo: str, *,
                      ip: str | None = None,
                      user_agent: str | None = None) -> ResultadoMfa:
    """Valida el segundo factor y, si acierta, deja el challenge consumido.

    No crea la sesión: eso lo decide el router para poder responder con la
    cookie y el ``next`` ya validado. Cada intento cuenta en
    ``auth_tokens.mfa_attempts``; al llegar a :data:`MAX_INTENTOS_MFA` el
    challenge se invalida y hay que volver a empezar por la contraseña.

    Fase A6a: además del tope del challenge (por cuenta) se aplica uno por IP
    (:data:`MAX_FALLOS_MFA_POR_IP` fallos en 15 minutos), porque un atacante
    puede encadenar challenges de varias cuentas desde el mismo origen. Cada
    fallo queda en ``login_attempts`` con ``factor='mfa'``.
    """
    ahora = utcnow()
    if desafio is None:
        return ResultadoMfa(motivo="ausente")
    if desafio.used_at is not None or desafio.expires_at is None \
            or desafio.expires_at <= ahora:
        return ResultadoMfa(motivo="invalido")

    usuario = db.get(Usuario, desafio.usuario_id)
    if usuario is None or not usuario.is_active:
        desafio.used_at = ahora
        db.commit()
        return ResultadoMfa(motivo="invalido")

    if ip_bloqueada(db, ip, MAX_FALLOS_MFA_POR_IP, factor="mfa"):
        desafio.used_at = ahora
        audit.registrar(
            db, audit.actor_de(usuario), "mfa_ip_locked", entidad="ip",
            detalle={"motivo": "fallos_por_ip", "maximo": MAX_FALLOS_MFA_POR_IP,
                     "ventana_min": int(VENTANA_RATE_LIMIT.total_seconds() // 60)},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="limite", usuario=usuario)

    desafio.mfa_attempts = int(desafio.mfa_attempts or 0) + 1
    metodo = _verificar_codigo(db, usuario, codigo or "")
    if metodo is not None:
        desafio.used_at = ahora
        audit.registrar(
            db, audit.actor_de(usuario), "mfa_ok", entidad="usuario",
            entidad_id=usuario.id,
            detalle={"metodo": metodo, "intentos": desafio.mfa_attempts},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="ok", usuario=usuario, metodo=metodo)

    limite = desafio.mfa_attempts >= MAX_INTENTOS_MFA
    if limite:
        desafio.used_at = ahora
    _registrar_intento(db, usuario.username or str(usuario.id), ip,
                       exito=False, factor="mfa")
    audit.registrar(
        db, audit.actor_de(usuario), "mfa_fail", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"intentos": desafio.mfa_attempts,
                 "motivo": "limite" if limite else "codigo_invalido"},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return ResultadoMfa(motivo="limite" if limite else "invalido",
                        usuario=usuario)


# --- Código por correo (Fase A4) ------------------------------------------------
def generar_codigo_email() -> str:
    """Código numérico de 6 dígitos con ceros a la izquierda."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _nombre_correo(usuario: Usuario | None) -> str:
    """Nombre para saludar en el correo, sin exponer datos de más."""
    nombre = (getattr(usuario, "nombre", "") or "").strip()
    if nombre:
        return nombre.split()[0]
    return "usuario"


def _url_publica(ruta: str) -> str:
    """URL absoluta del enlace del correo (``EET_PUBLIC_BASE_URL``)."""
    base = (settings.public_base_url or "http://localhost:8000").rstrip("/")
    return f"{base}{ruta}"


def _notificar(usuario: Usuario | None, asunto: str, texto: str,
               html: str) -> bool:
    """Aviso de seguridad por correo (no bloquea el flujo principal)."""
    correo = (getattr(usuario, "email", "") or "").strip()
    if not correo:
        return False
    try:
        return mailer.enviar(correo, asunto, texto, html, sensible=False)
    except Exception:  # el mailer ya no lanza; defensa en profundidad
        return False


def _notificar_enrolamiento(usuario: Usuario) -> None:
    """Aviso de que el segundo factor TOTP quedó activo (plan A4.7)."""
    nombre = _nombre_correo(usuario)
    momento = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    texto = (
        f"Hola {nombre}:\n\n"
        f"Su segundo factor con aplicación autenticadora quedó activado el "
        f"{momento}.\n"
        f"Guarde los códigos de respaldo en un lugar seguro: son la "
        f"alternativa si pierde el teléfono.\n\n"
        f"Si usted no lo activó, cambie su contraseña y avise a la "
        f"coordinación de inmediato.\n"
    )
    html = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:15px;"
        "line-height:1.5;color:#1f2937\">"
        f"<p>Hola {escape(nombre)}:</p>"
        "<p>Su segundo factor con aplicación autenticadora quedó "
        f"<strong>activado</strong> el {escape(momento)}.</p>"
        "<p>Guarde los códigos de respaldo en un lugar seguro: son la "
        "alternativa si pierde el teléfono.</p>"
        "<p>Si usted no lo activó, cambie su contraseña y avise a la "
        "coordinación de inmediato.</p>"
        "</div>"
    )
    _notificar(usuario, "Segundo factor activado — Validador EET", texto, html)


def _notificar_password_cambiada(usuario: Usuario, ip: str | None) -> None:
    """Aviso de cambio de contraseña desde la pantalla de seguridad."""
    nombre = _nombre_correo(usuario)
    momento = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    origen = ip or "origen no disponible"
    texto = (
        f"Hola {nombre}:\n\n"
        f"Su contraseña del Validador EET se cambió el {momento} ({origen}).\n"
        f"Si usted no lo hizo, comuníquese de inmediato con la coordinación "
        f"del proyecto.\n"
    )
    html = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:15px;"
        "line-height:1.5;color:#1f2937\">"
        f"<p>Hola {escape(nombre)}:</p>"
        f"<p>Su contraseña del <strong>Validador EET</strong> se cambió el "
        f"{escape(momento)} ({escape(origen)}).</p>"
        "<p>Si usted no lo hizo, comuníquese de inmediato con la coordinación "
        "del proyecto.</p>"
        "</div>"
    )
    _notificar(usuario, "Contraseña actualizada — Validador EET", texto, html)


def _cifrar_hash(codigo: str, usuario: Usuario) -> str | None:
    """HMAC del código; ``None`` si falta el pepper (no se emite nada)."""
    if not (settings.auth_pepper or "").strip():
        return None
    return hmac_code(codigo, usuario.id, settings.auth_pepper)


def puede_enviar_codigo(db: Session, usuario: Usuario) -> tuple[bool, str, int]:
    """Rate limit del envío de códigos por correo.

    Devuelve ``(permitido, motivo, espera_segundos)`` con motivo ``"ok"``,
    ``"cooldown"`` (menos de 60 s desde el último envío) o ``"limite"`` (5 en
    una hora). Se consulta ``mfa_email_codes`` para no depender de memoria.
    """
    if usuario is None or usuario.id is None:
        return False, "limite", 0
    ahora = utcnow()
    ultimo = db.scalar(
        select(func.max(MfaEmailCode.created_at)).where(
            MfaEmailCode.usuario_id == usuario.id,
            MfaEmailCode.purpose == "mfa",
        )
    )
    if ultimo is not None:
        restante = COOLDOWN_ENVIO_CORREO - (ahora - ultimo)
        if restante.total_seconds() > 0:
            return False, "cooldown", max(1, int(ceil(restante.total_seconds())))
    desde = ahora - timedelta(hours=1)
    total = db.scalar(
        select(func.count()).select_from(MfaEmailCode).where(
            MfaEmailCode.usuario_id == usuario.id,
            MfaEmailCode.purpose == "mfa",
            MfaEmailCode.created_at >= desde,
        )
    )
    if int(total or 0) >= MAX_ENVIOS_CORREO_HORA:
        return False, "limite", 0
    return True, "ok", 0


def puede_enviar_codigo_por_ip(db: Session, ip: str | None) -> bool:
    """Rate limit del envío de códigos **por IP** (Fase A6a).

    ``puede_enviar_codigo`` protege a cada cuenta (5 por hora); este tope evita
    que una misma IP bombardee decenas de buzones distintos. Sin IP no limita.
    """
    if not ip:
        return True
    desde = utcnow() - timedelta(hours=1)
    total = db.scalar(
        select(func.count()).select_from(MfaEmailCode).where(
            MfaEmailCode.ip == _ip_corta(ip),
            MfaEmailCode.purpose == "mfa",
            MfaEmailCode.created_at >= desde,
        )
    )
    return int(total or 0) < MAX_ENVIOS_CORREO_POR_IP_HORA


def _texto_codigo(codigo: str, usuario: Usuario) -> tuple[str, str]:
    """Cuerpo texto y HTML mínimo del código (sin imágenes ni seguimiento)."""
    nombre = _nombre_correo(usuario)
    enlace = _url_publica("/auth/recuperar")
    minutos = int(VIGENCIA_CODIGO_CORREO.total_seconds() // 60)
    texto = (
        f"Hola {nombre}:\n\n"
        f"Su código de acceso al Validador EET es:\n\n"
        f"    {codigo}\n\n"
        f"Vence en {minutos} minutos y sirve una sola vez.\n"
        f"No comparta este código con nadie.\n\n"
        f"Si usted no intentó ingresar, cambie su contraseña ahora:\n"
        f"{enlace}\n\n"
        f"Este mensaje se generó automáticamente; no responda a este correo.\n"
    )
    html = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:15px;"
        "line-height:1.5;color:#1f2937\">"
        f"<p>Hola {escape(nombre)}:</p>"
        "<p>Su código de acceso al <strong>Validador EET</strong> es:</p>"
        "<p style=\"font-size:26px;font-weight:700;letter-spacing:6px;"
        f"margin:16px 0\">{codigo}</p>"
        f"<p>Vence en {minutos} minutos y sirve una sola vez. "
        "<strong>No comparta este código con nadie.</strong></p>"
        f"<p>Si usted no intentó ingresar, "
        f"<a href=\"{escape(enlace)}\">cambie su contraseña ahora</a>.</p>"
        "<p style=\"font-size:12px;color:#6b7280\">Este mensaje se generó "
        "automáticamente; no responda a este correo.</p>"
        "</div>"
    )
    return texto, html


def emitir_codigo_email(db: Session, usuario: Usuario, *,
                        ip: str | None = None,
                        user_agent: str | None = None) -> bool:
    """Emite y envía un código de 6 dígitos; ``True`` si el correo salió.

    Invalida los códigos anteriores no consumidos del mismo usuario y
    propósito, guarda solo el HMAC, audita ``email_otp_sent`` y entrega el
    código por correo (nunca por pantalla ni por log).
    """
    if usuario is None or usuario.id is None or not (usuario.email or "").strip():
        return False
    codigo = generar_codigo_email()
    hash_codigo = _cifrar_hash(codigo, usuario)
    if hash_codigo is None:
        return False

    ahora = utcnow()
    db.execute(
        update(MfaEmailCode)
        .where(
            MfaEmailCode.usuario_id == usuario.id,
            MfaEmailCode.purpose == "mfa",
            MfaEmailCode.consumed_at.is_(None),
        )
        .values(consumed_at=ahora)
    )
    db.add(MfaEmailCode(
        usuario_id=usuario.id,
        code_hash=hash_codigo,
        purpose="mfa",
        expires_at=ahora + VIGENCIA_CODIGO_CORREO,
        attempts=0,
        ip=_ip_corta(ip),
    ))
    audit.registrar(
        db, audit.actor_de(usuario), "email_otp_sent", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"vigencia_min": int(VIGENCIA_CODIGO_CORREO.total_seconds() // 60)},
        ip=ip, user_agent=user_agent,
    )
    # El código debe existir antes de enviar: si el SMTP tarda o falla, queda
    # la evidencia y el usuario puede pedir reenvío (respetando el cooldown).
    db.commit()

    texto, html = _texto_codigo(codigo, usuario)
    enviado = mailer.enviar(
        usuario.email, "Código de acceso — Validador EET", texto, html,
        sensible=True,
    )
    if not enviado:
        audit.registrar(
            db, audit.actor_de(usuario), "email_otp_send_failed",
            entidad="usuario", entidad_id=usuario.id,
            detalle={"motivo": "envio_fallido"}, ip=ip, user_agent=user_agent,
        )
        db.commit()
    return enviado


def codigo_email_vigente(db: Session, usuario: Usuario | None) -> MfaEmailCode | None:
    """Código de correo no consumido más reciente (aunque ya haya expirado)."""
    if usuario is None or usuario.id is None:
        return None
    return db.scalar(
        select(MfaEmailCode)
        .where(
            MfaEmailCode.usuario_id == usuario.id,
            MfaEmailCode.purpose == "mfa",
            MfaEmailCode.consumed_at.is_(None),
        )
        .order_by(MfaEmailCode.created_at.desc(), MfaEmailCode.id.desc())
        .limit(1)
    )


def verificar_desafio_email(db: Session, desafio: AuthToken | None, codigo: str,
                            *, ip: str | None = None,
                            user_agent: str | None = None) -> ResultadoMfa:
    """Valida el código enviado por correo y consume el challenge si acierta.

    Igual que el TOTP, no crea la sesión: el router decide cómo responder. El
    método nunca se acepta solo porque venga en la query: exige que exista un
    código emitido, vigente y con intentos disponibles en la base.
    """
    ahora = utcnow()
    if desafio is None:
        return ResultadoMfa(motivo="ausente")
    if desafio.used_at is not None or desafio.expires_at is None \
            or desafio.expires_at <= ahora:
        return ResultadoMfa(motivo="invalido")

    usuario = db.get(Usuario, desafio.usuario_id)
    if usuario is None or not usuario.is_active or not (usuario.email or "").strip():
        desafio.used_at = ahora
        db.commit()
        return ResultadoMfa(motivo="invalido")

    fila = codigo_email_vigente(db, usuario)
    if fila is None:
        audit.registrar(
            db, audit.actor_de(usuario), "email_otp_fail", entidad="usuario",
            entidad_id=usuario.id, detalle={"motivo": "sin_codigo"},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="invalido", usuario=usuario)

    if ip_bloqueada(db, ip, MAX_FALLOS_MFA_POR_IP, factor="mfa"):
        desafio.used_at = ahora
        audit.registrar(
            db, audit.actor_de(usuario), "mfa_ip_locked", entidad="ip",
            detalle={"motivo": "fallos_por_ip", "maximo": MAX_FALLOS_MFA_POR_IP,
                     "ventana_min": int(VENTANA_RATE_LIMIT.total_seconds() // 60)},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="limite", usuario=usuario)

    if fila.expires_at is None or fila.expires_at <= ahora:
        fila.consumed_at = ahora
        audit.registrar(
            db, audit.actor_de(usuario), "email_otp_fail", entidad="usuario",
            entidad_id=usuario.id, detalle={"motivo": "expirado"},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="invalido", usuario=usuario)

    if int(fila.attempts or 0) >= MAX_INTENTOS_CODIGO_CORREO:
        fila.consumed_at = ahora
        db.commit()
        return ResultadoMfa(motivo="invalido", usuario=usuario)

    desafio.mfa_attempts = int(desafio.mfa_attempts or 0) + 1
    candidato = (codigo or "").strip()
    esperado = _cifrar_hash(candidato, usuario)
    if esperado is not None and len(candidato) == 6 and candidato.isdigit() \
            and constant_time_eq(fila.code_hash, esperado):
        fila.consumed_at = ahora
        desafio.used_at = ahora
        if usuario.mfa_enrolled_at is None:
            usuario.mfa_enrolled_at = ahora
        audit.registrar(
            db, audit.actor_de(usuario), "email_otp_ok", entidad="usuario",
            entidad_id=usuario.id,
            detalle={"metodo": "email", "intentos": desafio.mfa_attempts},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="ok", usuario=usuario, metodo="email")

    fila.attempts = int(fila.attempts or 0) + 1
    if fila.attempts >= MAX_INTENTOS_CODIGO_CORREO:
        fila.consumed_at = ahora
    limite = desafio.mfa_attempts >= MAX_INTENTOS_MFA
    if limite:
        desafio.used_at = ahora
    _registrar_intento(db, usuario.username or str(usuario.id), ip,
                       exito=False, factor="mfa")
    audit.registrar(
        db, audit.actor_de(usuario), "email_otp_fail", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"intentos": fila.attempts, "motivo": "codigo_invalido"},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return ResultadoMfa(motivo="limite" if limite else "invalido",
                        usuario=usuario)


# --- Recuperación de contraseña (Fase A4) ---------------------------------------
def _texto_reset(usuario: Usuario, enlace: str) -> tuple[str, str]:
    """Cuerpo del correo de restablecimiento (contiene el enlace de un uso)."""
    nombre = _nombre_correo(usuario)
    minutos = int(VIGENCIA_RESET.total_seconds() // 60)
    texto = (
        f"Hola {nombre}:\n\n"
        f"Recibimos una solicitud para restablecer su contraseña del "
        f"Validador EET.\n\n"
        f"Abra este enlace para elegir una nueva contraseña:\n\n"
        f"{enlace}\n\n"
        f"El enlace vence en {minutos} minutos y sirve una sola vez.\n"
        f"Si usted no lo solicitó, ignore este mensaje: su contraseña no "
        f"cambia y todas sus sesiones siguen activas.\n\n"
        f"Por seguridad, no comparta este enlace con nadie.\n"
    )
    html = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:15px;"
        "line-height:1.5;color:#1f2937\">"
        f"<p>Hola {escape(nombre)}:</p>"
        "<p>Recibimos una solicitud para restablecer su contraseña del "
        "<strong>Validador EET</strong>.</p>"
        f"<p><a href=\"{escape(enlace)}\" style=\"display:inline-block;"
        "background:#0B3B2E;color:#ffffff;padding:10px 18px;border-radius:6px;"
        "text-decoration:none\">Elegir nueva contraseña</a></p>"
        f"<p>El enlace vence en {minutos} minutos y sirve una sola vez.</p>"
        "<p>Si usted no lo solicitó, ignore este mensaje: su contraseña no "
        "cambia y todas sus sesiones siguen activas.</p>"
        "<p style=\"font-size:12px;color:#6b7280\">Por seguridad, no comparta "
        "este enlace con nadie.</p>"
        "</div>"
    )
    return texto, html


def _texto_confirmacion(usuario: Usuario, ip: str | None) -> tuple[str, str]:
    """Aviso de contraseña restablecida (sin token ni enlace de acción)."""
    nombre = _nombre_correo(usuario)
    momento = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    origen = ip or "origen no disponible"
    texto = (
        f"Hola {nombre}:\n\n"
        f"Su contraseña del Validador EET se restableció el {momento} "
        f"({origen}).\n"
        f"Por seguridad se cerraron todas las sesiones abiertas: deberá "
        f"ingresar de nuevo con la nueva contraseña.\n\n"
        f"Si usted no hizo este cambio, comuníquese de inmediato con la "
        f"coordinación del proyecto.\n"
    )
    html = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:15px;"
        "line-height:1.5;color:#1f2937\">"
        f"<p>Hola {escape(nombre)}:</p>"
        f"<p>Su contraseña del <strong>Validador EET</strong> se restableció "
        f"el {escape(momento)} ({escape(origen)}).</p>"
        "<p>Por seguridad se cerraron todas las sesiones abiertas: deberá "
        "ingresar de nuevo con la nueva contraseña.</p>"
        "<p>Si usted no hizo este cambio, comuníquese de inmediato con la "
        "coordinación del proyecto.</p>"
        "</div>"
    )
    return texto, html


def _auditar_reset(db: Session, usuario: Usuario | None, *, enviado: bool,
                   motivo: str, ip: str | None, user_agent: str | None) -> None:
    """Registra la solicitud sin token, enlace ni identificador crudo."""
    audit.registrar(
        db, audit.actor_de(usuario), "password_reset_requested",
        entidad="usuario", entidad_id=getattr(usuario, "id", None),
        detalle={"enviado": enviado, "motivo": motivo},
        ip=ip, user_agent=user_agent,
    )


def solicitar_reset(db: Session, identificador: str, *, ip: str | None = None,
                    user_agent: str | None = None) -> bool:
    """Crea el token y envía el enlace si la cuenta existe y es local.

    El valor de retorno es solo para trazas internas: la vista responde con
    :data:`RESPUESTA_RECUPERACION` siempre, exista o no la cuenta, sea local o
    LDAP, esté activa o no.

    Fase A6a: además del tope por cuenta y del tope por IP de enlaces emitidos,
    los intentos sobre cuentas no elegibles se registran en ``login_attempts``
    (``factor='recovery'``). Sin eso, sondear identificadores inexistentes no
    dejaba rastro y el límite por IP no se activaba nunca.
    """
    ahora = utcnow()

    # Techo por IP sobre sondeos fallidos: se comprueba antes de tocar la base
    # de usuarios para que el coste sea constante.
    if ip_bloqueada(db, ip, MAX_RESET_POR_IP_HORA, factor="recovery",
                    ventana=timedelta(hours=1)):
        _auditar_reset(db, None, enviado=False, motivo="limite_ip",
                       ip=ip, user_agent=user_agent)
        db.commit()
        return False

    usuario = buscar_usuario(db, identificador)

    if (usuario is None or usuario.auth_provider != "local"
            or not usuario.is_active or not usuario.password_hash
            or not (usuario.email or "").strip()):
        _registrar_intento(db, identificador, ip, exito=False, factor="recovery")
        _auditar_reset(db, usuario, enviado=False,
                       motivo="cuenta_no_elegible", ip=ip, user_agent=user_agent)
        db.commit()
        return False

    desde = ahora - timedelta(hours=1)
    por_cuenta = db.scalar(
        select(func.count()).select_from(AuthToken).where(
            AuthToken.usuario_id == usuario.id,
            AuthToken.purpose == "reset",
            AuthToken.created_at >= desde,
        )
    )
    por_ip = 0
    if ip:
        por_ip = db.scalar(
            select(func.count()).select_from(AuthToken).where(
                AuthToken.purpose == "reset",
                AuthToken.ip == _ip_corta(ip),
                AuthToken.created_at >= desde,
            )
        )
    if (int(por_cuenta or 0) >= MAX_RESET_POR_USUARIO_HORA
            or int(por_ip or 0) >= MAX_RESET_POR_IP_HORA):
        _auditar_reset(db, usuario, enviado=False, motivo="limite",
                       ip=ip, user_agent=user_agent)
        db.commit()
        return False

    # Solo el último enlace solicitado queda válido.
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.usuario_id == usuario.id,
            AuthToken.purpose == "reset",
            AuthToken.used_at.is_(None),
        )
        .values(used_at=ahora)
    )
    token = new_token(32)
    db.add(AuthToken(
        usuario_id=usuario.id,
        purpose="reset",
        token_hash=sha256_hex(token),
        expires_at=ahora + VIGENCIA_RESET,
        ip=_ip_corta(ip),
    ))
    _auditar_reset(db, usuario, enviado=True, motivo="enviado",
                   ip=ip, user_agent=user_agent)
    db.commit()

    enlace = _url_publica(f"/auth/restablecer?token={quote(token, safe='')}")
    texto, html = _texto_reset(usuario, enlace)
    mailer.enviar(usuario.email, "Restablecer contraseña — Validador EET",
                  texto, html, sensible=True)
    return True


def validar_token_reset(db: Session, token: str | None) -> AuthToken | None:
    """Token de restablecimiento vigente o ``None`` (mismo mensaje en todos los casos)."""
    if not token:
        return None
    fila = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == sha256_hex(token),
            AuthToken.purpose == "reset",
        )
    )
    if fila is None or fila.used_at is not None:
        return None
    if fila.expires_at is None or fila.expires_at <= utcnow():
        return None
    usuario = db.get(Usuario, fila.usuario_id)
    if usuario is None or not usuario.is_active or usuario.auth_provider != "local":
        return None
    return fila


def restablecer_password(db: Session, fila: AuthToken, nueva: str,
                         confirmacion: str, *, ip: str | None = None,
                         user_agent: str | None = None) -> list[str]:
    """Aplica la nueva contraseña si el token sigue vigente.

    Devuelve la lista de errores (vacía = aplicado). Con errores el token **no**
    se consume, para que el usuario pueda corregir y reintentar. Al aplicar:
    marca el token usado, invalida los demás enlaces, revoca todas las sesiones,
    cierra los challenges pendientes, audita ``password_reset_ok`` y envía el
    aviso de confirmación sin token.
    """
    if fila is None:
        return ["El enlace no es válido o expiró."]
    usuario = db.get(Usuario, fila.usuario_id)
    if usuario is None or not usuario.is_active or usuario.auth_provider != "local":
        return ["El enlace no es válido o expiró."]

    errores: list[str] = []
    if (nueva or "") != (confirmacion or ""):
        errores.append("La nueva contraseña y su confirmación no coinciden.")
    errores.extend(validar_password(nueva or "", usuario.username, usuario.email))
    if errores:
        return errores

    ahora = utcnow()
    usuario.password_hash = hash_password(nueva)
    usuario.password_changed_at = ahora
    usuario.must_change_password = False
    usuario.failed_attempts = 0
    usuario.locked_until = None
    fila.used_at = ahora

    # Un solo enlace válido y ningún challenge a medio camino.
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.usuario_id == usuario.id,
            AuthToken.purpose.in_(("reset", "mfa")),
            AuthToken.id != fila.id,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=ahora)
    )
    db.execute(
        update(MfaEmailCode)
        .where(
            MfaEmailCode.usuario_id == usuario.id,
            MfaEmailCode.purpose == "mfa",
            MfaEmailCode.consumed_at.is_(None),
        )
        .values(consumed_at=ahora)
    )
    revocadas = db.execute(
        update(Sesion)
        .where(Sesion.usuario_id == usuario.id, Sesion.revoked_at.is_(None))
        .values(revoked_at=ahora)
    ).rowcount or 0
    audit.registrar(
        db, audit.actor_de(usuario), "password_reset_ok", entidad="usuario",
        entidad_id=usuario.id, detalle={"sesiones_revocadas": int(revocadas)},
        ip=ip, user_agent=user_agent,
    )
    db.commit()

    texto, html = _texto_confirmacion(usuario, ip)
    mailer.enviar(usuario.email, "Contraseña actualizada — Validador EET",
                  texto, html, sensible=False)
    return []



# --- Sesiones activas (Fase A3) -----------------------------------------------
def sesiones_activas(db: Session, usuario_id: int) -> list[Sesion]:
    """Sesiones del usuario que hoy sirven para navegar.

    Se excluyen revocadas, vencidas por vida absoluta e inactivas: mostrar una
    fila que el gate ya rechaza confundiría al usuario.
    """
    ahora = utcnow()
    inactividad = ahora - timedelta(minutes=int(settings.session_idle_min))
    return list(db.scalars(
        select(Sesion).where(
            Sesion.usuario_id == usuario_id,
            Sesion.revoked_at.is_(None),
            Sesion.expires_at > ahora,
            Sesion.last_seen_at >= inactividad,
        ).order_by(Sesion.last_seen_at.desc())
    ))


def revocar_sesion_de(db: Session, usuario_id: int, sesion_id: str | UUID, *,
                      sesion_actual: str | UUID | None = None,
                      ip: str | None = None,
                      user_agent: str | None = None) -> str:
    """Revoca una sesión propia y la audita.

    Devuelve ``"ok"``, ``"sesion_actual"`` (se rechaza a propósito: la sesión
    en curso se cierra con «Salir»), ``"no_encontrada"`` (no existe o es de
    otro usuario: no se distingue, para no filtrar ids ajenos) o ``"ya"``.
    """
    try:
        identificador = sesion_id if isinstance(sesion_id, UUID) else UUID(str(sesion_id))
    except (ValueError, TypeError, AttributeError):
        return "no_encontrada"
    sesion = db.get(Sesion, identificador)
    if sesion is None or sesion.usuario_id != usuario_id:
        return "no_encontrada"
    if sesion_actual is not None and str(sesion.id) == str(sesion_actual):
        return "sesion_actual"
    if sesion.revoked_at is not None:
        return "ya"
    sesion.revoked_at = utcnow()
    db.flush()
    audit.registrar(
        db, audit.actor_de(db.get(Usuario, usuario_id)), "session_revoked",
        entidad="sesion", entidad_id=sesion.id,
        detalle={"propia": True}, ip=ip, user_agent=user_agent,
    )
    db.commit()
    return "ok"


# --- Administración de usuarios y auditoría (Fase A6b) -------------------------
#
# Reglas que esta capa garantiza:
#
# - Un administrador **nunca** se cambia el rol ni se desactiva a sí mismo: la
#   separación de deberes exige que otro administrador ejecute el cambio.
# - El último administrador activo no se puede degradar ni desactivar. La
#   comprobación es defensa en profundidad: por la UI no se alcanza (las
#   acciones sobre la propia cuenta se rechazan antes), pero un script, la CLI
#   o un futuro endpoint sí podrían intentarlo.
# - Desactivar, resetear la contraseña o reiniciar el segundo factor revocan
#   todas las sesiones y anulan tokens y códigos pendientes del objetivo. Un
#   reset sin revocación dejaría al titular anterior con acceso vivo.
# - Las contraseñas temporales se generan con :mod:`secrets`, se muestran una
#   sola vez en la respuesta y **nunca** se guardan en claro, se auditan ni se
#   ponen en una URL.
# - Toda acción sensible exige ``motivo`` y queda en ``auditoria`` con el actor
#   real (``usuario:<id>``), la IP y el agente.

#: Filas por página del listado de usuarios.
PAGINA_USUARIOS = 25
#: Filas por página de la vista de auditoría.
PAGINA_AUDITORIA = 50
#: Tope de filas de la exportación CSV (la vista no exporta la tabla completa).
MAX_FILAS_CSV_AUDITORIA = 10_000

#: Roles admitidos en el alta y el cambio de rol (matriz §6.4).
ROLES_USUARIO = ("admin", "supervision", "cargador", "asistente", "consulta")
#: Letras de la terna; obligatorias solo para el rol ``asistente``.
LETRAS_TERNA = ("A", "B", "C")
#: Segundo factor por defecto según el rol (análisis §2): los roles críticos
#: nacen con TOTP obligatorio; el resto admite TOTP o correo.
MFA_POR_ROL = {"admin": "totp", "supervision": "totp"}
MFA_POR_DEFECTO = "any"

#: Etiquetas legibles de la política de segundo factor en la tabla de usuarios.
MFA_ETIQUETAS = {
    "totp": "TOTP",
    "email": "Correo",
    "any": "TOTP o correo",
    "disabled": "Deshabilitado",
}

# Alfabeto de las contraseñas temporales: sin caracteres ambiguos (I/O/1/0)
# para que se puedan dictar o transcribir sin errores.
_ALFABETO_TEMPORAL = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "abcdefghijkmnopqrstuvwxyz"
    "23456789"
    "!@#$%*-_"
)
_LARGO_TEMPORAL = 16
#: Usuario admitido: 3–100 caracteres de letras, números, punto, guion y «_».
_PATRON_USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,100}$")
#: Tope del motivo registrado en la auditoría.
_MAX_MOTIVO = 200


@dataclass(frozen=True)
class ResultadoAlta:
    """Resultado de :func:`crear_usuario_admin`.

    ``password`` solo trae valor cuando la contraseña la generó el sistema:
    es el único momento en que existe en claro y la vista la muestra una vez.
    """

    usuario: Usuario | None = None
    errores: tuple[str, ...] = ()
    password: str | None = None

    @property
    def ok(self) -> bool:
        return self.usuario is not None


@dataclass(frozen=True)
class PaginaUsuarios:
    """Página del listado de cuentas con su etiqueta de segundo factor."""

    filas: tuple[tuple[Usuario, str], ...]
    total: int
    paginas: int
    page: int
    conteos: dict[str, int]


@dataclass(frozen=True)
class PaginaAuditoria:
    """Página de la vista de auditoría con el detalle ya serializado."""

    filas: tuple[tuple[Auditoria, str], ...]
    total: int
    paginas: int
    page: int


def generar_password_temporal(largo: int = _LARGO_TEMPORAL) -> str:
    """Contraseña aleatoria de 16 caracteres para entrega presencial.

    Usa ``secrets`` (no ``random``) y un alfabeto sin caracteres ambiguos. No
    depende de la identidad del usuario, así que nunca coincide con su nombre
    de usuario ni con su correo en la práctica; la política se valida igual al
    asignarla.
    """
    tamano = max(12, int(largo))
    return "".join(secrets.choice(_ALFABETO_TEMPORAL) for _ in range(tamano))


def mfa_etiqueta(usuario: Usuario, totp_confirmado: bool) -> str:
    """Texto de la columna «2FA» para la tabla de usuarios."""
    politica = (getattr(usuario, "mfa_policy", None) or MFA_POR_DEFECTO).strip().lower()
    if politica == "totp" and not totp_confirmado:
        return "TOTP (pendiente)"
    if politica == "any" and totp_confirmado:
        return "TOTP o correo (TOTP activo)"
    return MFA_ETIQUETAS.get(politica, politica)


def _motivo(motivo: str | None) -> tuple[str, list[str]]:
    """Normaliza y valida el motivo obligatorio de una acción sensible."""
    texto = (motivo or "").strip()
    if not texto:
        return "", ["Indique el motivo: queda registrado en la auditoría."]
    if len(texto) > _MAX_MOTIVO:
        return texto[:_MAX_MOTIVO], [
            f"El motivo no puede superar los {_MAX_MOTIVO} caracteres."
        ]
    return texto, []


def _admins_activos(db: Session, excepto_id: int | None = None) -> int:
    """Cantidad de administradores activos, opcionalmente excluyendo uno."""
    condiciones = [Usuario.rol == "admin", Usuario.is_active.is_(True)]
    if excepto_id is not None:
        condiciones.append(Usuario.id != excepto_id)
    total = db.scalar(
        select(func.count()).select_from(Usuario).where(*condiciones))
    return int(total or 0)


def _invalidar_acceso(db: Session, usuario_id: int) -> int:
    """Revoca sesiones y anula tokens/códigos pendientes del usuario.

    Devuelve la cantidad de sesiones revocadas. No confirma la transacción: el
    caso de uso la cierra junto con su evento de auditoría.
    """
    ahora = utcnow()
    revocadas = db.execute(
        update(Sesion)
        .where(Sesion.usuario_id == usuario_id, Sesion.revoked_at.is_(None))
        .values(revoked_at=ahora)
    ).rowcount or 0
    db.execute(
        update(AuthToken)
        .where(AuthToken.usuario_id == usuario_id, AuthToken.used_at.is_(None))
        .values(used_at=ahora)
    )
    db.execute(
        update(MfaEmailCode)
        .where(
            MfaEmailCode.usuario_id == usuario_id,
            MfaEmailCode.consumed_at.is_(None),
        )
        .values(consumed_at=ahora)
    )
    return int(revocadas)


def _pagina(valor) -> int:
    """Convierte el parámetro ``page`` en un entero ≥ 1 (tolerante a basura)."""
    try:
        return max(1, int(valor or 1))
    except (TypeError, ValueError):
        return 1


def listar_usuarios(db: Session, *, q: str | None = None, rol: str | None = None,
                    activo: str | None = None, page: int = 1,
                    por_pagina: int = PAGINA_USUARIOS) -> PaginaUsuarios:
    """Cuentas filtradas y paginadas, con los contadores globales por rol.

    ``activo`` acepta ``"1"``, ``"0"`` o vacío. Un rol desconocido se ignora
    (no filtra) en lugar de romper la vista.
    """
    condiciones = []
    texto = (q or "").strip()
    if texto:
        patron = f"%{texto}%"
        condiciones.append(or_(
            Usuario.nombre.ilike(patron),
            Usuario.username.ilike(patron),
            Usuario.email.ilike(patron),
        ))
    if rol in ROLES_USUARIO:
        condiciones.append(Usuario.rol == rol)
    if activo in ("1", "0"):
        condiciones.append(Usuario.is_active.is_(activo == "1"))

    total = int(db.scalar(
        select(func.count()).select_from(Usuario).where(*condiciones)) or 0)
    paginas = max(1, ceil(total / max(1, int(por_pagina))))
    pagina = min(_pagina(page), paginas)
    usuarios = list(db.scalars(
        select(Usuario)
        .where(*condiciones)
        .order_by(Usuario.nombre.asc(), Usuario.id.asc())
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
    ))
    confirmados: set[int] = set()
    if usuarios:
        confirmados = set(db.scalars(
            select(MfaTotp.usuario_id).where(
                MfaTotp.usuario_id.in_([fila.id for fila in usuarios]),
                MfaTotp.confirmed_at.is_not(None),
            )
        ).all())

    conteos = {clave: 0 for clave in ROLES_USUARIO}
    for clave, cantidad in db.execute(
            select(Usuario.rol, func.count()).group_by(Usuario.rol)).all():
        if clave in conteos:
            conteos[clave] = int(cantidad or 0)

    return PaginaUsuarios(
        filas=tuple((fila, mfa_etiqueta(fila, fila.id in confirmados))
                    for fila in usuarios),
        total=total,
        paginas=paginas,
        page=pagina,
        conteos=conteos,
    )


def crear_usuario_admin(db: Session, *, actor: Usuario | None, nombre: str,
                        username: str, email: str, rol: str, letra: str | None,
                        mfa_policy: str | None, password: str,
                        confirmacion: str, generar_password: bool,
                        ip: str | None = None,
                        user_agent: str | None = None) -> ResultadoAlta:
    """Crea una cuenta local desde la administración.

    Devuelve todos los incumplimientos juntos para que el formulario se
    corrija en un solo intento. La cuenta nace con ``must_change_password`` y
    sin segundo factor enrolado: el enrolamiento ocurre en el primer acceso.
    """
    errores: list[str] = []

    nombre_limpio = (nombre or "").strip()
    if not nombre_limpio:
        errores.append("El nombre es obligatorio.")
    elif len(nombre_limpio) > 200:
        errores.append("El nombre no puede superar los 200 caracteres.")

    usuario_limpio = (username or "").strip()
    if not _PATRON_USERNAME.match(usuario_limpio):
        errores.append(
            "El usuario debe tener entre 3 y 100 caracteres y solo admite "
            "letras, números, punto, guion y guion bajo."
        )

    correo_limpio = (email or "").strip()
    try:
        correo_limpio = validate_email(
            correo_limpio, check_deliverability=False).normalized
    except EmailNotValidError:
        errores.append("El correo no tiene un formato válido.")

    rol_limpio = (rol or "").strip().lower()
    if rol_limpio not in ROLES_USUARIO:
        errores.append("El rol indicado no es válido.")

    letra_limpia: str | None = None
    if rol_limpio == "asistente":
        letra_limpia = (letra or "").strip().upper()
        if letra_limpia not in LETRAS_TERNA:
            errores.append("El rol asistente exige letra de terna (A, B o C).")

    politica = (mfa_policy or "").strip().lower()
    if not politica:
        politica = MFA_POR_ROL.get(rol_limpio, MFA_POR_DEFECTO)
    elif politica not in POLITICAS_MFA:
        errores.append("La política de segundo factor no es válida.")
        politica = MFA_POR_DEFECTO

    if usuario_limpio and correo_limpio:
        duplicados = db.scalars(
            select(Usuario).where(or_(
                func.lower(Usuario.username) == usuario_limpio.casefold(),
                func.lower(Usuario.email) == correo_limpio.casefold(),
            ))
        ).all()
        for fila in duplicados:
            if (fila.username or "").casefold() == usuario_limpio.casefold():
                errores.append(
                    f"Ya existe una cuenta con el usuario «{usuario_limpio}».")
            if (fila.email or "").casefold() == correo_limpio.casefold():
                errores.append(
                    f"Ya existe una cuenta con el correo «{correo_limpio}».")

    temporal: str | None = None
    if generar_password:
        temporal = generar_password_temporal()
        password_limpia = temporal
    else:
        password_limpia = password or ""
        if password_limpia != (confirmacion or ""):
            errores.append("La contraseña y su confirmación no coinciden.")
        errores.extend(validar_password(
            password_limpia, usuario_limpio, correo_limpio))

    if errores:
        return ResultadoAlta(errores=tuple(errores))

    usuario = Usuario(
        nombre=nombre_limpio,
        username=usuario_limpio,
        email=correo_limpio,
        rol=rol_limpio,
        letra_terna=letra_limpia,
        auth_provider="local",
        password_hash=hash_password(password_limpia),
        password_changed_at=utcnow(),
        must_change_password=True,
        is_active=True,
        mfa_policy=politica,
        role_source="manual",
    )
    db.add(usuario)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "user_created", entidad="usuario",
        entidad_id=usuario.id,
        detalle={
            "rol": rol_limpio,
            "username": usuario_limpio,
            "letra_terna": letra_limpia,
            "mfa_policy": politica,
            "must_change_password": True,
            "password_generada": temporal is not None,
        },
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return ResultadoAlta(usuario=usuario, password=temporal)


def cambiar_rol_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                        nuevo_rol: str, motivo: str, *,
                        letra: str | None = None, ip: str | None = None,
                        user_agent: str | None = None,
                        ) -> tuple[Usuario | None, list[str]]:
    """Cambia el rol (y la letra de terna) de una cuenta, con motivo.

    Rechaza el cambio sobre la propia cuenta y la degradación del último
    administrador activo. La cuenta nunca se desactiva aquí.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."]

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)

    rol_limpio = (nuevo_rol or "").strip().lower()
    if rol_limpio not in ROLES_USUARIO:
        errores.append("El rol indicado no es válido.")

    if actor is not None and actor.id == usuario.id:
        errores.append("No puede cambiar su propio rol.")

    letra_limpia: str | None = None
    if rol_limpio == "asistente":
        letra_limpia = (letra or usuario.letra_terna or "").strip().upper()
        if letra_limpia not in LETRAS_TERNA:
            errores.append(
                "El rol asistente requiere letra de terna (A, B o C).")
    else:
        letra_limpia = None

    if (usuario.rol == "admin" and usuario.is_active
            and rol_limpio != "admin"
            and _admins_activos(db, excepto_id=usuario.id) == 0):
        errores.append(
            "No puede quitar el rol al último administrador activo: asigne "
            "otro administrador antes de degradar esta cuenta.")

    if errores:
        return usuario, errores

    anterior, letra_anterior = usuario.rol, usuario.letra_terna
    usuario.rol = rol_limpio
    usuario.letra_terna = letra_limpia
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "role_changed", entidad="usuario",
        entidad_id=usuario.id,
        detalle={
            "de": anterior,
            "a": rol_limpio,
            "letra_anterior": letra_anterior,
            "letra": letra_limpia,
            "motivo": motivo_limpio,
        },
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, []


def cambiar_estado_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                           activar: bool, motivo: str, *,
                           ip: str | None = None,
                           user_agent: str | None = None,
                           ) -> tuple[Usuario | None, list[str]]:
    """Activa o desactiva una cuenta (baja lógica) con motivo.

    Desactivar revoca todas las sesiones y anula tokens y códigos pendientes.
    Activar limpia el bloqueo por intentos fallidos para que la cuenta pueda
    volver a entrar sin esperar la ventana de 15 minutos.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."]

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)

    if not activar and actor is not None and actor.id == usuario.id:
        errores.append("No puede desactivar su propia cuenta.")
    if (not activar and usuario.rol == "admin" and usuario.is_active
            and _admins_activos(db, excepto_id=usuario.id) == 0):
        errores.append(
            "No puede desactivar al último administrador activo: asigne otro "
            "administrador antes de dar de baja esta cuenta.")
    if usuario.is_active == activar:
        errores.append(
            "La cuenta ya está activa." if activar else "La cuenta ya está inactiva.")

    if errores:
        return usuario, errores

    usuario.is_active = activar
    revocadas = 0
    if activar:
        usuario.failed_attempts = 0
        usuario.locked_until = None
    else:
        revocadas = _invalidar_acceso(db, usuario.id)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor),
        "user_enabled" if activar else "user_disabled",
        entidad="usuario", entidad_id=usuario.id,
        detalle={"motivo": motivo_limpio, "sesiones_revocadas": revocadas},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, []


def reset_password_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                           motivo: str, *, ip: str | None = None,
                           user_agent: str | None = None,
                           ) -> tuple[Usuario | None, list[str], str | None]:
    """Asigna una contraseña temporal y devuelve su valor en claro una vez.

    Prohibido sobre la propia cuenta (para eso está el cambio de contraseña) y
    sobre cuentas cuyo proveedor no es local. Revoca sesiones, anula tokens y
    códigos, y exige cambio en el próximo acceso.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."], None

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)
    if actor is not None and actor.id == usuario.id:
        errores.append(
            "No puede restablecer su propia contraseña desde aquí: use "
            "«Cambiar contraseña» en su cuenta.")
    if usuario.auth_provider != "local":
        errores.append(
            "La contraseña de esta cuenta se administra fuera del sistema.")

    if errores:
        return usuario, errores, None

    temporal = generar_password_temporal()
    usuario.password_hash = hash_password(temporal)
    usuario.password_changed_at = utcnow()
    usuario.must_change_password = True
    usuario.failed_attempts = 0
    usuario.locked_until = None
    revocadas = _invalidar_acceso(db, usuario.id)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "password_reset_admin", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"motivo": motivo_limpio, "sesiones_revocadas": revocadas,
                 "temporal": True},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, [], temporal


def reset_mfa_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                      motivo: str, *, ip: str | None = None,
                      user_agent: str | None = None,
                      ) -> tuple[Usuario | None, list[str]]:
    """Reinicia el segundo factor: borra TOTP y códigos de respaldo.

    Prohibido sobre la propia cuenta: un error del administrador dejaría su
    propia cuenta sin el segundo factor que su rol exige. Para la propia cuenta
    existe el flujo de ``/auth/seguridad`` y, si se perdió el teléfono, otro
    administrador ejecuta este reset. Deja ``mfa_enrolled_at`` en NULL para que
    la política TOTP fuerce el re-enrolamiento en el próximo acceso.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."]

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)
    if actor is not None and actor.id == usuario.id:
        errores.append(
            "No puede reiniciar su propio segundo factor desde aquí: solicite "
            "el reset a otro administrador.")

    if errores:
        return usuario, errores

    fila_totp = db.get(MfaTotp, usuario.id)
    tenia_totp = fila_totp is not None
    if fila_totp is not None:
        db.delete(fila_totp)
    codigos = db.execute(
        delete(RecoveryCode).where(RecoveryCode.usuario_id == usuario.id)
    ).rowcount or 0
    usuario.mfa_enrolled_at = None
    revocadas = _invalidar_acceso(db, usuario.id)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "mfa_reset_admin", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"motivo": motivo_limpio, "totp_eliminado": tenia_totp,
                 "codigos_eliminados": int(codigos),
                 "sesiones_revocadas": revocadas},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, []


# --- Vista y exportación de auditoría (Fase A6b) -------------------------------
def _fecha_filtro(valor: str | None) -> datetime | None:
    """Parsea ``AAAA-MM-DD``; un valor inválido se ignora (no rompe la vista)."""
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:
        return None


def _condiciones_auditoria(accion: str | None, actor: str | None,
                           desde: str | None, hasta: str | None,
                           q: str | None) -> list:
    """Filtros compartidos por la vista y la exportación CSV."""
    condiciones = []
    if (accion or "").strip():
        condiciones.append(Auditoria.accion.ilike(f"%{accion.strip()}%"))
    if (actor or "").strip():
        condiciones.append(Auditoria.actor.ilike(f"%{actor.strip()}%"))
    desde_dt = _fecha_filtro(desde)
    hasta_dt = _fecha_filtro(hasta)
    if desde_dt is not None:
        condiciones.append(Auditoria.fecha >= desde_dt)
    if hasta_dt is not None:
        # El filtro «hasta» incluye todo el día indicado.
        condiciones.append(Auditoria.fecha < hasta_dt + timedelta(days=1))
    if (q or "").strip():
        patron = f"%{q.strip()}%"
        condiciones.append(or_(
            Auditoria.entidad.ilike(patron),
            Auditoria.entidad_id.ilike(patron),
        ))
    return condiciones


def _detalle_json(fila: Auditoria) -> str:
    """Detalle del evento en JSON legible (sin secretos: se sanearon al escribir)."""
    try:
        return json.dumps(fila.detalle or {}, ensure_ascii=False,
                          indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):  # detalle corrupto: no romper la página
        return "{}"


def listar_auditoria(db: Session, *, accion: str | None = None,
                     actor: str | None = None, desde: str | None = None,
                     hasta: str | None = None, q: str | None = None,
                     page: int = 1,
                     por_pagina: int = PAGINA_AUDITORIA) -> PaginaAuditoria:
    """Eventos de auditoría filtrados y paginados, del más reciente al más viejo."""
    condiciones = _condiciones_auditoria(accion, actor, desde, hasta, q)
    total = int(db.scalar(
        select(func.count()).select_from(Auditoria).where(*condiciones)) or 0)
    paginas = max(1, ceil(total / max(1, int(por_pagina))))
    pagina = min(_pagina(page), paginas)
    filas = list(db.scalars(
        select(Auditoria)
        .where(*condiciones)
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
    ))
    return PaginaAuditoria(
        filas=tuple((fila, _detalle_json(fila)) for fila in filas),
        total=total, paginas=paginas, page=pagina)


def _celda_csv(valor) -> str:
    """Sanea una celda para Excel: evita la inyección de fórmulas.

    Un valor que empieza con ``= + - @`` o un carácter de control haría que
    Excel lo evalúe como fórmula. Se antepone un apóstrofo, que Excel muestra
    como texto y descarta al copiar.
    """
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + texto
    return texto


def exportar_auditoria(db: Session, *, accion: str | None = None,
                       actor: str | None = None, desde: str | None = None,
                       hasta: str | None = None, q: str | None = None,
                       limite: int = MAX_FILAS_CSV_AUDITORIA) -> str:
    """CSV de la auditoría filtrada (máximo :data:`MAX_FILAS_CSV_AUDITORIA`).

    El BOM UTF-8 lo agrega el router al codificar; aquí solo se genera el
    contenido con ``csv.writer`` (comillas cuando hacen falta y CRLF).
    """
    condiciones = _condiciones_auditoria(accion, actor, desde, hasta, q)
    filas = db.scalars(
        select(Auditoria)
        .where(*condiciones)
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .limit(max(1, int(limite)))
    ).all()

    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    escritor.writerow([
        "fecha", "actor", "accion", "entidad", "entidad_id", "ip",
        "user_agent", "detalle",
    ])
    for fila in filas:
        escritor.writerow([
            _celda_csv(fila.fecha.strftime("%Y-%m-%d %H:%M:%S")
                       if fila.fecha else ""),
            _celda_csv(fila.actor),
            _celda_csv(fila.accion),
            _celda_csv(fila.entidad),
            _celda_csv(fila.entidad_id),
            _celda_csv(fila.ip),
            _celda_csv(fila.user_agent),
            _celda_csv(_detalle_json(fila).replace("\n", " ")),
        ])
    return buffer.getvalue()
