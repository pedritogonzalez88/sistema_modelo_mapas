"""Portada: resumen consolidado o panel propio según el rol (Fase A2).

El dashboard es de lectura para todos, pero el ``asistente`` no puede ver los
resultados consolidados (concordancia de la terna, cruces, QA/QC ni el estado
del flujo): es el rol que produce las interpretaciones y su independencia se
garantiza no mostrándole el cruce. El servidor le entrega solo sus entregas y
la plantilla oculta el resto.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permiso
from app.core.database import get_db
from app.models import Entrega
from app.services import persist
from app.web import templates

router = APIRouter(dependencies=[Depends(require_permiso("dashboard.ver"))])

# Mismo formato que ``persist.resumen_dashboard`` para que la plantilla no
# distinga el origen del dato.
_LIMITE_PROPIAS = 20


def _entregas_propias(db: Session, proyecto_id: int, letra: str | None) -> list[dict]:
    """Entregas de la letra del asistente (vacío si no tiene letra asignada)."""
    if not letra:
        return []
    filas = db.scalars(
        select(Entrega)
        .where(Entrega.proyecto_id == proyecto_id,
               Entrega.asistente_letra == letra)
        .order_by(Entrega.recibida_en.desc())
        .limit(_LIMITE_PROPIAS)
    ).all()
    return [{"id": e.id, "letra": e.asistente_letra, "semana": e.semana,
             "tipo": e.tipo, "version": e.version, "estado": e.estado}
            for e in filas]


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    datos = persist.resumen_dashboard(db, proyecto.id)

    usuario = getattr(request.state, "usuario", None)
    # Alcance de datos del asistente: se filtra aquí, sin tocar el servicio
    # ``persist.resumen_dashboard`` (que sigue siendo el del proyecto completo).
    if usuario is not None and getattr(usuario, "rol", "") == "asistente":
        datos["entregas"] = _entregas_propias(
            db, proyecto.id, getattr(usuario, "letra_terna", None))

    return templates.TemplateResponse(request, "dashboard.html",
                                      {"proyecto": proyecto, **datos})
