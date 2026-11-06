"""Resumen agregado para la portada."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ClaseMapa,
    Entrega,
    FichaQAQC,
    Interpretacion,
    PuntoMalla,
    ResultadoCruce,
)
from app.services.common import _ESTADOS_CERRADOS
from app.services.malla import estado_mapa

def resumen_dashboard(db: Session, proyecto_id: int) -> dict:
    filas = db.execute(select(ResultadoCruce.resultado, func.count())
                       .where(ResultadoCruce.proyecto_id == proyecto_id)
                       .group_by(ResultadoCruce.resultado)).all()
    conteo = {r: n for r, n in filas}
    completas = (conteo.get("unanimidad", 0) + conteo.get("mayoria", 0)
                 + conteo.get("discrepancia", 0))
    # B-19: una corrección crea otra interpretación para el mismo
    # punto/año/letra. Se cuenta solo la última aceptada de cada combinación.
    ultimas = (
        select(Interpretacion.punto_id, Interpretacion.anio,
               Interpretacion.asistente_letra,
               func.max(Interpretacion.id).label("ultimo"))
        .join(Entrega, Entrega.id == Interpretacion.entrega_id)
        .where(Interpretacion.proyecto_id == proyecto_id,
               Entrega.estado == "aceptada")
        .group_by(Interpretacion.punto_id, Interpretacion.anio,
                  Interpretacion.asistente_letra)
        .subquery()
    )
    total_eval = db.scalar(
        select(func.count()).select_from(ultimas)) or 0
    total_puntos = db.scalar(select(func.count(func.distinct(ResultadoCruce.punto_id))).where(
        ResultadoCruce.proyecto_id == proyecto_id)) or 0
    pendientes = db.scalar(select(func.count()).select_from(FichaQAQC).where(
        FichaQAQC.proyecto_id == proyecto_id,
        FichaQAQC.estado.notin_(_ESTADOS_CERRADOS))) or 0
    entregas = db.scalars(select(Entrega).where(
        Entrega.proyecto_id == proyecto_id).order_by(Entrega.recibida_en.desc()).limit(20)).all()
    mapa = estado_mapa(db, proyecto_id)
    entregas_pendientes = db.scalar(
        select(func.count()).select_from(Entrega).where(
            Entrega.proyecto_id == proyecto_id,
            Entrega.estado == "pendiente")) or 0
    tiene_malla = (db.scalar(
        select(func.count()).select_from(PuntoMalla).where(
            PuntoMalla.proyecto_id == proyecto_id)) or 0) > 0
    ultima_mapa = db.scalar(
        select(func.max(ClaseMapa.cargada_en)).where(
            ClaseMapa.proyecto_id == proyecto_id))
    return {
        "total_puntos": total_puntos, "total_evaluaciones": total_eval,
        "unanimidad": conteo.get("unanimidad", 0), "mayoria": conteo.get("mayoria", 0),
        "discrepancia": conteo.get("discrepancia", 0),
        "incompleto": conteo.get("incompleto", 0),
        "pct_unanimidad": round(conteo.get("unanimidad", 0) / completas * 100, 1) if completas else 0.0,
        "pct_mayoria": round(conteo.get("mayoria", 0) / completas * 100, 1) if completas else 0.0,
        "pct_discrepancia": round(conteo.get("discrepancia", 0) / completas * 100, 1) if completas else 0.0,
        "pendientes_qaqc": pendientes,
        "entregas_pendientes": entregas_pendientes,
        "tiene_malla": tiene_malla,
        "ultima_mapa": ultima_mapa,
        "mapa_registros": mapa["registros"], "mapa_puntos": mapa["puntos"],
        "entregas": [{"id": e.id, "letra": e.asistente_letra, "semana": e.semana,
                      "tipo": e.tipo, "version": e.version, "estado": e.estado} for e in entregas],
    }
