from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/mapa")


@router.get("", response_class=HTMLResponse)
def pagina(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    info = persist.estado_mapa(db, proyecto.id)
    return templates.TemplateResponse(request, "mapa.html",
                                       {"proyecto": proyecto, **info})


@router.post("/upload")
async def upload(file: UploadFile, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    contenido = await file.read()
    try:
        stats = persist.cargar_mapa(db, proyecto.id, contenido,
                                     file.filename or "mapa.xlsx")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _ = stats
    return RedirectResponse("/mapa", status_code=303)
