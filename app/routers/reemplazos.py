"""Reemplazos: propuesta, autorización y cierre (4.5–4.9).

Autorización (Fase A2): leer exige ``reemplazos.ver``; reportar/proponer,
``reemplazos.proponer`` (supervisión y carga); autorizar o completar,
``reemplazos.autorizar`` (solo supervisión). El árbol de transiciones sigue
siendo el del servicio: aquí solo se decide **quién** puede intentarlo.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.audit import actor_de_request
from app.auth.csrf import csrf_protect
from app.auth.dependencies import exigir_permiso, require_permiso
from app.core.database import get_db
from app.core.security import utcnow
from app.services import persist
from app.services.importer import CAUSAS_RESERVA
from app.web import templates

router = APIRouter(prefix="/reemplazos",
                   dependencies=[Depends(require_permiso("reemplazos.ver"))])

# Estados que solo puede fijar la supervisión.
ESTADOS_AUTORIZACION = ("autorizado", "completado")


@router.get("", response_class=HTMLResponse)
def listar(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    filas = persist.listar_reemplazos(db, proyecto.id)
    return templates.TemplateResponse(request, "reemplazos.html", {
        "proyecto": proyecto, "filas": filas, "causas": CAUSAS_RESERVA,
        "estados": list(persist.ESTADOS_REEMPLAZO),
        "now": utcnow(),
    })


@router.post("", dependencies=[Depends(csrf_protect),
                                Depends(require_permiso("reemplazos.proponer"))])
def crear(request: Request, punto_origen: int = Form(...),
          causa: str = Form(""), db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    try:
        persist.crear_reemplazo_propuesto(
            db, proyecto.id, punto_origen, causa or None,
            actor=actor_de_request(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/reemplazos?ok=reemplazo_creado", status_code=303)


@router.post("/{rid}/proponer", dependencies=[Depends(csrf_protect),
                                               Depends(require_permiso("reemplazos.proponer"))])
def proponer(request: Request, rid: int, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    rep = next((r for r in persist.listar_reemplazos(db, proyecto.id) if r.id == rid), None)
    if rep is None:
        raise HTTPException(status_code=404, detail="Reemplazo inexistente.")
    try:
        persist.proponer_reserva(
            db, proyecto.id, rep.punto_origen,
            actor=actor_de_request(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/reemplazos?ok=reserva_propuesta", status_code=303)


@router.post("/{rid}/estado", dependencies=[Depends(csrf_protect)])
async def estado(request: Request, rid: int, nuevo_estado: str = Form(...),
                 db: Session = Depends(get_db)):
    # El permiso depende del estado pedido: autorizar/completar es exclusivo de
    # supervisión; el resto de intentos exige al menos poder proponer.
    if nuevo_estado in ESTADOS_AUTORIZACION:
        exigir_permiso(request, "reemplazos.autorizar")
    else:
        exigir_permiso(request, "reemplazos.proponer")
    try:
        persist.transicionar_reemplazo(
            db, rid, nuevo_estado, actor=actor_de_request(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/reemplazos?ok=reemplazo_estado", status_code=303)
