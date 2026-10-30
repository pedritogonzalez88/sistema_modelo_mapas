"""Protección CSRF (Fase A1).

Modelo **double-submit** para el login anónimo y **token por sesión** para el
resto de la aplicación:

1. Antes de existir sesión, el middleware crea un token aleatorio de 32 bytes y
   lo guarda en la cookie ``eet_csrf`` (``HttpOnly``, ``SameSite=Lax``). El
   formulario de login lleva el mismo valor en el campo oculto ``csrf_token``:
   un sitio externo puede provocar el POST, pero no puede leer la cookie para
   copiar el valor.
2. Con sesión iniciada el token válido es ``sesion.csrf_token``, derivado con
   HMAC-SHA256 del hash del token de sesión y de ``EET_SECRET_KEY``. Así el
   token es distinto por sesión, no depende de una columna extra en ``sesiones``
   (el esquema de A0 está congelado por sus pruebas) y rotar la sesión invalida
   el token anterior de forma automática.

Además se valida ``Origin``/``Referer`` cuando el navegador los envía: deben
coincidir en esquema y host con ``request.base_url``. Un token correcto enviado
desde otro origen se rechaza igual.

Con ``EET_AUTH_ENABLED=false`` el token es vacío y :func:`csrf_protect` no
valida nada: la aplicación se comporta exactamente como antes de la Fase A1.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import Response

from app.core.config import settings
from app.core.security import constant_time_eq, new_token

__all__ = [
    "COOKIE_CSRF",
    "asegurar_csrf_cookie",
    "csrf_protect",
    "poner_cookie_csrf",
    "token_actual",
    "validar_csrf",
]

# Nombre de la cookie anónima. No usa el prefijo __Host- porque en desarrollo
# local la cookie no viaja con Secure y el navegador la descartaría.
COOKIE_CSRF = "eet_csrf"

_MENSAJE_CSRF = "Token CSRF inválido o ausente."


def _max_age() -> int:
    return max(60, int(settings.session_absolute_min) * 60)


def asegurar_csrf_cookie(request: Request) -> str:
    """Devuelve el token anónimo y lo deja en ``request.state``.

    Si la petición ya trae la cookie se reutiliza (el que envía el formulario
    debe coincidir con el que ya tiene el navegador); si no, se genera uno
    nuevo y se marca para que el middleware lo escriba en la respuesta.
    """
    existente = request.cookies.get(COOKIE_CSRF)
    if existente:
        request.state.csrf_cookie = existente
        request.state.csrf_cookie_nueva = False
        return existente
    nuevo = new_token(32)
    request.state.csrf_cookie = nuevo
    request.state.csrf_cookie_nueva = True
    return nuevo


def poner_cookie_csrf(request: Request, respuesta: Response) -> Response:
    """Escribe la cookie CSRF en la respuesta si se generó en esta petición."""
    if not getattr(request.state, "csrf_cookie_nueva", False):
        return respuesta
    token = getattr(request.state, "csrf_cookie", None)
    if token:
        respuesta.set_cookie(
            COOKIE_CSRF, token, max_age=_max_age(), path="/",
            httponly=True, samesite="lax", secure=settings.cookie_secure,
        )
    return respuesta


def token_actual(request: Request) -> str:
    """Token CSRF que debe llevar el formulario de esta petición.

    Con sesión se usa el token derivado de la sesión; sin sesión, la cookie
    anónima (creándola si el middleware todavía no la preparó). Con la bandera
    de autenticación apagada devuelve cadena vacía: no hay nada que proteger y
    no se emiten cookies nuevas.
    """
    if not settings.auth_enabled:
        return ""
    sesion = getattr(request.state, "sesion", None)
    token_sesion = getattr(sesion, "csrf_token", None) if sesion is not None else None
    if token_sesion:
        return token_sesion
    token = getattr(request.state, "csrf_cookie", None)
    if not token:
        token = new_token(32)
        request.state.csrf_cookie = token
        request.state.csrf_cookie_nueva = True
    return token


def _origen_valido(request: Request, cabecera: str | None) -> bool:
    """Compara esquema y host de ``Origin``/``Referer`` con la petición.

    ``Referrer-Policy: no-referrer`` (endurecimiento de A6a para no filtrar
    tokens en el ``Referer``) hace que el navegador envíe ``Origin: null`` en
    los POST de formulario **aunque sean del mismo origen**: la especificación
    de Fetch sustituye el origen serializado por ``null`` cuando la política de
    referrer es ``no-referrer``. Un ``null`` no se puede comparar con el host,
    así que se acepta solo si ``Sec-Fetch-Site`` confirma que la petición es
    del mismo origen (cabecera que fija el navegador y que una página no puede
    falsear). Sin esa cabecera —clientes que no son navegadores— rige la
    validación por token, que es el control principal.
    """
    if not cabecera:
        return True
    partes = urlsplit(cabecera)
    if not partes.scheme or not partes.netloc:
        # Origen opaco (``null``) o valor no parseable: no comparables.
        mismo_origen = (request.headers.get("sec-fetch-site") or "").strip().lower()
        return mismo_origen in ("", "same-origin")
    base = request.base_url
    return (partes.scheme == base.scheme
            and partes.netloc.casefold() == base.netloc.casefold())


def validar_csrf(request: Request, form_token: str | None) -> bool:
    """Valida el token del formulario y, si viene, el origen de la petición."""
    if not settings.auth_enabled:
        return True
    esperado = token_actual(request)
    recibido = form_token or ""
    if not esperado or not recibido:
        return False
    if not constant_time_eq(esperado, recibido):
        return False
    return (_origen_valido(request, request.headers.get("origin"))
            and _origen_valido(request, request.headers.get("referer")))


async def csrf_protect(request: Request) -> None:
    """Dependencia FastAPI para todos los POST con formulario.

    Lee el cuerpo con ``request.form()`` (Starlette lo cachea, así que el
    router lo vuelve a leer sin consumir el stream) y responde 403 HTML a
    través del manejador de errores de la aplicación.
    """
    if not settings.auth_enabled:
        return
    try:
        formulario = await request.form()
    except Exception:  # cuerpo ausente, JSON inesperado o multipart corrupto
        raise HTTPException(status_code=403, detail=_MENSAJE_CSRF) from None
    if not validar_csrf(request, formulario.get("csrf_token")):
        raise HTTPException(status_code=403, detail=_MENSAJE_CSRF)
