"""Código de segundo factor enviado por correo."""
import secrets
from datetime import timedelta
from html import escape
from math import ceil

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth import audit, mailer
from app.auth.common import (
    COOLDOWN_ENVIO_CORREO,
    MAX_ENVIOS_CORREO_HORA,
    MAX_ENVIOS_CORREO_POR_IP_HORA,
    MAX_FALLOS_MFA_POR_IP,
    MAX_INTENTOS_CODIGO_CORREO,
    MAX_INTENTOS_MFA,
    VENTANA_RATE_LIMIT,
    VIGENCIA_CODIGO_CORREO,
    ResultadoMfa,
    _ip_corta,
    _registrar_intento,
    ip_bloqueada,
)
from app.auth.notifications import _nombre_correo, _url_publica
from app.core.config import settings
from app.core.security import constant_time_eq, hmac_code, utcnow
from app.models import AuthToken, MfaEmailCode, Usuario


def generar_codigo_email() -> str:
    """Código numérico de 6 dígitos con ceros a la izquierda."""
    return f"{secrets.randbelow(1_000_000):06d}"


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
            return False, "cooldown", max(1, ceil(restante.total_seconds()))
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
    # El tope también se aplica aquí, no solo en el reenvío: el login inicial
    # llamaba a esta función directamente y permitía inundar el buzón (y
    # invalidar el código en curso) con solo conocer la contraseña.
    permitido, _motivo, _espera = puede_enviar_codigo(db, usuario)
    if not permitido:
        return False
    if not puede_enviar_codigo_por_ip(db, ip):
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
        # Explícito: los topes comparan con utcnow() de la app, no con el reloj
        # del servidor de base de datos.
        created_at=ahora,
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
    # Mismo bloqueo que en el TOTP: evita consumir dos veces el challenge.
    bloqueado = db.scalar(
        select(AuthToken)
        .where(AuthToken.id == desafio.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if bloqueado is None:
        return ResultadoMfa(motivo="invalido")
    desafio = bloqueado
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
        # ``mfa_enrolled_at`` queda reservado a TOTP: el gate de enrolamiento
        # no debe confundir un código por correo con un autenticador enrolado.
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
