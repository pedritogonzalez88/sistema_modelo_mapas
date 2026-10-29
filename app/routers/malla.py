from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import PuntoMalla
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/malla")


@router.get("", response_class=HTMLResponse)
def pagina(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    total = db.scalar(select(func.count()).select_from(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id)) or 0
    reservas = db.scalar(select(func.count()).select_from(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id, PuntoMalla.es_reserva.is_(True))) or 0
    con_peso = db.scalar(select(func.count()).select_from(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id, PuntoMalla.peso.is_not(None))) or 0
    muestra = db.scalars(select(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id
    ).order_by(PuntoMalla.punto_id).limit(100)).all()
    return templates.TemplateResponse(request, "malla.html", {
        "proyecto": proyecto, "total": total, "reservas": reservas,
        "con_peso": con_peso, "muestra": muestra,
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
    return RedirectResponse("/malla", status_code=303)
