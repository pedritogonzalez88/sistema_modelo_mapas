"""Punto de entrada de la aplicación.

``create_app()`` permite montar una instancia limpia en pruebas y en tareas
(CLI, scripts) sin reutilizar el estado global; ``app = create_app()`` es la
que consume Uvicorn. Los middlewares se agregan al final: Starlette construye
la pila en orden inverso, así que ``SecurityHeaders`` queda por fuera de
``AuthGate`` y este por fuera de los routers.
"""
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.auth.middleware import AuthGateMiddleware, SecurityHeadersMiddleware
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.database import SessionLocal, engine, init_db
from app.routers import cruces, dashboard, entregas, exportar, malla, mapa, qaqc, reemplazos
from app.services import persist
from app.web import templates

logger = logging.getLogger("eet.acceso")

_RE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Registra método, ruta, estado y duración sin cuerpos ni query strings.

    Añade ``X-Request-ID`` para correlacionar logs. Los healthchecks exitosos
    se registran en DEBUG para no inundar el journal; los 5xx siempre en WARNING.
    """

    async def dispatch(self, request: Request, call_next):
        inicio = time.perf_counter()
        solicitado = request.headers.get("X-Request-ID", "")
        request_id = solicitado if _RE_REQUEST_ID.fullmatch(solicitado) else uuid.uuid4().hex
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            duracion = (time.perf_counter() - inicio) * 1000
            logger.exception(
                "request_id=%s metodo=%s ruta=%s estado=500 duracion_ms=%.1f",
                request_id, request.method, request.url.path, duracion,
            )
            raise
        duracion = (time.perf_counter() - inicio) * 1000
        response.headers["X-Request-ID"] = request_id
        ruta = request.url.path
        nivel = logging.WARNING if response.status_code >= 500 else logging.INFO
        if (ruta in {"/salud", "/health", "/ready"} and response.status_code < 500) \
                or ruta.startswith("/static/"):
            nivel = logging.DEBUG
        logger.log(
            nivel,
            "request_id=%s metodo=%s ruta=%s estado=%s duracion_ms=%.1f",
            request_id, request.method, ruta, response.status_code, duracion,
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepara la base y siembra los datos mínimos antes de atender tráfico.

    Se usa el ciclo de vida `lifespan` (y no `@app.on_event`) porque es el
    mecanismo vigente de FastAPI. La sesión se abre con `SessionLocal` y se
    cierra en `finally`: `next(get_db())` dejaría el generador sin finalizar.
    `persist.seed` es idempotente, así que repetir el arranque no duplica datos.
    """
    # DDL solo en desarrollo/tests. En staging/producción el esquema lo
    # administra Alembic (el contenedor ejecuta `alembic upgrade head` antes de
    # Uvicorn); create_all/ALTER en runtime quedaría fuera de control.
    if settings.environment == "dev":
        init_db()
    db = SessionLocal()
    try:
        # Producción no crea usuarios nominales ni datos de demostración: el
        # administrador se crea con scripts/bootstrap_admin.py.
        if settings.environment != "prod":
            persist.seed(db)
    finally:
        db.close()
    yield


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Errores HTTP como HTML legible, salvo rutas de máquina.

    JSON solo para /salud, /api* o cuando el cliente pide JSON explícitamente;
    el resto de la aplicación muestra error.html con navegación y sin trazas.
    El 403 de CSRF y el 401 del gate también pasan por aquí.
    """
    if request.url.path.startswith(("/salud", "/ready", "/api")) or \
            "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request, "error.html",
        {"codigo": exc.status_code, "mensaje": exc.detail, "proyecto": None},
        status_code=exc.status_code,
    )


def create_app() -> FastAPI:
    """Construye la aplicación completa (middlewares, routers, static)."""
    # Con autenticación activa y exposición de documentación desactivada, el
    # esquema OpenAPI tampoco se publica: describe la superficie interna.
    ocultar_docs = settings.auth_enabled and not settings.expose_docs

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url=None if ocultar_docs else "/docs",
        redoc_url=None if ocultar_docs else "/redoc",
        openapi_url=None if ocultar_docs else "/openapi.json",
    )
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(auth_router)
    app.include_router(dashboard.router)
    app.include_router(entregas.router)
    app.include_router(cruces.router)
    app.include_router(qaqc.router)
    app.include_router(reemplazos.router)
    app.include_router(malla.router)
    app.include_router(mapa.router)
    app.include_router(exportar.router)

    @app.get("/salud")
    def salud():
        return {"estado": "ok"}

    @app.get("/health")
    def health():
        """Alias de ``/salud`` para monitores que usan la convención /health."""
        return {"estado": "ok"}

    @app.get("/ready")
    def ready():
        """Readiness real: comprueba que la base responde antes de dar tráfico."""
        try:
            with engine.connect() as conexion:
                conexion.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - solo se informa el estado
            # Sin traza ni cadena de conexión: podría contener credenciales.
            logger.error("readiness: la base de datos no responde")
            return JSONResponse(
                {"estado": "no_listo", "base": "error"}, status_code=503)
        return {"estado": "listo", "base": "ok"}

    @app.get("/inicio")
    def inicio():
        return RedirectResponse("/", status_code=302)

    # Orden de la pila: el último `add_middleware` queda más afuera. El proxy
    # de confianza va delante de todo para que `request.url.scheme` y
    # `request.client.host` reflejen lo que informó el reverse proxy.
    app.add_middleware(AuthGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    proxies = settings.trusted_proxy_hosts
    if proxies:
        # Solo se aceptan X-Forwarded-Proto/For de estas IPs/CIDR. Sin la
        # variable, `request.client.host` es la IP real de la conexión y el
        # rate limiting nunca se puede falsear con una cabecera.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=proxies)
    return app


app = create_app()
