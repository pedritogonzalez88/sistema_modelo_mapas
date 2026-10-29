from datetime import date, datetime
from math import ceil

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.classes import ClaseCobertura
from app.domain.qaqc import EstadoQAQC, eventos_disponibles
from app.models import FichaQAQC
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/qaqc")

# Estados que dan la ficha por concluida. Se declara aquí (y no se importa el
# privado de persist) para ordenar la lista: primero lo que aún exige trabajo.
ESTADOS_CERRADOS = {"Resuelto por consenso", "Reemplazo completado"}

# Mensaje único para el consenso sin clase: coincide con el que usa
# persist.transicionar_ficha al aprobar, para que el usuario lea lo mismo en
# el formulario y en la transición.
MENSAJE_CLASE_REQUERIDA = "Para cerrar por consenso se exige una clase definitiva 1–9."

# Tamaños de página ofrecidos en la lista. Cualquier otro valor cae a 50 para no
# permitir consultas arbitrariamente grandes desde la URL.
PER_PAGE_OPCIONES = (25, 50, 100)
PER_PAGE_DEFECTO = 50


def _eventos_disponibles(ficha: FichaQAQC) -> list[str]:
    return [e.value for e in eventos_disponibles(EstadoQAQC(ficha.estado))]


def _contexto(db: Session, ficha: FichaQAQC, eventos: list[str], **extra) -> dict:
    """Contexto único de la ficha: lo comparten el GET y el POST con error."""
    contexto = {
        "proyecto": persist.get_default_proyecto(db),
        "ficha": ficha,
        "eventos": eventos,
        "cruce": persist.detalle_cruce(db, ficha.proyecto_id, ficha.punto_id, ficha.anio),
        "clases": list(ClaseCobertura),
        "valores": ficha.valores_iniciales or {},
    }
    contexto.update(extra)
    return contexto


def _valores_enviados(form, evidencias: list[str]) -> dict:
    """Lo que el usuario escribió, para no perderlo cuando falla la validación."""
    return {
        "debate": form.get("debate") or "",
        "dictamen": form.get("dictamen") or "",
        "clase_definitiva": form.get("clase_definitiva") or "",
        "sensores": form.getlist("sensores"),
        "causa_descarte": form.get("causa_descarte") or "",
        "fecha_sesion": form.get("fecha_sesion") or "",
        "firmas": {k: form.get(k) or "" for k in ("firma_a", "firma_b", "firma_c")},
        "evidencias": evidencias,
    }


@router.get("", response_class=HTMLResponse)
def listar(request: Request, estado: str | None = None,
           page: int = Query(1), per_page: int = Query(PER_PAGE_DEFECTO),
           db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)

    if per_page not in PER_PAGE_OPCIONES:
        per_page = PER_PAGE_DEFECTO

    cond = [FichaQAQC.proyecto_id == proyecto.id]
    if estado:
        cond.append(FichaQAQC.estado == estado)

    # El total filtrado manda en el contador "X–Y de N" y en el número de
    # páginas. Los conteos de los chips son globales (sin el filtro de estado)
    # para que el usuario vea siempre la distribución completa del proyecto.
    total_filtrado = db.scalar(
        select(func.count()).select_from(FichaQAQC).where(*cond)
    ) or 0
    pages = max(1, ceil(total_filtrado / per_page))

    # Decisión: una página fuera de rango se acota a la última disponible en
    # lugar de devolver una tabla vacía. Así `?page=999` —o el salto que puede
    # provocar cambiar el tamaño de página— sigue mostrando fichas y el
    # contador "X–Y de N" es coherente con lo que se ve.
    page = min(max(page, 1), pages)

    rows = (
        select(FichaQAQC)
        .where(*cond)
        # Prioridad: primero las fichas que aún exigen trabajo, luego por antigüedad.
        .order_by(case((FichaQAQC.estado.in_(ESTADOS_CERRADOS), 1), else_=0),
                  FichaQAQC.creada_en.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    fichas = db.scalars(rows).all()

    hoy = datetime.now()
    filas = [
        # Se acota a 0: el reloj de la app y el de PostgreSQL pueden diferir en
        # husos horarios y no tiene sentido mostrar "hace -1 días".
        {"ficha": f, "dias": max((hoy - f.creada_en).days, 0) if f.creada_en else None}
        for f in fichas
    ]
    conteos = dict(db.execute(
        select(FichaQAQC.estado, func.count())
        .where(FichaQAQC.proyecto_id == proyecto.id)
        .group_by(FichaQAQC.estado)
    ).all())

    return templates.TemplateResponse(request, "qaqc_list.html", {
        "proyecto": proyecto,
        "filas": filas,
        "conteos": conteos,
        "total_filtrado": total_filtrado,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "per_page_opciones": PER_PAGE_OPCIONES,
        "f_estado": estado or "",
        "estados": [e.value for e in EstadoQAQC],
    })


@router.get("/{ficha_id}", response_class=HTMLResponse)
def detalle(request: Request, ficha_id: int, db: Session = Depends(get_db)):
    ficha = db.get(FichaQAQC, ficha_id)
    if ficha is None:
        raise HTTPException(status_code=404, detail="Ficha inexistente.")
    return templates.TemplateResponse(request, "qaqc_detalle.html",
                                      _contexto(db, ficha, _eventos_disponibles(ficha)))


@router.post("/{ficha_id}")
async def actualizar(request: Request, ficha_id: int, db: Session = Depends(get_db)):
    form = await request.form()
    evidencias = [e.strip() for e in form.getlist("evidencias") if e and e.strip()]
    valores_form = _valores_enviados(form, evidencias)

    try:
        datos: dict = {}
        if "debate" in form:
            datos["debate"] = form["debate"] or None
        if "dictamen" in form and form["dictamen"]:
            datos["dictamen"] = form["dictamen"]
        if "clase_definitiva" in form and form["clase_definitiva"]:
            try:
                datos["clase_definitiva"] = int(form["clase_definitiva"])
            except (TypeError, ValueError):
                raise ValueError("La clase definitiva debe ser un número del 1 al 9.") from None
        # Los sensores se pueden vaciar: el marcador distingue "no enviado" de "ninguno".
        if "sensores_present" in form:
            datos["sensores"] = form.getlist("sensores")
        if "causa_descarte" in form:
            # Se envía tal cual (también vacío) para que elegir "sin causa" la borre.
            datos["causa_descarte"] = form["causa_descarte"]
        if "fecha_sesion" in form and form["fecha_sesion"]:
            try:
                datos["fecha_sesion"] = date.fromisoformat(form["fecha_sesion"])
            except (TypeError, ValueError):
                raise ValueError("La fecha de sesión debe tener el formato AAAA-MM-DD.") from None
        firmas = {k: form[k] for k in ("firma_a", "firma_b", "firma_c")
                  if form.get(k)}
        if firmas:
            datos["firmas"] = firmas
        # Evidencias múltiples: una fila por evidencia; el marcador permite vaciar.
        if "evidencias_present" in form:
            datos["evidencias"] = evidencias

        ficha = db.get(FichaQAQC, ficha_id)
        if ficha is None:
            raise ValueError("Ficha inexistente.")
        # Misma regla que aplica persist.transicionar_ficha al aprobar consenso:
        # un consenso sin clase definitiva no es un dictamen cerrado.
        if datos.get("dictamen") == "consenso" and not (
                datos.get("clase_definitiva") or ficha.clase_definitiva):
            raise ValueError(MENSAJE_CLASE_REQUERIDA)

        persist.actualizar_ficha(db, ficha_id, datos)
    except ValueError as exc:
        # Error de validación: se vuelve a pintar la ficha (400, HTML) sin
        # perder lo escrito y sin exponer el error como JSON.
        db.rollback()
        ficha = db.get(FichaQAQC, ficha_id)
        if ficha is None:
            raise HTTPException(status_code=404, detail="Ficha inexistente.") from None
        return templates.TemplateResponse(
            request, "qaqc_detalle.html",
            _contexto(db, ficha, _eventos_disponibles(ficha),
                      error=str(exc), valores_form=valores_form),
            status_code=400,
        )

    return RedirectResponse(f"/qaqc/{ficha_id}?ok=ficha_guardada", status_code=303)


@router.post("/{ficha_id}/evento")
def evento(ficha_id: int, evento: str = Form(...), db: Session = Depends(get_db)):
    try:
        persist.transicionar_ficha(db, ficha_id, evento)
    except ValueError as exc:
        db.rollback()
        if "clase definitiva" in str(exc).lower():
            return RedirectResponse(f"/qaqc/{ficha_id}?error=clase_requerida",
                                    status_code=303)
        return RedirectResponse(f"/qaqc/{ficha_id}?error=transicion_invalida",
                                status_code=303)
    return RedirectResponse(f"/qaqc/{ficha_id}?ok=ficha_transicion", status_code=303)
