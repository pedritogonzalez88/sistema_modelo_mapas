"""Constantes, errores y conversiones compartidas de persistencia."""
from app.domain.classes import SIN_CONSENSO, ClaseCobertura

SRID_MALLA = 4326

_CLASES = list(ClaseCobertura)
_ESTADOS_CERRADOS = {"Resuelto por consenso", "Reemplazo completado"}
ESTADOS_REEMPLAZO = ("propuesto", "autorizado", "completado")
ERROR_MAPA_NO_CARGADO = "MAPA_NO_CARGADO"


class StoreError(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


class ErrorEntrega(ValueError):
    """Una entrega no puede confirmarse.

    ``codigo`` lo consume el router para redirigir a la vista previa con el
    flash correspondiente, sin perder el detalle del motivo.
    """

    def __init__(self, mensaje: str, codigo: str = "confirmacion_invalida"):
        super().__init__(mensaje)
        self.codigo = codigo


def _para_json(obj):
    """Convierte fechas a ISO para columnas JSONB (auditoria, detalles)."""
    from datetime import date, datetime

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _para_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_para_json(v) for v in obj]
    return obj


# --- Conversión dominio <-> BD -------------------------------------------------
def clase_a_db(valor) -> tuple[int | None, bool]:
    """Devuelve (codigo 1-9 o None, es_sc)."""
    if valor is None:
        return None, False
    if valor == SIN_CONSENSO:
        return None, True
    if isinstance(valor, ClaseCobertura):
        return _CLASES.index(valor) + 1, False
    return None, False


def clase_a_dominio(codigo: int | None, es_sc: bool = False):
    if es_sc:
        return SIN_CONSENSO
    if codigo is None:
        return None
    # Un índice negativo devolvería silenciosamente otra clase (0 -> Otras
    # tierras) y un 99 rompería más adelante con IndexError. Se falla aquí.
    if (isinstance(codigo, bool) or not isinstance(codigo, int)
            or not 1 <= codigo <= len(_CLASES)):
        raise ValueError(
            f"Código de clase fuera de rango (1-{len(_CLASES)}): {codigo!r}."
        )
    return _CLASES[codigo - 1]
