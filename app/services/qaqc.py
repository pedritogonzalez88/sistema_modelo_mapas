"""Actualización y transición de fichas QA/QC."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.domain.qaqc import EstadoQAQC, EventoQAQC, transicion
from app.models import Auditoria, FichaQAQC, ResultadoCruce
from app.services.common import _ESTADOS_CERRADOS, _para_json

def actualizar_ficha(db: Session, ficha_id: int, datos: dict, actor: str = "supervision"):
    ficha = db.get(FichaQAQC, ficha_id)
    if ficha is None:
        raise ValueError("Ficha inexistente.")
    if ficha.estado in _ESTADOS_CERRADOS:
        raise ValueError("La ficha está cerrada.")
    if datos.get("evidencias") is not None:
        ficha.evidencias = datos["evidencias"]
    if datos.get("sensores") is not None:
        ficha.sensores = datos["sensores"]
    if datos.get("debate") is not None:
        ficha.debate = datos["debate"]
    if datos.get("dictamen") is not None:
        dictamen = str(datos["dictamen"]).strip().lower()
        if dictamen not in ("consenso", "sin_consenso"):
            raise ValueError("El dictamen debe ser 'consenso' o 'sin_consenso'.")
        ficha.dictamen = dictamen
    if datos.get("clase_definitiva") is not None:
        clase = datos["clase_definitiva"]
        if not isinstance(clase, int) or not 1 <= clase <= 9:
            raise ValueError("La clase definitiva debe ser un número del 1 al 9.")
        ficha.clase_definitiva = clase
    if datos.get("reemplazo") is not None:
        ficha.reemplazo = datos["reemplazo"]
    if datos.get("causa_descarte") is not None:
        ficha.causa_descarte = datos["causa_descarte"] or None
    if datos.get("fecha_sesion") is not None:
        ficha.fecha_sesion = datos["fecha_sesion"]
    if datos.get("firmas") is not None:
        ficha.firmas = datos["firmas"]
    db.add(Auditoria(actor=actor, accion="ficha_actualizada", entidad="ficha",
                     entidad_id=str(ficha.id),
                     detalle=_para_json({k: v for k, v in datos.items()})))
    db.commit()
    db.refresh(ficha)
    return ficha


def transicionar_ficha(db: Session, ficha_id: int, evento_nombre: str, actor: str = "supervision"):
    ficha = db.get(FichaQAQC, ficha_id)
    if ficha is None:
        raise ValueError("Ficha inexistente.")
    try:
        evento = EventoQAQC(evento_nombre)
    except ValueError:
        raise ValueError(f"Evento '{evento_nombre}' no válido.") from None
    estado_actual = EstadoQAQC(ficha.estado)
    nuevo = transicion(estado_actual, evento)
    if nuevo is None:
        raise ValueError(f"No se permite '{evento.value}' desde '{estado_actual.value}'.")
    if evento is EventoQAQC.APROBAR_CONSENSO:
        clase = ficha.clase_definitiva
        if not isinstance(clase, int) or not 1 <= clase <= 9:
            raise ValueError("Para aprobar por consenso se exige clase definitiva 1-9.")
    ficha.estado = nuevo.value
    if nuevo.value in _ESTADOS_CERRADOS:
        ficha.cerrada_en = utcnow()
    # El dictamen actualiza el consenso (trazable: no toca interpretaciones)
    cruce = db.scalar(select(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == ficha.proyecto_id,
        ResultadoCruce.punto_id == ficha.punto_id, ResultadoCruce.anio == ficha.anio))
    if cruce is not None and evento is EventoQAQC.APROBAR_CONSENSO:
        cruce.clase_consolidada = ficha.clase_definitiva
        cruce.regla_version = "qaqc-consenso"
    if cruce is not None and evento is EventoQAQC.DECLARAR_SIN_CONSENSO:
        cruce.clase_consolidada = None
        cruce.regla_version = "qaqc-sin-consenso"
    db.add(Auditoria(actor=actor, accion="ficha_transicion", entidad="ficha",
                     entidad_id=str(ficha.id),
                     detalle={"de": estado_actual.value, "a": nuevo.value,
                              "evento": evento.value}))
    db.commit()
    db.refresh(ficha)
    return ficha
