import mimetypes
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.audit import actor_de_request
from app.auth.csrf import csrf_protect
from app.auth.dependencies import require_permiso
from app.core.config import settings
from app.core.database import get_db
from app.models import PuntoMalla
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/malla",
                   dependencies=[Depends(require_permiso("malla.ver"))])


def _validar_tamano(tamano: int | None) -> None:
    """Rechaza la carga antes de procesarla si supera ``max_upload_mb`` (A6a)."""
    if tamano is None:
        return
    limite_mb = int(settings.max_upload_mb)
    if tamano > limite_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el límite de {limite_mb} MB.",
        )

# Windows registra .csv como application/vnd.ms-excel; se fija text/csv para
# que la plantilla de ejemplo (/static/plantillas/malla.csv) se descargue como
# CSV en cualquier plataforma (el Mount de /static resuelve el tipo al servir).
mimetypes.add_type("text/csv", ".csv")

_PER_PAGE = (25, 50, 100)


@router.get("", response_class=HTMLResponse)
def pagina(request: Request, q: str | None = None, page: int = 1,
           per_page: int = 50, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    if per_page not in _PER_PAGE:
        per_page = 50
    page = max(1, page)
    texto = (q or "").strip()

    total = db.scalar(select(func.count()).select_from(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id)) or 0
    reservas = db.scalar(select(func.count()).select_from(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id, PuntoMalla.es_reserva.is_(True))) or 0
    con_peso = db.scalar(select(func.count()).select_from(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id, PuntoMalla.peso.is_not(None))) or 0

    # Búsqueda por punto exacto o por coincidencia parcial de estrato.
    cond = [PuntoMalla.proyecto_id == proyecto.id]
    if texto:
        if texto.isdigit():
            cond.append(PuntoMalla.punto_id == int(texto))
        else:
            cond.append(PuntoMalla.estrato.ilike(f"%{texto}%"))

    total_filtrado = db.scalar(
        select(func.count()).select_from(PuntoMalla).where(*cond)) or 0
    pages = max(1, ceil(total_filtrado / per_page))
    page = min(page, pages)

    muestra = db.scalars(select(PuntoMalla).where(*cond).order_by(
        PuntoMalla.punto_id).offset((page - 1) * per_page).limit(per_page)).all()

    return templates.TemplateResponse(request, "malla.html", {
        "proyecto": proyecto, "total": total, "reservas": reservas,
        "con_peso": con_peso, "muestra": muestra, "q": texto,
        "page": page, "pages": pages, "per_page": per_page,
        "total_filtrado": total_filtrado,
    })


@router.post("/upload", dependencies=[Depends(csrf_protect),
                                       Depends(require_permiso("malla.cargar"))])
async def upload(request: Request, file: UploadFile,
                 db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    _validar_tamano(file.size)
    contenido = await file.read()
    _validar_tamano(len(contenido))
    try:
        filas = persist.parsear_malla(contenido, file.filename or "malla.csv")
        stats = persist.cargar_malla(
            db, proyecto.id, filas, actor=actor_de_request(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _ = stats
    return RedirectResponse("/malla?ok=malla_cargada", status_code=303)
