"""Fachada de compatibilidad de persistencia.

La implementación vive en módulos cohesivos: bootstrap, entregas, cruces,
malla, reemplazos, qaqc y dashboard. Se mantiene ``app.services.persist`` para
no romper imports existentes.
"""
from app.services.bootstrap import get_default_proyecto, seed
from app.services.common import (
    _CLASES,
    _ESTADOS_CERRADOS,
    ERROR_MAPA_NO_CARGADO,
    ESTADOS_REEMPLAZO,
    SRID_MALLA,
    ErrorEntrega,
    StoreError,
    _para_json,
    clase_a_db,
    clase_a_dominio,
)
from app.services.cruces import (
    _ultima_interpretacion,
    conteo_resultados,
    detalle_cruce,
    listar_cruces,
    matriz_consenso,
    recalcular_cruce,
    seccion5,
)
from app.services.dashboard import resumen_dashboard
from app.services.entregas import (
    confirmar_entrega,
    crear_entrega_pendiente,
    rechazar_entrega,
)
from app.services.malla import (
    _malla_de,
    _parsear_mapa_xlsx,
    cargar_malla,
    cargar_mapa,
    estado_mapa,
    parsear_malla,
)
from app.services.qaqc import actualizar_ficha, transicionar_ficha
from app.services.reemplazos import (
    _procesar_movimientos,
    crear_reemplazo_propuesto,
    listar_reemplazos,
    proponer_reserva,
    transicionar_reemplazo,
)

__all__ = [
    "ERROR_MAPA_NO_CARGADO",
    "ESTADOS_REEMPLAZO",
    "SRID_MALLA",
    "_CLASES",
    "_ESTADOS_CERRADOS",
    "ErrorEntrega",
    "StoreError",
    "_malla_de",
    "_para_json",
    "_parsear_mapa_xlsx",
    "_procesar_movimientos",
    "_ultima_interpretacion",
    "actualizar_ficha",
    "cargar_malla",
    "cargar_mapa",
    "clase_a_db",
    "clase_a_dominio",
    "confirmar_entrega",
    "conteo_resultados",
    "crear_entrega_pendiente",
    "crear_reemplazo_propuesto",
    "detalle_cruce",
    "estado_mapa",
    "get_default_proyecto",
    "listar_cruces",
    "listar_reemplazos",
    "matriz_consenso",
    "parsear_malla",
    "proponer_reserva",
    "recalcular_cruce",
    "rechazar_entrega",
    "resumen_dashboard",
    "seccion5",
    "seed",
    "transicionar_ficha",
    "transicionar_reemplazo",
]
