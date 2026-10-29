from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Entrega, ResultadoCruce
from app.services import persist
from app.services.importer import importar_excel
from app.web import templates

router = APIRouter(prefix="/entregas")


@router.get("", response_class=HTMLResponse)
def listar(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    entregas = db.scalars(select(Entrega).where(
        Entrega.proyecto_id == proyecto.id).order_by(Entrega.recibida_en.desc())).all()
    return templates.TemplateResponse(request, "entregas.html",
                                       {"proyecto": proyecto, "entregas": entregas})


@router.post("/upload")
async def upload(request: Request, file: UploadFile,
                 asistente_letra: str = Form(...), semana: str = Form(...),
                 tipo: str = Form("avance"), db: Session = Depends(get_db)):
    contenido = await file.read()
    try:
        entrega, _ = persist.crear_entrega_pendiente(
            db, asistente_letra, semana, tipo, contenido)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(f"/entregas/{entrega.id}/preview", status_code=303)


@router.get("/{entrega_id}/preview", response_class=HTMLResponse)
def preview(request: Request, entrega_id: int, db: Session = Depends(get_db)):
    entrega = db.get(Entrega, entrega_id)
    if entrega is None:
        raise HTTPException(status_code=404, detail="Entrega inexistente.")
    contenido = Path(entrega.archivo_path).read_bytes()
    parsed = importar_excel(contenido)
    existentes = set(db.execute(select(ResultadoCruce.punto_id, ResultadoCruce.anio).where(
        ResultadoCruce.proyecto_id == entrega.proyecto_id)).all())
    nuevos = sum(1 for clave in parsed.datos if clave not in existentes)
    return templates.TemplateResponse(request, "entrega_preview.html", {
        "entrega": entrega, "detalle": entrega.detalle_validacion or {},
        "errores": (entrega.detalle_validacion or {}).get("primeros_errores", []),
        "n_nuevos": nuevos, "n_total": len(parsed.datos),
    })


@router.post("/{entrega_id}/confirm")
def confirm(entrega_id: int, db: Session = Depends(get_db)):
    try:
        stats = persist.confirmar_entrega(db, entrega_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _ = stats
    return RedirectResponse("/cruces", status_code=303)


@router.post("/{entrega_id}/reject")
def reject(entrega_id: int, motivo: str = Form(""), db: Session = Depends(get_db)):
    try:
        persist.rechazar_entrega(db, entrega_id, motivo or "Sin motivo registrado.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/entregas", status_code=303)
