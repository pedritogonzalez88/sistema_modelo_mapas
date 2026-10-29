from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import persist
from app.services.importer import CAUSAS_RESERVA
from app.web import templates

router = APIRouter(prefix="/reemplazos")


@router.get("", response_class=HTMLResponse)
def listar(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    filas = persist.listar_reemplazos(db, proyecto.id)
    return templates.TemplateResponse(request, "reemplazos.html", {
        "proyecto": proyecto, "filas": filas, "causas": CAUSAS_RESERVA,
        "estados": list(persist.ESTADOS_REEMPLAZO),
    })


@router.post("")
def crear(punto_origen: int = Form(...), causa: str = Form(""),
          db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    try:
        persist.crear_reemplazo_propuesto(db, proyecto.id, punto_origen,
                                          causa or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/reemplazos", status_code=303)


@router.post("/{rid}/proponer")
def proponer(rid: int, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    rep = next((r for r in persist.listar_reemplazos(db, proyecto.id) if r.id == rid), None)
    if rep is None:
        raise HTTPException(status_code=404, detail="Reemplazo inexistente.")
    try:
        persist.proponer_reserva(db, proyecto.id, rep.punto_origen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/reemplazos", status_code=303)


@router.post("/{rid}/estado")
def estado(rid: int, nuevo_estado: str = Form(...), db: Session = Depends(get_db)):
    try:
        persist.transicionar_reemplazo(db, rid, nuevo_estado)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/reemplazos", status_code=303)
