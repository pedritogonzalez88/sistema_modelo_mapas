from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.csrf import csrf_protect
from app.core.database import get_db
from app.models import Entrega, ResultadoCruce
from app.services import persist
from app.services.importer import importar_excel
from app.web import templates

router = APIRouter(prefix="/entregas")


@router.get("", response_class=HTMLResponse)
def listar(request: Request, asistente: str | None = None,
           estado: str | None = None, semana: str | None = None,
           db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)

    q = select(Entrega).where(Entrega.proyecto_id == proyecto.id)
    if asistente:
        q = q.where(Entrega.asistente_letra == asistente)
    if estado:
        q = q.where(Entrega.estado == estado)
    if semana:
        q = q.where(Entrega.semana == semana)
    entregas = db.scalars(q.order_by(Entrega.recibida_en.desc())).all()

    # Conteo por estado del proyecto completo (una sola consulta) para las
    # opciones del filtro: el contador no debe depender del filtro activo.
    conteos = dict(db.execute(
        select(Entrega.estado, func.count())
        .where(Entrega.proyecto_id == proyecto.id)
        .group_by(Entrega.estado)).all())

    semanas = db.scalars(
        select(Entrega.semana)
        .where(Entrega.proyecto_id == proyecto.id)
        .distinct()
        .order_by(Entrega.semana.desc())).all()

    return templates.TemplateResponse(request, "entregas.html", {
        "proyecto": proyecto, "entregas": entregas,
        "conteos": conteos, "semanas": semanas,
        "total_entregas": sum(conteos.values()),
        "f_asistente": asistente or "", "f_estado": estado or "",
        "f_semana": semana or "",
    })


@router.post("/upload", dependencies=[Depends(csrf_protect)])
async def upload(request: Request, file: UploadFile,
                 asistente_letra: str = Form(...), semana: str = Form(...),
                 tipo: str = Form("avance"), db: Session = Depends(get_db)):
    contenido = await file.read()
    try:
        entrega, _ = persist.crear_entrega_pendiente(
            db, asistente_letra, semana, tipo, contenido)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Sin flash: la propia vista previa es el feedback de la carga.
    return RedirectResponse(f"/entregas/{entrega.id}/preview", status_code=303)


@router.get("/{entrega_id}/preview", response_class=HTMLResponse)
def preview(request: Request, entrega_id: int, db: Session = Depends(get_db)):
    entrega = db.get(Entrega, entrega_id)
    if entrega is None:
        raise HTTPException(status_code=404, detail="Entrega inexistente.")
    ruta = Path(entrega.archivo_path)
    contenido = ruta.read_bytes()
    parsed = importar_excel(contenido)
    existentes = set(db.execute(select(ResultadoCruce.punto_id, ResultadoCruce.anio).where(
        ResultadoCruce.proyecto_id == entrega.proyecto_id)).all())
    nuevos = sum(1 for clave in parsed.datos if clave not in existentes)
    try:
        tamano = ruta.stat().st_size
    except OSError:
        tamano = None
    return templates.TemplateResponse(request, "entrega_preview.html", {
        "proyecto": persist.get_default_proyecto(db),
        "entrega": entrega, "detalle": entrega.detalle_validacion or {},
        "errores": (entrega.detalle_validacion or {}).get("primeros_errores", []),
        "n_nuevos": nuevos, "n_total": len(parsed.datos),
        "tamano": tamano, "sha_corto": entrega.sha256[:16],
        "nombre_archivo": ruta.name,
    })


@router.post("/{entrega_id}/confirm", dependencies=[Depends(csrf_protect)])
def confirm(entrega_id: int, db: Session = Depends(get_db)):
    try:
        stats = persist.confirmar_entrega(db, entrega_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _ = stats
    return RedirectResponse("/cruces?ok=entrega_confirmada", status_code=303)


@router.post("/{entrega_id}/reject", dependencies=[Depends(csrf_protect)])
def reject(entrega_id: int, motivo: str = Form(""), db: Session = Depends(get_db)):
    try:
        persist.rechazar_entrega(db, entrega_id, motivo or "Sin motivo registrado.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/entregas?ok=entrega_rechazada", status_code=303)
