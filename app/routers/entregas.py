"""Entregas: carga y revisión (4.1–4.4).

Autorización (Fase A2):

- Lectura: ``entregas.ver`` **o** ``entregas.propias``. El ``asistente`` entra
  solo con el segundo y el router acota el listado a su ``letra_terna``; pedir
  la vista previa de otra letra responde 403 aunque la URL sea manual.
- ``upload``/``confirm``/``reject``: ``entregas.gestionar`` (supervisión y
  carga). Con ``EET_AUTH_ENABLED=false`` las dependencias dejan pasar todo.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.csrf import csrf_protect
from app.auth.dependencies import negar_acceso, require_permiso
from app.core.database import get_db
from app.models import Entrega, ResultadoCruce
from app.services import persist
from app.services.importer import importar_excel
from app.web import templates

router = APIRouter(prefix="/entregas",
                   dependencies=[Depends(require_permiso("entregas.ver",
                                                         "entregas.propias"))])


def _alcance(request: Request) -> tuple[bool, str | None]:
    """``(es_asistente, letra_terna)``.

    Se devuelven las dos piezas porque un asistente **sin** letra asignada no
    debe ver nada, y eso es distinto de "no es asistente, ve todo".
    """
    usuario = getattr(request.state, "usuario", None)
    if usuario is None or getattr(usuario, "rol", "") != "asistente":
        return False, None
    return True, getattr(usuario, "letra_terna", None)


@router.get("", response_class=HTMLResponse)
def listar(request: Request, asistente: str | None = None,
           estado: str | None = None, semana: str | None = None,
           db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)

    # Alcance del asistente: su letra manda sobre cualquier filtro de la URL.
    es_asistente, letra_propia = _alcance(request)
    if es_asistente:
        asistente = letra_propia

    # Condición base compartida por listado, contadores y semanas: el asistente
    # no debe deducir actividad de otras letras a través de los filtros.
    base = [Entrega.proyecto_id == proyecto.id]
    if es_asistente:
        base.append(Entrega.asistente_letra == letra_propia)

    q = select(Entrega).where(*base)
    if asistente:
        q = q.where(Entrega.asistente_letra == asistente)
    if estado:
        q = q.where(Entrega.estado == estado)
    if semana:
        q = q.where(Entrega.semana == semana)
    entregas = db.scalars(q.order_by(Entrega.recibida_en.desc())).all()

    # Conteo por estado del alcance visible (una sola consulta) para las
    # opciones del filtro: el contador no debe depender del filtro activo.
    conteos = dict(db.execute(
        select(Entrega.estado, func.count())
        .where(*base)
        .group_by(Entrega.estado)).all())

    semanas = db.scalars(
        select(Entrega.semana)
        .where(*base)
        .distinct()
        .order_by(Entrega.semana.desc())).all()

    return templates.TemplateResponse(request, "entregas.html", {
        "proyecto": proyecto, "entregas": entregas,
        "conteos": conteos, "semanas": semanas,
        "total_entregas": sum(conteos.values()),
        "f_asistente": asistente or "", "f_estado": estado or "",
        "f_semana": semana or "",
        "letra_propia": letra_propia,
    })


@router.post("/upload", dependencies=[Depends(csrf_protect),
                                      Depends(require_permiso("entregas.gestionar"))])
async def upload(request: Request, file: UploadFile,
                 asistente_letra: str = Form(...), semana: str = Form(...),
                 tipo: str = Form("avance"), db: Session = Depends(get_db)):
    contenido = await file.read()
    try:
        entrega, _ = persist.crear_entrega_pendiente(
            db, asistente_letra, semana, tipo, contenido)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Sin flash: la propia vista previa es el feedback de la carga.
    return RedirectResponse(f"/entregas/{entrega.id}/preview", status_code=303)


@router.get("/{entrega_id}/preview", response_class=HTMLResponse)
def preview(request: Request, entrega_id: int, db: Session = Depends(get_db)):
    entrega = db.get(Entrega, entrega_id)
    if entrega is None:
        raise HTTPException(status_code=404, detail="Entrega inexistente.")

    # Se comprueba antes de leer el archivo: el asistente no debe ni abrir el
    # .xlsx de otra evaluadora.
    es_asistente, letra_propia = _alcance(request)
    if es_asistente and (not letra_propia
                         or entrega.asistente_letra != letra_propia):
        negar_acceso(request, getattr(request.state, "usuario", None),
                     "entregas.propias",
                     {"entrega": entrega_id, "alcance": "letra_ajena"})

    ruta = Path(entrega.archivo_path)
    contenido = ruta.read_bytes()
    parsed = importar_excel(contenido)
    existentes = set(db.execute(select(ResultadoCruce.punto_id, ResultadoCruce.anio).where(
        ResultadoCruce.proyecto_id == entrega.proyecto_id)).all())
    nuevos = sum(1 for clave in parsed.datos if clave not in existentes)
    try:
        tamano = ruta.stat().st_size
    except OSError:
        tamano = None
    return templates.TemplateResponse(request, "entrega_preview.html", {
        "proyecto": persist.get_default_proyecto(db),
        "entrega": entrega, "detalle": entrega.detalle_validacion or {},
        "errores": (entrega.detalle_validacion or {}).get("primeros_errores", []),
        "n_nuevos": nuevos, "n_total": len(parsed.datos),
        "tamano": tamano, "sha_corto": entrega.sha256[:16],
        "nombre_archivo": ruta.name,
    })


@router.post("/{entrega_id}/confirm",
             dependencies=[Depends(csrf_protect),
                           Depends(require_permiso("entregas.gestionar"))])
def confirm(entrega_id: int, db: Session = Depends(get_db)):
    try:
        stats = persist.confirmar_entrega(db, entrega_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _ = stats
    return RedirectResponse("/cruces?ok=entrega_confirmada", status_code=303)


@router.post("/{entrega_id}/reject",
             dependencies=[Depends(csrf_protect),
                           Depends(require_permiso("entregas.gestionar"))])
def reject(entrega_id: int, motivo: str = Form(""), db: Session = Depends(get_db)):
    try:
        persist.rechazar_entrega(db, entrega_id, motivo or "Sin motivo registrado.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/entregas?ok=entrega_rechazada", status_code=303)
