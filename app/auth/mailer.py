"""Envío de correo y cola de salida (Fase A4).

Diseño
------

Tres backends intercambiables por configuración:

- ``console``: desarrollo. Escribe en el log **solo** el destinatario y el
  asunto; el cuerpo (que puede llevar un código de un solo uso o un enlace de
  restablecimiento) nunca se registra.
- ``file``: escribe un ``.eml`` único en ``settings.email_dir`` (por defecto
  ``uploads/mail/``). Pensado para pruebas y para inspeccionar el correo real
  sin servidor SMTP.
- ``smtp``: ``smtplib`` con timeout de 10 s, STARTTLS opcional y autenticación
  si hay usuario. La contraseña solo se usa en el ``login`` y no se registra.

Toda entrega deja una fila en ``email_outbox`` antes de intentar el envío:

- ``cuerpo`` guarda un sobre JSON (``texto`` + ``html``). Cuando el correo es
  sensible (códigos y enlaces de un solo uso) el sobre se cifra con
  :class:`app.core.security.SecretBox` y se prefija con ``enc:``. El formato es
  ``enc:<key_id>:<token>`` para poder descifrarlo tras una rotación de claves.
- Si el envío falla, la fila queda con ``intentos`` incrementado y
  ``ultimo_error``; :func:`reintentar_pendientes` la reintenta hasta 5 veces.

Reglas de seguridad:

- ``destinatario`` y ``asunto`` se sanean contra inyección de cabeceras: un
  salto de línea en cualquiera de los dos descarta el correo (``False``).
- :func:`enviar` **nunca** lanza: un fallo de SMTP se convierte en ``False`` y
  queda registrado, para que el flujo de login pueda mostrar un error
  controlado sin romper la petición.
- El cuerpo sensible no se escribe en logs ni en la tabla sin cifrar. Si no hay
  claves de cifrado configuradas, se conserva la fila de auditoría pero sin
  cuerpo recuperable (se avisa en el log, sin contenido).
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from secrets import token_hex

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import secret_box
from app.models import EmailOutbox

__all__ = [
    "MAX_INTENTOS",
    "enviar",
    "reintentar_pendientes",
]

log = logging.getLogger(__name__)

#: Intentos máximos de reenvío de una fila de la cola.
MAX_INTENTOS = 5
#: Marca del cuerpo cifrado en ``email_outbox.cuerpo``.
PREFIJO_CIFRADO = "enc:"
#: Tope de caracteres del error persistido (no hay secretos en él).
_MAX_ERROR = 400


def _utcnow() -> datetime:
    """UTC *naive* actual, compatible con las columnas ``DateTime`` del esquema.

    Mismo criterio que :func:`app.core.security.utcnow`: se pide el instante con
    zona y se le quita ``tzinfo``. ``datetime.utcnow()`` está deprecado desde
    Python 3.12 y emitía un ``DeprecationWarning`` por cada correo emitido.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Saneado de cabeceras ------------------------------------------------------
def _cabecera(valor: str | None, campo: str) -> str:
    """Valida una cabecera y rechaza saltos de línea (header injection)."""
    texto = (valor or "").strip()
    if not texto:
        raise ValueError(f"La cabecera «{campo}» no puede estar vacía.")
    if any(caracter in texto for caracter in ("\r", "\n", "\x00")):
        raise ValueError(f"La cabecera «{campo}» contiene caracteres no permitidos.")
    return texto


# --- Cuerpo: sobre JSON + cifrado opcional -------------------------------------
def _sobre(texto: str | None, html: str | None) -> str:
    """Serializa texto y HTML en un único campo ``cuerpo``."""
    return json.dumps({"texto": texto or "", "html": html or ""},
                      ensure_ascii=False)


def _leer_sobre(crudo: str | None) -> tuple[str, str | None] | None:
    """Devuelve ``(texto, html)`` o ``None`` si el cuerpo no es recuperable."""
    if not crudo:
        return None
    if crudo.startswith(PREFIJO_CIFRADO):
        texto = _descifrar(crudo)
        if texto is None:
            return None
        crudo = texto
    try:
        datos = json.loads(crudo)
    except (TypeError, ValueError):
        # Compatibilidad con filas escritas como texto plano.
        return crudo, None
    if not isinstance(datos, dict) or "texto" not in datos:
        return crudo, None
    html = datos.get("html") or None
    return str(datos.get("texto") or ""), (str(html) if html else None)


def _cifrar(sobre: str) -> str:
    """Cifra el sobre y lo marca con ``enc:<key_id>:<token>``."""
    try:
        blob, key_id = secret_box().encrypt(sobre)
    except (RuntimeError, ValueError):
        # Sin claves (por ejemplo, autenticación apagada en desarrollo) no se
        # guarda el cuerpo en claro: se conserva solo la evidencia del envío.
        log.warning("Correo sensible sin claves de cifrado: no se conserva el cuerpo.")
        return PREFIJO_CIFRADO
    return f"{PREFIJO_CIFRADO}{key_id}:{blob.decode('ascii')}"


def _descifrar(crudo: str) -> str | None:
    """Descifra ``enc:<key_id>:<token>``; ``None`` si no se puede."""
    resto = crudo[len(PREFIJO_CIFRADO):]
    identificador, separador, token = resto.partition(":")
    if not separador or not identificador or not token:
        return None
    try:
        return secret_box().decrypt(token.encode("ascii"), identificador)
    except (RuntimeError, ValueError, UnicodeEncodeError):
        return None


def cuerpo_para_cola(texto: str | None, html: str | None,
                     sensible: bool) -> str:
    """Cuerpo que se persiste en ``email_outbox`` (cifrado si es sensible)."""
    sobre = _sobre(texto, html)
    return _cifrar(sobre) if sensible else sobre


# --- Construcción y entrega ----------------------------------------------------
def _mensaje(destinatario: str, asunto: str, texto: str,
             html: str | None) -> EmailMessage:
    """Arma el ``EmailMessage`` con texto y HTML mínimo (sin imágenes)."""
    mensaje = EmailMessage()
    try:
        remitente = _cabecera(settings.email_from, "remitente")
    except ValueError:
        remitente = "no-reply@localhost"
    mensaje["From"] = remitente
    mensaje["To"] = destinatario
    mensaje["Subject"] = asunto
    mensaje.set_content(texto or "")
    if html:
        mensaje.add_alternative(html, subtype="html")
    return mensaje


def _escribir_eml(mensaje: EmailMessage) -> None:
    """Escribe el correo como ``.eml`` único en ``settings.email_dir``."""
    carpeta = settings.email_path
    nombre = f"{_utcnow():%Y%m%d-%H%M%S}-{token_hex(6)}.eml"
    (carpeta / nombre).write_bytes(mensaje.as_bytes(policy=policy.SMTP))


def _enviar_smtp(mensaje: EmailMessage) -> None:
    """Entrega por SMTP autenticado con timeout de 10 segundos."""
    host = (settings.smtp_host or "").strip()
    if not host:
        raise RuntimeError("SMTP sin host configurado.")
    contexto = ssl.create_default_context()
    with smtplib.SMTP(host, int(settings.smtp_port), timeout=10) as cliente:
        cliente.ehlo()
        if settings.smtp_starttls:
            cliente.starttls(context=contexto)
            cliente.ehlo()
        if settings.smtp_user:
            cliente.login(settings.smtp_user, settings.smtp_password or "")
        cliente.send_message(mensaje)


def _entregar(destinatario: str, asunto: str, texto: str,
              html: str | None) -> None:
    """Despacha según ``settings.email_backend``; lanza si el envío falla."""
    backend = (settings.email_backend or "console").strip().lower()
    mensaje = _mensaje(destinatario, asunto, texto, html)
    if backend == "file":
        _escribir_eml(mensaje)
        return
    if backend == "smtp":
        _enviar_smtp(mensaje)
        return
    # console (y cualquier valor desconocido): desarrolla sin enviar y sin
    # volcar el cuerpo en el log.
    log.info("Correo (console) para %s, asunto «%s»; cuerpo no registrado.",
             destinatario, asunto)


# --- Cola de salida ------------------------------------------------------------
def _error_breve(error: BaseException) -> str:
    """Mensaje de error acotado y sin volcar el cuerpo ni credenciales."""
    detalle = str(error).replace("\n", " ").strip()
    return f"{type(error).__name__}: {detalle}"[:_MAX_ERROR]


def _registrar(destinatario: str, asunto: str, cuerpo: str) -> int | None:
    """Inserta la fila de la cola y devuelve su id (``None`` si falla)."""
    try:
        with SessionLocal() as db:
            fila = EmailOutbox(
                destinatario=destinatario[:320],
                asunto=asunto[:300],
                cuerpo=cuerpo,
                intentos=0,
            )
            db.add(fila)
            db.commit()
            return int(fila.id)
    except Exception:  # la cola nunca debe romper el flujo que envía el correo
        log.exception("No se pudo registrar el correo en la cola de salida.")
        return None


def _marcar(fila_id: int | None, *, error: BaseException | None = None) -> None:
    """Cierra la fila: enviada o con el último error."""
    if fila_id is None:
        return
    try:
        with SessionLocal() as db:
            fila = db.get(EmailOutbox, fila_id)
            if fila is None:
                return
            if error is None:
                fila.enviado_en = _utcnow()
                fila.ultimo_error = None
                # ``intentos`` cuenta los despachos: el primero también cuenta.
                fila.intentos = max(1, int(fila.intentos or 0))
            else:
                fila.intentos = int(fila.intentos or 0) + 1
                fila.ultimo_error = _error_breve(error)
            db.commit()
    except Exception:
        log.warning("No se pudo actualizar el estado del correo id=%s.", fila_id)


def enviar(destinatario: str, asunto: str, texto: str,
           html: str | None = None, sensible: bool = False) -> bool:
    """Envía un correo y lo registra en la cola. Nunca lanza.

    Devuelve ``True`` si el backend aceptó el mensaje. Un asunto o destinatario
    con saltos de línea se descarta sin enviar ni registrar (intento de
    inyección de cabeceras).
    """
    try:
        destino = _cabecera(destinatario, "destinatario")
        titulo = _cabecera(asunto, "asunto")
    except ValueError:
        log.warning("Correo descartado: cabecera inválida (destinatario o asunto).")
        return False

    cuerpo = cuerpo_para_cola(texto, html, sensible)
    fila_id = _registrar(destino, titulo, cuerpo)
    try:
        _entregar(destino, titulo, texto or "", html)
    except Exception as exc:  # SMTP caído, DNS, TLS, autenticación...
        log.warning("Falló el envío de correo a %s (%s).",
                    destino, type(exc).__name__)
        _marcar(fila_id, error=exc)
        return False
    _marcar(fila_id)
    return True


def reintentar_pendientes(db: Session, limit: int = 20) -> dict[str, int]:
    """Reenvía los correos pendientes con ``intentos < 5``.

    Pensado para un cron o comando de mantenimiento: **no** se ejecuta solo al
    arrancar. Descifra el cuerpo si empieza con ``enc:`` y actualiza
    ``intentos``, ``enviado_en`` y ``ultimo_error`` en la misma sesión recibida.
    """
    tope = max(1, int(limit))
    pendientes = list(db.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.enviado_en.is_(None),
               EmailOutbox.intentos < MAX_INTENTOS)
        .order_by(EmailOutbox.created_at, EmailOutbox.id)
        .limit(tope)
    ))
    enviados = fallidos = 0
    for fila in pendientes:
        fila.intentos = int(fila.intentos or 0) + 1
        contenido = _leer_sobre(fila.cuerpo)
        if contenido is None:
            fila.ultimo_error = "Cuerpo no recuperable (cifrado o formato inválido)."
            db.commit()
            fallidos += 1
            continue
        texto, html = contenido
        try:
            _entregar(fila.destinatario, fila.asunto, texto, html)
        except Exception as exc:
            fila.ultimo_error = _error_breve(exc)
            db.commit()
            fallidos += 1
        else:
            fila.enviado_en = _utcnow()
            fila.ultimo_error = None
            db.commit()
            enviados += 1
    return {"revisados": len(pendientes), "enviados": enviados,
            "fallidos": fallidos}
