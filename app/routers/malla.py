import mimetypes
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import PuntoMalla
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/malla")

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


@router.post("/upload")
async def upload(file: UploadFile, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    contenido = await file.read()
    try:
        filas = persist.parsear_malla(contenido, file.filename or "malla.csv")
        stats = persist.cargar_malla(db, proyecto.id, filas)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _ = stats
    return RedirectResponse("/malla?ok=malla_cargada", status_code=303)
