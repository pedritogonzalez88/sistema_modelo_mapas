from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/cruces")


@router.get("", response_class=HTMLResponse)
def listar(request: Request, resultado: str | None = None,
           punto: int | None = None, anio: int | None = None,
           db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    filas = persist.listar_cruces(db, proyecto.id, resultado, punto, anio)
    return templates.TemplateResponse(request, "cruces.html", {
        "proyecto": proyecto, "filas": filas,
        "f_resultado": resultado or "", "f_punto": punto or "", "f_anio": anio or "",
    })


@router.get("/{punto}/{anio}", response_class=HTMLResponse)
def detalle(request: Request, punto: int, anio: int, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    datos = persist.detalle_cruce(db, proyecto.id, punto, anio)
    if datos is None:
        raise HTTPException(status_code=404, detail="Cruce inexistente.")
    return templates.TemplateResponse(request, "cruce_detalle.html",
                                       {"proyecto": proyecto, "punto": punto,
                                        "anio": anio, **datos})
