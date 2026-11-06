"""Recuperación de contraseña y tokens de restablecimiento."""
from datetime import timedelta
from html import escape
from urllib.parse import quote

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth import audit, mailer
from app.auth.common import (
    MAX_RESET_POR_IP_HORA,
    MAX_RESET_POR_USUARIO_HORA,
    VIGENCIA_RESET,
    _ip_corta,
    _registrar_intento,
    ip_bloqueada,
)
from app.auth.notifications import _nombre_correo, _url_publica
from app.auth.providers import buscar_usuario
from app.core.security import (
    hash_password,
    new_token,
    sha256_hex,
    utcnow,
    validar_password,
)
from app.models import AuthToken, MfaEmailCode, Sesion, Usuario


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
