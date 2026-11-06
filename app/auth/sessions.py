"""Sesiones, login local, bloqueo y cambio de contraseña."""
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit
from app.auth.common import (
    _INTERVALO_TOQUE,
    VENTANA_RATE_LIMIT,
    ResultadoLogin,
    _esperar_retardo_ldap,
    _ip_corta,
    _registrar_intento,
    ip_bloqueada,
)
from app.auth.notifications import _notificar_password_cambiada
from app.auth.providers import (
    AuthProvider,
    LdapProvider,
    LocalProvider,
    buscar_usuario,
)
from app.core.config import settings
from app.core.security import (
    hash_password,
    new_token,
    sha256_hex,
    utcnow,
    validar_password,
    verify_password,
)
from app.models import Sesion, Usuario


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


# --- Tokens y cookies ----------------------------------------------------------
def token_csrf_de_sesion(token_hash: str) -> str:
    """Token CSRF derivado de la sesión (HMAC-SHA256, estable por sesión).

    Se deriva del hash del token y de ``EET_SECRET_KEY``: no se guarda una
    columna extra en ``sesiones`` (el esquema de A0 está congelado por sus
    pruebas) y el token cambia al rotar la sesión.
    """
    clave = (settings.secret_key or settings.auth_pepper or "eet-csrf").encode("utf-8")
    mensaje = f"csrf:{token_hash}".encode()
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


def limpiar_expiradas(db: Session, dias: int = 30,
                      usuario_id: int | None = None) -> int:
    """Borra sesiones muertas hace más de ``dias`` (tarea de mantenimiento).

    Elimina expiradas, revocadas e inactivas antiguas. Nunca toca una sesión
    viva: el umbral se mide contra el momento en que dejó de ser válida.
    ``usuario_id`` limita la limpieza a una cuenta (pruebas y operación).
    """
    dias = max(0, int(dias))
    limite = utcnow() - timedelta(days=dias)
    temporales = [Sesion.expires_at < limite, Sesion.revoked_at < limite]
    if dias > 0:
        # Con --dias 0 solo se purgan sesiones ya expiradas/revocadas; no se
        # debe borrar una sesión viva por su último acceso.
        temporales.append(Sesion.last_seen_at < limite)
    consulta = delete(Sesion).where(or_(*temporales))
    if usuario_id is not None:
        consulta = consulta.where(Sesion.usuario_id == usuario_id)
    resultado = db.execute(consulta)
    db.commit()
    return int(resultado.rowcount or 0)


# --- Login ---------------------------------------------------------------------
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
