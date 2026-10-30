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
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit
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
from app.models import AuthToken, LoginAttempt, MfaTotp, RecoveryCode, Sesion, Usuario

__all__ = [
    "CODIGOS_RESPALDO",
    "MAX_INTENTOS_MFA",
    "POLITICAS_MFA",
    "ResultadoLogin",
    "ResultadoMfa",
    "EstadoMfa",
    "autenticar",
    "cambiar_password",
    "cargar_desafio",
    "cargar_sesion",
    "codigos_restantes",
    "confirmar_enrolamiento",
    "crear_desafio",
    "crear_sesion",
    "estado_mfa",
    "iniciar_enrolamiento",
    "limpiar_expiradas",
    "regenerar_respaldo",
    "revocar_por_token",
    "revocar_sesion",
    "revocar_sesion_de",
    "revocar_todas",
    "sesion_para_csrf",
    "sesiones_activas",
    "token_csrf_de_sesion",
    "verificar_desafio",
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
    """Estado del segundo factor de un usuario (Fase A3).

    ``requiere_desafio`` es el caso "tiene TOTP confirmado y su política lo
    exige": el login no crea sesión y pasa por ``/auth/mfa``.
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
    """Estado del segundo factor: política, enrolamiento y qué exige el login."""
    if usuario is None:
        return EstadoMfa()
    policy = (usuario.mfa_policy or "any").strip().lower()
    fila = _fila_totp(db, usuario.id)
    enrolado = bool(fila is not None and fila.confirmed_at is not None)
    return EstadoMfa(
        policy=policy,
        enrolado=enrolado,
        requiere_desafio=enrolado and policy in ("totp", "any"),
        requiere_enrolamiento=(policy == "totp" and not enrolado),
        enrolado_desde=(fila.confirmed_at.strftime("%Y-%m-%d %H:%M")
                        if enrolado and fila.confirmed_at else None),
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
