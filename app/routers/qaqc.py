from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.qaqc import eventos_disponibles, EstadoQAQC
from app.models import FichaQAQC
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/qaqc")


@router.get("", response_class=HTMLResponse)
def listar(request: Request, estado: str | None = None, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    q = select(FichaQAQC).where(FichaQAQC.proyecto_id == proyecto.id)
    if estado:
        q = q.where(FichaQAQC.estado == estado)
    fichas = db.scalars(q.order_by(FichaQAQC.punto_id, FichaQAQC.anio)).all()
    return templates.TemplateResponse(request, "qaqc_list.html", {
        "proyecto": proyecto, "fichas": fichas, "f_estado": estado or "",
        "estados": [e.value for e in EstadoQAQC],
    })


@router.get("/{ficha_id}", response_class=HTMLResponse)
def detalle(request: Request, ficha_id: int, db: Session = Depends(get_db)):
    ficha = db.get(FichaQAQC, ficha_id)
    if ficha is None:
        raise HTTPException(status_code=404, detail="Ficha inexistente.")
    eventos = [e.value for e in eventos_disponibles(EstadoQAQC(ficha.estado))]
    return templates.TemplateResponse(request, "qaqc_detalle.html",
                                       {"ficha": ficha, "eventos": eventos})


@router.post("/{ficha_id}")
async def actualizar(request: Request, ficha_id: int, db: Session = Depends(get_db)):
    from datetime import date

    form = await request.form()
    datos: dict = {}
    if "debate" in form:
        datos["debate"] = form["debate"] or None
    if "dictamen" in form and form["dictamen"]:
        datos["dictamen"] = form["dictamen"]
    if "clase_definitiva" in form and form["clase_definitiva"]:
        datos["clase_definitiva"] = int(form["clase_definitiva"])
    sensores = form.getlist("sensores")
    if sensores:
        datos["sensores"] = sensores
    if "causa_descarte" in form:
        datos["causa_descarte"] = form["causa_descarte"] or None
    if "fecha_sesion" in form and form["fecha_sesion"]:
        datos["fecha_sesion"] = date.fromisoformat(form["fecha_sesion"])
    firmas = {k: form[k] for k in ("firma_a", "firma_b", "firma_c")
              if form.get(k)}
    if firmas:
        datos["firmas"] = firmas
    evidencias_raw = (form.get("evidencias") or "").strip()
    if evidencias_raw:
        datos["evidencias"] = [l.strip() for l in evidencias_raw.splitlines() if l.strip()]
    try:
        persist.actualizar_ficha(db, ficha_id, datos)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(f"/qaqc/{ficha_id}", status_code=303)


@router.post("/{ficha_id}/evento")
def evento(ficha_id: int, evento: str = Form(...), db: Session = Depends(get_db)):
    try:
        persist.transicionar_ficha(db, ficha_id, evento)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(f"/qaqc/{ficha_id}", status_code=303)
