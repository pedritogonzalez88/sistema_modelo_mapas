from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.database import get_db, init_db
from app.routers import cruces, dashboard, entregas, exportar, malla, mapa, qaqc, reemplazos
from app.services import persist
from app.web import templates

app = FastAPI(title=settings.app_name)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Errores HTTP como HTML legible, salvo rutas de máquina.

    JSON solo para /salud, /api* o cuando el cliente pide JSON explícitamente;
    el resto de la aplicación muestra error.html con navegación y sin trazas.
    """
    if request.url.path.startswith(("/salud", "/api")) or \
            "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request, "error.html",
        {"codigo": exc.status_code, "mensaje": exc.detail, "proyecto": None},
        status_code=exc.status_code,
    )

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(dashboard.router)
app.include_router(entregas.router)
app.include_router(cruces.router)
app.include_router(qaqc.router)
app.include_router(reemplazos.router)
app.include_router(malla.router)
app.include_router(mapa.router)
app.include_router(exportar.router)


@app.on_event("startup")
def _startup():
    init_db()
    db: Session = next(get_db())
    try:
        persist.seed(db)
    finally:
        db.close()


@app.get("/salud")
def salud():
    return {"estado": "ok"}


@app.get("/inicio")
def inicio():
    return RedirectResponse("/", status_code=302)
