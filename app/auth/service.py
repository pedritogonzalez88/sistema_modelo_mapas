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
- Usar ``datetime.utcnow()`` porque las columnas ``DateTime`` del esquema son
  *naive* (zona horaria implícita UTC), igual que el resto de la aplicación.

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
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from html import escape
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit, mailer
from app.auth.providers import AuthProvider, LocalProvider, buscar_usuario
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
    validar_password,
    verificar_totp,
    verify_password,
)
from app.models import (
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
    "MAX_INTENTOS_CODIGO_CORREO",
    "MAX_INTENTOS_MFA",
    "MAX_RESET_POR_IP_HORA",
    "MAX_RESET_POR_USUARIO_HORA",
    "POLITICAS_MFA",
    "ResultadoLogin",
    "ResultadoMfa",
    "EstadoMfa",
    "autenticar",
    "cambiar_password",
    "cargar_desafio",
    "cargar_sesion",
    "codigo_email_vigente",
    "codigos_restantes",
    "confirmar_enrolamiento",
    "crear_desafio",
    "crear_sesion",
    "emitir_codigo_email",
    "estado_mfa",
    "generar_codigo_email",
    "iniciar_enrolamiento",
    "limpiar_expiradas",
    "puede_enviar_codigo",
    "regenerar_respaldo",
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
    ahora = datetime.utcnow()
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
    ahora = datetime.utcnow()
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

    ahora = datetime.utcnow()
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
    elegido.used_at = datetime.utcnow()
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
    ahora = datetime.utcnow()
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
    if fila.expires_at is None or fila.expires_at <= datetime.utcnow():
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
    """
    ahora = datetime.utcnow()
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
    momento = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
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
    momento = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
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
    ahora = datetime.utcnow()
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

    ahora = datetime.utcnow()
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
    ahora = datetime.utcnow()
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
    momento = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
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
    """
    ahora = datetime.utcnow()
    usuario = buscar_usuario(db, identificador)

    if (usuario is None or usuario.auth_provider != "local"
            or not usuario.is_active or not usuario.password_hash
            or not (usuario.email or "").strip()):
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
    if fila.expires_at is None or fila.expires_at <= datetime.utcnow():
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

    ahora = datetime.utcnow()
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
    ahora = datetime.utcnow()
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
    sesion.revoked_at = datetime.utcnow()
    db.flush()
    audit.registrar(
        db, audit.actor_de(db.get(Usuario, usuario_id)), "session_revoked",
        entidad="sesion", entidad_id=sesion.id,
        detalle={"propia": True}, ip=ip, user_agent=user_agent,
    )
    db.commit()
    return "ok"
