from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.csrf import csrf_protect
from app.core.database import get_db
from app.models import ClaseMapa, ResultadoCruce
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/mapa")


@router.get("", response_class=HTMLResponse)
def pagina(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    info = persist.estado_mapa(db, proyecto.id)
    ultima_mapa = db.scalar(select(func.max(ClaseMapa.cargada_en)).where(
        ClaseMapa.proyecto_id == proyecto.id))
    cruce_count = db.scalar(select(func.count()).select_from(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == proyecto.id)) or 0
    contexto = {"proyecto": proyecto, **info}
    contexto.update({
        "ultima_mapa": ultima_mapa,
        "cruce_count": cruce_count,
        "tiene_cruces": cruce_count > 0,
    })
    return templates.TemplateResponse(request, "mapa.html", contexto)


@router.post("/upload", dependencies=[Depends(csrf_protect)])
async def upload(file: UploadFile, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    contenido = await file.read()
    try:
        stats = persist.cargar_mapa(db, proyecto.id, contenido,
                                     file.filename or "mapa.xlsx")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _ = stats
    return RedirectResponse("/mapa?ok=mapa_cargado", status_code=303)
