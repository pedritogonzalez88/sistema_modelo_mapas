"""Cálculo de cruces, historial, consultas y análisis de la terna."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.classes import SIN_CONSENSO, ClaseCobertura
from app.domain.cross_engine import TipoResultado, resolver_tabla7
from app.domain.qaqc import EstadoQAQC, requiere_ficha_qaqc
from app.models import (
    ClaseMapa,
    Entrega,
    FichaQAQC,
    Interpretacion,
    ResultadoCruce,
    ResultadoCruceHistorial,
)
from app.services.analysis import analisis_seccion5
from app.services.common import (
    _CLASES,
    _ESTADOS_CERRADOS,
    ERROR_MAPA_NO_CARGADO,
    StoreError,
    clase_a_db,
    clase_a_dominio,
)


def _ultima_interpretacion(db: Session, proyecto_id: int, punto: int, anio: int, letra: str):
    return db.scalar(
        select(Interpretacion)
        .join(Entrega, Entrega.id == Interpretacion.entrega_id)
        .where(Interpretacion.proyecto_id == proyecto_id,
               Interpretacion.punto_id == punto, Interpretacion.anio == anio,
               Interpretacion.asistente_letra == letra,
               Entrega.estado == "aceptada")
        # La versión se numera por letra + semana, así que no es comparable
        # entre semanas: la semana 05 v2 no debe ganarle a la 06 v1. Manda la
        # entrega aceptada más reciente y, a igual fecha, el id mayor.
        .order_by(Entrega.recibida_en.desc(), Entrega.id.desc()))


def recalcular_cruce(db: Session, proyecto_id: int, punto: int, anio: int,
                     actor: str = "sistema",
                     entrega_id: int | None = None) -> ResultadoCruce:
    interps = {letra: _ultima_interpretacion(db, proyecto_id, punto, anio, letra)
               for letra in ("A", "B", "C")}
    valores = {letra: (clase_a_dominio(i.clase, i.es_sc) if i else None)
               for letra, i in interps.items()}
    res = resolver_tabla7(valores["A"], valores["B"], valores["C"])
    consol, _ = clase_a_db(res.consenso) if isinstance(res.consenso, ClaseCobertura) else (None, False)

    if res.resultado is TipoResultado.VALIDACION_AUTOMATICA:
        nombre_res = "unanimidad"
    elif res.resultado is TipoResultado.REQUIERE_REVISION:
        nombre_res = "mayoria"
    elif res.resultado is TipoResultado.CONSENSO_TECNICO:
        nombre_res = "discrepancia"
    else:
        nombre_res = "incompleto" if SIN_CONSENSO not in valores.values() else "discrepancia"

    ficha = db.scalar(select(FichaQAQC).where(
        FichaQAQC.proyecto_id == proyecto_id,
        FichaQAQC.punto_id == punto, FichaQAQC.anio == anio))
    ficha_cerrada = ficha is not None and ficha.estado in _ESTADOS_CERRADOS

    cruce = db.scalar(select(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == proyecto_id,
        ResultadoCruce.punto_id == punto, ResultadoCruce.anio == anio))
    if cruce is None:
        cruce = ResultadoCruce(proyecto_id=proyecto_id, punto_id=punto, anio=anio)
        db.add(cruce)
    a_db, _ = clase_a_db(valores["A"])
    b_db, _ = clase_a_db(valores["B"])
    c_db, _ = clase_a_db(valores["C"])
    cruce.a, cruce.b, cruce.c = a_db, b_db, c_db
    cruce.resultado = nombre_res
    if not ficha_cerrada:
        # Solo el cálculo automático escribe el consolidado. Una ficha cerrada
        # contiene una decisión humana (consenso o reemplazo completado) que no
        # debe desaparecer porque después llegue otra entrega semanal.
        cruce.clase_consolidada = consol
        cruce.regla_version = settings.regla_version
    elif (cruce.clase_consolidada is None and ficha is not None
          and ficha.clase_definitiva is not None):
        # Caso defensivo: ficha cerrada sin consolidado previo (datos migrados).
        cruce.clase_consolidada = ficha.clase_definitiva
        cruce.regla_version = "qaqc-consenso"

    # Historial append-only: el estado vigente se puede reconstruir con la
    # entrega, la regla y el actor que lo produjeron.
    db.add(ResultadoCruceHistorial(
        proyecto_id=proyecto_id, punto_id=punto, anio=anio,
        entrega_id=entrega_id, a=a_db, b=b_db, c=c_db,
        resultado=nombre_res,
        clase_consolidada=cruce.clase_consolidada,
        regla_version=cruce.regla_version,
        actor=actor,
    ))
    db.flush()

    # Ficha QA/QC automática solo para discrepancia absoluta (3 clases
    # distintas) o SC, siempre con la terna completa. La mayoría 2/3 se
    # consolida sin ficha y la terna incompleta no es un desacuerdo (protocolo
    # 4.2 y 6.3); la regla vive en el dominio para no divergir de Store.
    if requiere_ficha_qaqc(valores.values()) and ficha is None:
        db.add(FichaQAQC(
            proyecto_id=proyecto_id, punto_id=punto, anio=anio,
            estado=EstadoQAQC.DETECTADO.value,
            valores_iniciales={k: (v.value if isinstance(v, ClaseCobertura) else v)
                               for k, v in valores.items()},
        ))
        db.flush()
    return cruce

def _muestra_con_mapa(db: Session, proyecto_id: int) -> list:
    """Pares (mapa, consenso) solo con clase del mapa cargada (5.1).

    Las filas de la matriz son las clases MADES y las columnas la
    referencia de la terna; usar una evaluadora como mapa esta prohibido.
    """
    filas = db.execute(
        select(ClaseMapa.clase, ResultadoCruce.clase_consolidada)
        .join(ResultadoCruce, (ResultadoCruce.proyecto_id == ClaseMapa.proyecto_id)
              & (ResultadoCruce.punto_id == ClaseMapa.punto_id)
              & (ResultadoCruce.anio == ClaseMapa.anio))
        .where(ClaseMapa.proyecto_id == proyecto_id,
               ResultadoCruce.clase_consolidada.is_not(None))).all()
    return [(_CLASES[mapa - 1], _CLASES[cons - 1])
            for mapa, cons in filas if mapa is not None and cons is not None]


def matriz_consenso(db: Session, proyecto_id: int) -> dict:
    muestra = _muestra_con_mapa(db, proyecto_id)
    if not muestra:
        raise StoreError(ERROR_MAPA_NO_CARGADO,
                         "Sin clases del mapa MADES: cargue el mapa antes de la Etapa 2.")
    k = len(_CLASES)
    matriz = [[0] * k for _ in range(k)]
    for mapa, consenso in muestra:
        matriz[_CLASES.index(mapa)][_CLASES.index(consenso)] += 1
    return {"clases": [c.value for c in _CLASES], "matriz": matriz,
            "total_registros": len(muestra)}


def seccion5(db: Session, proyecto_id: int, area_total_ha: float, pesos: list):
    muestra = _muestra_con_mapa(db, proyecto_id)
    if not muestra:
        raise StoreError(ERROR_MAPA_NO_CARGADO,
                         "Sin clases del mapa MADES: cargue el mapa antes de la Etapa 2.")
    return analisis_seccion5(muestra, area_total_ha, pesos)


# --- Consultas ----------------------------------------------------------------

def listar_cruces(db: Session, proyecto_id: int, resultado: str | None = None,
                  punto: int | None = None, anio: int | None = None,
                  page: int = 1, per_page: int = 50) -> tuple[list, int, int]:
    """Cruces de la terna paginados, con el id de ficha QA/QC si existe.

    Devuelve ``(filas, total, page)``; ``page`` se ajusta a la última página
    cuando el cliente pide una fuera de rango. Cada fila es
    ``(ResultadoCruce, id_ficha)`` y ``total`` cuenta los cruces que cumplen
    los filtros (sin paginar).

    El id de la ficha se resuelve con una subconsulta escalar correlacionada en
    lugar de un ``outerjoin``: un cruce tiene como maximo una ficha, pero si en
    el futuro apareciera un duplicado (mismo punto-anio) el join multiplicaria
    filas y descuadraria el total frente a las filas de la pagina. Comprobado en
    la base del contenedor ``eet-mapa-aep`` antes de implementar::

        SELECT punto_id, anio, count(*) FROM fichas_qaqc
        GROUP BY 1, 2 HAVING count(*) > 1;   -- 0 filas

    La subconsulta deja el resultado estable sin depender de esa comprobacion.
    """
    page = max(int(page or 1), 1)
    per_page = max(int(per_page or 50), 1)

    cond = [ResultadoCruce.proyecto_id == proyecto_id]
    if resultado:
        cond.append(ResultadoCruce.resultado == resultado)
    if punto:
        cond.append(ResultadoCruce.punto_id == punto)
    if anio:
        cond.append(ResultadoCruce.anio == anio)

    total = db.scalar(
        select(func.count()).select_from(ResultadoCruce).where(*cond)) or 0
    paginas = max(1, (int(total) + per_page - 1) // per_page)
    page = min(page, paginas)

    ficha_id = (
        select(FichaQAQC.id)
        .where(FichaQAQC.proyecto_id == ResultadoCruce.proyecto_id,
               FichaQAQC.punto_id == ResultadoCruce.punto_id,
               FichaQAQC.anio == ResultadoCruce.anio)
        .order_by(FichaQAQC.id)
        .limit(1)
        .correlate(ResultadoCruce)
        .scalar_subquery()
    )

    filas = list(db.execute(
        select(ResultadoCruce, ficha_id.label("ficha_id"))
        .where(*cond)
        .order_by(ResultadoCruce.punto_id, ResultadoCruce.anio)
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all())
    return filas, total, page


def conteo_resultados(db: Session, proyecto_id: int) -> dict[str, int]:
    """Conteo de cruces por resultado, con las cuatro claves siempre presentes.

    Alimenta los chips de filtro de la vista de cruces: si un resultado no
    aparece en la base (por ejemplo ``incompleto``), el chip muestra 0 en lugar
    de desaparecer.
    """
    conteo = dict(db.execute(
        select(ResultadoCruce.resultado, func.count())
        .where(ResultadoCruce.proyecto_id == proyecto_id)
        .group_by(ResultadoCruce.resultado)
    ).all())
    return {clave: conteo.get(clave, 0)
            for clave in ("unanimidad", "mayoria", "discrepancia", "incompleto")}


def detalle_cruce(db: Session, proyecto_id: int, punto: int, anio: int) -> dict | None:
    cruce = db.scalar(select(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == proyecto_id,
        ResultadoCruce.punto_id == punto, ResultadoCruce.anio == anio))
    if cruce is None:
        return None
    origen: list[dict[str, object]] = []
    for letra in ("A", "B", "C"):
        i = _ultima_interpretacion(db, proyecto_id, punto, anio, letra)
        if i is None:
            origen.append({"letra": letra, "clase": None, "entrega": None})
        else:
            e = db.get(Entrega, i.entrega_id)
            entrega = (None if e is None else {
                "id": e.id, "semana": e.semana,
                "version": e.version, "sha": e.sha256[:10],
            })
            origen.append({"letra": letra,
                           "clase": (i.clase if not i.es_sc else "SC"),
                           "entrega": entrega})
    ficha = db.scalar(select(FichaQAQC).where(
        FichaQAQC.proyecto_id == proyecto_id,
        FichaQAQC.punto_id == punto, FichaQAQC.anio == anio))
    return {"cruce": cruce, "origen": origen, "ficha": ficha}


# --- QA/QC --------------------------------------------------------------------
