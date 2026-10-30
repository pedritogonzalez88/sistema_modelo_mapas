"""Middlewares de autenticación y cabeceras (Fases A1 y A6).

``AuthGateMiddleware`` es el único punto que convierte una cookie en identidad
para el resto de la petición. Con ``EET_AUTH_ENABLED=false`` no interviene en
absoluto: no consulta la base, no crea cookies y no altera la respuesta, de
modo que la aplicación se comporta igual que antes de la Fase A1.

``SecurityHeadersMiddleware`` (Fase A6a) genera un **nonce por petición** para
la CSP, lo publica en ``request.state.csp_nonce`` (las plantillas lo usan en
cada ``<script>`` inline) y fija el resto de cabeceras de endurecimiento.

Orden de la pila (``create_app``): ``ProxyHeaders`` (si hay proxy de confianza)
por fuera de ``SecurityHeaders``, este por fuera de ``AuthGate`` y ``AuthGate``
por fuera de los routers, para que las cabeceras también viajen en las
redirecciones al login.
"""
from __future__ import annotations

import secrets
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
    "COOKIE_MFA_DESARROLLO",
    "COOKIE_SESION_DESARROLLO",
    "SecurityHeadersMiddleware",
    "aplicar_cookie_mfa",
    "aplicar_cookie_sesion",
    "borrar_cookie_mfa",
    "borrar_cookie_sesion",
    "es_ruta_publica",
    "exige_enrolamiento",
    "nombre_cookie_mfa",
    "nombre_cookie_sesion",
    "ruta_segura",
]

# Prefijo ``__Host-`` obliga a Secure; en desarrollo local (http) se usa un
# nombre plano o el navegador descartaría la cookie.
COOKIE_SESION_DESARROLLO = "eet_session"
COOKIE_MFA_DESARROLLO = "eet_mfa"

# Vida del challenge de segundo factor (Fase A3): 5 minutos.
MFA_MAX_AGE = 300

# Rutas que no exigen sesión. ``/auth/logout`` es público a propósito: el
# cierre de sesión debe ser idempotente aunque la cookie ya no sea válida.
# ``/auth/mfa`` también: entre la contraseña y el segundo factor el usuario
# todavía **no** tiene sesión (la sesión nace al superar el desafío).
RUTAS_PUBLICAS = frozenset({
    "/auth/login", "/auth/logout", "/auth/mfa", "/auth/mfa/enviar",
    "/auth/recuperar", "/auth/restablecer", "/salud", "/favicon.ico",
})
PREFIJOS_PUBLICOS = ("/static/",)

# Rutas que ni siquiera abren una sesión de base de datos.
_RUTAS_SIN_SESION = frozenset({"/salud", "/favicon.ico"})

# Con ``must_change_password`` la única navegación permitida es el cambio.
RUTAS_CAMBIO_PASSWORD = frozenset({"/auth/cambiar-password", "/auth/logout"})

# Con la política TOTP y sin enrolar, solo se permite completar el enrolamiento
# (Fase A3). El resto redirige a ``/auth/seguridad``.
RUTAS_ENROLAMIENTO = frozenset({"/auth/seguridad", "/auth/logout"})
PREFIJOS_ENROLAMIENTO = ("/auth/seguridad/",)


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


def nombre_cookie_mfa() -> str:
    """Nombre de la cookie del challenge de segundo factor."""
    return "__Host-eet_mfa" if settings.cookie_secure else COOKIE_MFA_DESARROLLO


def aplicar_cookie_mfa(respuesta: Response, token: str) -> None:
    """Cookie del challenge: HttpOnly, SameSite=Lax y 5 minutos de vida."""
    respuesta.set_cookie(
        nombre_cookie_mfa(), token,
        max_age=MFA_MAX_AGE,
        path="/", httponly=True, samesite="lax", secure=settings.cookie_secure,
    )


def borrar_cookie_mfa(respuesta: Response) -> None:
    respuesta.delete_cookie(nombre_cookie_mfa(), path="/")


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


def _permite_enrolamiento(ruta: str) -> bool:
    return (ruta in RUTAS_ENROLAMIENTO
            or ruta.startswith(PREFIJOS_ENROLAMIENTO)
            or ruta.startswith(PREFIJOS_PUBLICOS))


def exige_enrolamiento(usuario: Usuario) -> bool:
    """``True`` si su política exige TOTP y todavía no lo activó (Fase A3).

    Se lee ``mfa_enrolled_at`` (marcado junto con ``mfa_totp.confirmed_at``):
    el gate no necesita descifrar secretos para decidir.
    """
    return ((usuario.mfa_policy or "").strip().lower() == "totp"
            and usuario.mfa_enrolled_at is None)


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

            if usuario.must_change_password:
                if not _permite_cambio_password(request.url.path):
                    destino = request.url.path
                    if request.url.query:
                        destino = f"{destino}?{request.url.query}"
                    if destino.startswith("/auth/cambiar-password"):
                        destino = "/"
                    return csrf.poner_cookie_csrf(
                        request, RedirectResponse(
                            "/auth/cambiar-password?next="
                            f"{quote(ruta_segura(destino), safe='')}",
                            status_code=303))
            # Fase A3: la política TOTP obliga a enrolarse antes de operar.
            # Se evalúa después del cambio obligatorio (nunca en paralelo: con
            # ambos pendientes, el gate rebotaría entre las dos pantallas).
            elif exige_enrolamiento(usuario) and not _permite_enrolamiento(
                    request.url.path):
                return csrf.poner_cookie_csrf(
                    request, RedirectResponse("/auth/seguridad",
                                              status_code=303))

            return csrf.poner_cookie_csrf(request, await call_next(request))
        finally:
            db.close()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Cabeceras de endurecimiento del navegador y CSP con nonce por petición.

    La CSP permite ``'unsafe-inline'`` **solo** en ``style-src``: el código usa
    atributos ``style="width:NN%"`` en las barras de progreso y en la dona del
    panel, y convertirlos a clases obligaría a generar CSS dinámico en el
    servidor. ``script-src`` nunca acepta ``'unsafe-inline'``: cada ``<script>``
    inline lleva el ``nonce`` de la petición (ver ``request.state.csp_nonce``).

    HSTS se emite solo cuando la petición llegó por HTTPS: mandarlo por HTTP no
    surte efecto y, en desarrollo local, impediría volver a entrar por http.
    """

    CABECERAS = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }

    HSTS = "max-age=31536000; includeSubDomains"

    @staticmethod
    def plantilla_csp(nonce: str) -> str:
        """Política completa con el nonce de esta petición."""
        return (
            "default-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "connect-src 'self'"
        )

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        # El nonce se genera antes de renderizar: las plantillas lo consumen.
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        respuesta = await call_next(request)

        for nombre, valor in self.CABECERAS.items():
            respuesta.headers.setdefault(nombre, valor)
        respuesta.headers.setdefault(
            "Content-Security-Policy", self.plantilla_csp(nonce))
        if request.url.scheme == "https":
            respuesta.headers.setdefault("Strict-Transport-Security", self.HSTS)

        # Pantallas de autenticación: contienen códigos, tokens y datos de
        # sesión. No se pisa un ``Cache-Control`` más específico del handler.
        ruta = request.url.path
        if ruta.startswith("/auth/") and not ruta.startswith("/auth/static/"):
            respuesta.headers.setdefault("Cache-Control", "no-store")
        return respuesta
