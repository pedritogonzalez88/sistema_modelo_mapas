"""Avisos de seguridad por correo (sin exponer secretos)."""
from html import escape

from app.auth import mailer
from app.core.config import settings
from app.core.security import utcnow
from app.models import Usuario


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
