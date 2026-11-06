from io import BytesIO

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permiso
from app.core.database import get_db
from app.core.excel import celda_segura
from app.models import FichaQAQC, PuntoMalla, ResultadoCruce
from app.services import persist
from app.web import templates

# El consolidado se publica solo con ``exportar`` (supervisión y consulta).
router = APIRouter(prefix="/exportar",
                   dependencies=[Depends(require_permiso("exportar"))])
_ANIOS = list(range(1990, 2025))


@router.get("", response_class=HTMLResponse)
def pagina(request: Request, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    n = db.scalar(select(func.count()).select_from(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == proyecto.id)) or 0
    return templates.TemplateResponse(request, "exportar.html",
                                       {"proyecto": proyecto, "n_cruces": n,
                                        "anios": _ANIOS})


@router.get("/consolidado.xlsx")
def consolidado(db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    cruces = db.scalars(select(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == proyecto.id
    ).order_by(ResultadoCruce.punto_id, ResultadoCruce.anio)).all()
    malla = {m.punto_id: m for m in db.scalars(select(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto.id)).all()}

    por_punto: dict[int, dict] = {}
    for c in cruces:
        fila = por_punto.setdefault(c.punto_id, {})
        fila[c.anio] = c.clase_consolidada

    libro = Workbook()
    ws = libro.active
    ws.title = "Consolidado"
    ws.append(["ID_PUNTO", "ORIGEN", "ESTRATO", "X", "Y"] + [f"A{a}" for a in _ANIOS])
    for punto in sorted(por_punto):
        m = malla.get(punto)
        x = y = origen = estrato = None
        if m is not None:
            origen, estrato = m.origen_anio, m.estrato
        if m is not None and m.geom is not None:
            x, y = db.scalar(func.ST_X(m.geom)), db.scalar(func.ST_Y(m.geom))
        ws.append([celda_segura(punto), celda_segura(origen),
                   celda_segura(estrato), celda_segura(x), celda_segura(y)]
                  + [celda_segura(por_punto[punto].get(a)) for a in _ANIOS])

    ws2 = libro.create_sheet("Ternas")
    ws2.append(["Punto", "Anio", "A", "B", "C", "Resultado", "ClaseFinal", "Ficha"])
    fichas = {(f.punto_id, f.anio): f for f in db.scalars(select(FichaQAQC).where(
        FichaQAQC.proyecto_id == proyecto.id)).all()}
    for c in cruces:
        f = fichas.get((c.punto_id, c.anio))
        ws2.append([celda_segura(c.punto_id), celda_segura(c.anio),
                    celda_segura(c.a), celda_segura(c.b), celda_segura(c.c),
                    celda_segura(c.resultado), celda_segura(c.clase_consolidada),
                    celda_segura(f"{f.id}/{f.estado}" if f else "")])

    buf = BytesIO()
    libro.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=consolidado_eet.xlsx"})
