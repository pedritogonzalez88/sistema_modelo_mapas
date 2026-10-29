"""Middlewares de autenticación y cabeceras (Fase A1).

``AuthGateMiddleware`` es el único punto que convierte una cookie en identidad
para el resto de la petición. Con ``EET_AUTH_ENABLED=false`` no interviene en
absoluto: no consulta la base, no crea cookies y no altera la respuesta, de
modo que la aplicación se comporta igual que antes de la Fase A1.

Orden de la pila (``create_app``): ``SecurityHeaders`` por fuera de
``AuthGate``, y ``AuthGate`` por fuera de los routers, para que las cabeceras
también viajen en las redirecciones al login.
"""
from __future__ import annotations

from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.auth import csrf
from app.auth.service import cargar_sesion, revocar_sesion, sesion_para_csrf
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Usuario

__all__ = [
    "AuthGateMiddleware",
    "COOKIE_SESION_DESARROLLO",
    "SecurityHeadersMiddleware",
    "aplicar_cookie_sesion",
    "borrar_cookie_sesion",
    "es_ruta_publica",
    "nombre_cookie_sesion",
    "ruta_segura",
]

# Prefijo ``__Host-`` obliga a Secure; en desarrollo local (http) se usa un
# nombre plano o el navegador descartaría la cookie.
COOKIE_SESION_DESARROLLO = "eet_session"

# Rutas que no exigen sesión. ``/auth/logout`` es público a propósito: el
# cierre de sesión debe ser idempotente aunque la cookie ya no sea válida.
RUTAS_PUBLICAS = frozenset({
    "/auth/login", "/auth/logout", "/auth/recuperar", "/auth/restablecer",
    "/salud", "/favicon.ico",
})
PREFIJOS_PUBLICOS = ("/static/",)

# Rutas que ni siquiera abren una sesión de base de datos.
_RUTAS_SIN_SESION = frozenset({"/salud", "/favicon.ico"})

# Con ``must_change_password`` la única navegación permitida es el cambio.
RUTAS_CAMBIO_PASSWORD = frozenset({"/auth/cambiar-password", "/auth/logout"})


def nombre_cookie_sesion() -> str:
    """Nombre de la cookie de sesión según el transporte actual."""
    return settings.session_cookie if settings.cookie_secure else COOKIE_SESION_DESARROLLO


def aplicar_cookie_sesion(respuesta: Response, token: str) -> None:
    """Escribe la cookie de sesión con los atributos de la política."""
    respuesta.set_cookie(
        nombre_cookie_sesion(), token,
        max_age=max(60, int(settings.session_absolute_min) * 60),
        path="/", httponly=True, samesite="lax", secure=settings.cookie_secure,
    )


def borrar_cookie_sesion(respuesta: Response) -> None:
    respuesta.delete_cookie(nombre_cookie_sesion(), path="/")


def es_ruta_publica(ruta: str) -> bool:
    return ruta in RUTAS_PUBLICAS or ruta.startswith(PREFIJOS_PUBLICOS)


def ruta_segura(destino: str | None, por_defecto: str = "/") -> str:
    """Acepta solo rutas internas (``/algo``), nunca absolutas ni ``//host``."""
    if not destino:
        return por_defecto
    candidato = str(destino).strip()
    if not candidato.startswith("/") or candidato.startswith("//"):
        return por_defecto
    if "\\" in candidato or "\r" in candidato or "\n" in candidato:
        return por_defecto
    return candidato


def _pide_json(request: Request) -> bool:
    acepta = request.headers.get("accept", "").lower()
    return "application/json" in acepta or request.url.path.startswith("/api")


def _respuesta_sin_sesion(request: Request) -> Response:
    if _pide_json(request):
        return JSONResponse({"detail": "Sesión requerida."}, status_code=401)
    destino = request.url.path
    if request.url.query:
        destino = f"{destino}?{request.url.query}"
    return RedirectResponse(
        f"/auth/login?next={quote(ruta_segura(destino), safe='')}", status_code=303)


def _permite_cambio_password(ruta: str) -> bool:
    return ruta in RUTAS_CAMBIO_PASSWORD or ruta.startswith(PREFIJOS_PUBLICOS)


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Exige sesión válida y publica usuario, sesión y token CSRF."""

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        if not settings.auth_enabled:
            return await call_next(request)

        # Recursos estáticos y de salud: no necesitan identidad ni sesión de
        # base de datos. Se evita abrir una conexión por cada asset.
        ruta = request.url.path
        if ruta in _RUTAS_SIN_SESION or ruta.startswith(PREFIJOS_PUBLICOS):
            return await call_next(request)

        db = SessionLocal()
        try:
            request.state.db_auth = db
            request.state.usuario = None
            request.state.sesion = None
            # El token CSRF se prepara siempre: el formulario de login lo usa
            # incluso sin sesión y así el input oculto nunca sale vacío.
            csrf.asegurar_csrf_cookie(request)

            if es_ruta_publica(request.url.path):
                # El logout debe validar el CSRF con el token de la sesión que
                # se está cerrando, incluso si ya caducó (página abierta mucho
                # tiempo). Se carga la fila sin autenticarla.
                if ruta == "/auth/logout":
                    request.state.sesion = await run_in_threadpool(
                        sesion_para_csrf, db,
                        request.cookies.get(nombre_cookie_sesion()))
                return csrf.poner_cookie_csrf(request, await call_next(request))

            token = request.cookies.get(nombre_cookie_sesion())
            sesion = await run_in_threadpool(cargar_sesion, db, token)
            if sesion is None:
                return csrf.poner_cookie_csrf(
                    request, _respuesta_sin_sesion(request))

            usuario = await run_in_threadpool(db.get, Usuario, sesion.usuario_id)
            if usuario is None or not usuario.is_active:
                # Baja lógica o fila borrada: la sesión deja de servir.
                await run_in_threadpool(revocar_sesion, db, sesion)
                return csrf.poner_cookie_csrf(
                    request, _respuesta_sin_sesion(request))

            request.state.usuario = usuario
            request.state.sesion = sesion

            if usuario.must_change_password and not _permite_cambio_password(
                    request.url.path):
                return csrf.poner_cookie_csrf(
                    request, RedirectResponse("/auth/cambiar-password",
                                              status_code=303))

            return csrf.poner_cookie_csrf(request, await call_next(request))
        finally:
            db.close()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Cabeceras mínimas de endurecimiento del navegador.

    La CSP con nonce y HSTS son parte de la Fase A6: aquí solo se fijan las
    tres cabeceras que no pueden romper la interfaz actual.
    """

    CABECERAS = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
    }

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        respuesta = await call_next(request)
        for nombre, valor in self.CABECERAS.items():
            respuesta.headers.setdefault(nombre, valor)
        return respuesta
