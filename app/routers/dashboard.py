from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import persist
from app.web import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    datos = persist.resumen_dashboard(db, proyecto.id)
    return templates.TemplateResponse(request, "dashboard.html",
                                       {"proyecto": proyecto, **datos})
