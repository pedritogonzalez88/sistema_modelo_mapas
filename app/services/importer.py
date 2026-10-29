import re
from dataclasses import dataclass, field
from io import BytesIO

from openpyxl import load_workbook

from ..domain.classes import ALIASES_SC, ClaseCobertura, MAPA_ETIQUETAS, SIN_CONSENSO

ERROR_ETIQUETA_INVALIDA = "ETIQUETA_INVALIDA"
ERROR_ID_EV_NO_RESUELTO = "ID_EV_NO_RESUELTO"
ERROR_EVALUADORA_DESCONOCIDA = "EVALUADORA_DESCONOCIDA"
ERROR_ID_PUNTO_INVALIDO = "ID_PUNTO_INVALIDO"
ERROR_DUPLICADO_CONFLICTIVO = "DUPLICADO_CONFLICTIVO"

CLAVE_POR_EVALUADORA = {
    "A": "A", "B": "B", "C": "C",
    "1": "A", "2": "B", "3": "C",
}

_RE_ID_EV = re.compile(r"^(?:ID[_ ]?)?(?:EV|EVALUADOR|EVALUADORA|ASISTENTE)$", re.IGNORECASE)
_RE_ID_PUNTO = re.compile(r"^(?:ID[_ ]?PUNTO|PUNTO[_ ]?ID|PUNTO)$", re.IGNORECASE)
_RE_ANIO = re.compile(r"^A?[-_ ]?((?:19|20)\d{2})$", re.IGNORECASE)
_RE_EVALUADORA = re.compile(r"^(?:evaluadora?|asistente|ev|eval)[_\- ]?([a-c1-3])$", re.IGNORECASE)
_RE_DIGITOS = re.compile(r"\d+")
_RE_NO_ALNUM = re.compile(r"[^a-z0-9]")
_RE_X = re.compile(r"^X$", re.IGNORECASE)
_RE_Y = re.compile(r"^Y$", re.IGNORECASE)
_RE_NOTAS = re.compile(r"^notas?$", re.IGNORECASE)
# Protocolo 4.6 / Tabla 5: "R558, N" = punto reemplazado por la reserva 558
# por causa N|B|T; "A12" = reserva activada que rescata al punto 12.
_RE_NOTA_R = re.compile(r"^R\s*(\d+)\s*,\s*([NBT])$", re.IGNORECASE)
_RE_NOTA_A = re.compile(r"^A\s*(\d+)$", re.IGNORECASE)
CAUSAS_RESERVA = {"N": "Nubosidad persistente", "B": "Bandeado del sensor",
                  "T": "Transición oculta"}


@dataclass
class ResultadoImportacion:
    datos: dict = field(default_factory=dict)
    errores: list = field(default_factory=list)
    nota: str = ""
    # Metadatos por punto (coordenadas y nota de la fila): {punto: {"x", "y", "nota"}}
    puntos_meta: dict = field(default_factory=dict)
    # Movimientos de reserva: {punto: {"accion": "R"|"A", "ref": int, "causa": "N"|"B"|"T"|None}}
    movimientos: dict = field(default_factory=dict)


def _id_plano(valor):
    return _RE_NO_ALNUM.sub("", str(valor).lower())


def _clave_evaluadora(id_ev):
    if id_ev is None:
        return None
    plano = _id_plano(id_ev)
    if plano in {"a", "b", "c"}:
        return plano.upper()
    if plano in {"1", "2", "3"}:
        return {"1": "A", "2": "B", "3": "C"}[plano]
    coincidencia = _RE_EVALUADORA.match(str(id_ev).strip())
    if coincidencia:
        letra = coincidencia.group(1).upper()
        return {"1": "A", "2": "B", "3": "C"}.get(letra, letra)
    return None


def _parsear_punto(valor):
    if valor is None:
        return None
    coincidencia = _RE_DIGITOS.search(_id_plano(valor))
    if coincidencia is None:
        return None
    return int(coincidencia.group(0))


def _parsear_etiqueta(valor):
    if valor is None:
        return None, None
    if isinstance(valor, ClaseCobertura):
        return valor, None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = str(valor).strip()
    if not texto:
        return None, None
    if texto.endswith(".0"):
        try:
            texto = str(int(float(texto)))
        except ValueError:
            pass
    if texto.lower() in ALIASES_SC:
        return SIN_CONSENSO, None
    clase = MAPA_ETIQUETAS.get(texto.lower())
    if clase is not None:
        return clase, None
    return None, ERROR_ETIQUETA_INVALIDA


def importar_excel(contenido):
    libro = load_workbook(BytesIO(contenido), data_only=True, read_only=True)
    try:
        return _procesar_libro(libro)
    finally:
        libro.close()


def _parsear_movimiento(nota_fila):
    """Extrae el codigo estructurado de reemplazo (4.6) o None si es texto libre."""
    if not nota_fila:
        return None
    m = _RE_NOTA_R.match(nota_fila.strip())
    if m:
        return {"accion": "R", "ref": int(m.group(1)), "causa": m.group(2).upper()}
    m = _RE_NOTA_A.match(nota_fila.strip())
    if m:
        return {"accion": "A", "ref": int(m.group(1)), "causa": None}
    return None


def _a_float(valor):
    if valor is None or (isinstance(valor, str) and valor.strip() == ""):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _procesar_libro(libro):
    datos = {}
    errores = []
    puntos_meta = {}
    movimientos = {}
    registros = 0
    for hoja in libro.worksheets:
        clave_hoja = _clave_evaluadora(hoja.title)
        filas = hoja.iter_rows(values_only=True)
        encabezado = next(filas, None)
        if encabezado is None:
            continue
        idx_ev = None
        idx_punto = None
        idx_x = None
        idx_y = None
        idx_nota = None
        anios = {}
        for indice, celda in enumerate(encabezado):
            texto = "" if celda is None else str(celda).strip()
            if idx_ev is None and _RE_ID_EV.match(texto):
                idx_ev = indice
            if idx_punto is None and _RE_ID_PUNTO.match(texto):
                idx_punto = indice
            if idx_x is None and _RE_X.match(texto):
                idx_x = indice
            if idx_y is None and _RE_Y.match(texto):
                idx_y = indice
            if idx_nota is None and _RE_NOTAS.match(texto):
                idx_nota = indice
            coincidencia = _RE_ANIO.match(texto)
            if coincidencia:
                anios[indice] = int(coincidencia.group(1))

        if idx_punto is None or not anios:
            continue
        if idx_ev is None and clave_hoja is None:
            continue

        for numero, fila in enumerate(filas, start=2):
            if fila is None:
                continue
            if all(celda is None or str(celda).strip() == "" for celda in fila):
                continue
            id_ev = fila[idx_ev] if idx_ev is not None and idx_ev < len(fila) else None
            clave_ev = _clave_evaluadora(id_ev) if id_ev is not None else None
            if clave_ev is None and clave_hoja is not None:
                clave_ev = clave_hoja

            if clave_ev is None:
                errores.append({
                    "tipo": ERROR_ID_EV_NO_RESUELTO if not id_ev else ERROR_EVALUADORA_DESCONOCIDA,
                    "mensaje": (
                        f"La fila {numero} de la hoja {hoja.title} no tiene un ID_EV."
                        if not id_ev else
                        f"La evaluadora '{id_ev}' de la fila {numero} no es reconocida."
                    ),
                    "fila": numero,
                })
                continue
            if clave_hoja is not None and clave_ev != clave_hoja:
                errores.append({
                    "tipo": ERROR_EVALUADORA_DESCONOCIDA,
                    "mensaje": f"El ID_EV '{id_ev}' de la fila {numero} no coincide con la hoja {hoja.title}.",
                    "fila": numero,
                })
                continue
            id_punto = fila[idx_punto] if idx_punto < len(fila) else None
            punto = _parsear_punto(id_punto)
            if punto is None:
                errores.append({
                    "tipo": ERROR_ID_PUNTO_INVALIDO,
                    "mensaje": f"El ID_PUNTO '{id_punto}' de la fila {numero} no contiene un número de punto.",
                    "fila": numero,
                })
                continue
            x = _a_float(fila[idx_x]) if idx_x is not None and idx_x < len(fila) else None
            y = _a_float(fila[idx_y]) if idx_y is not None and idx_y < len(fila) else None
            nota_fila = None
            if idx_nota is not None and idx_nota < len(fila) and fila[idx_nota] not in (None, ""):
                nota_fila = str(fila[idx_nota]).strip() or None
            if (x is not None or y is not None or nota_fila) and punto not in puntos_meta:
                puntos_meta[punto] = {"x": x, "y": y, "nota": nota_fila}
            mov = _parsear_movimiento(nota_fila)
            if mov is not None and punto not in movimientos:
                movimientos[punto] = mov
            for indice, anio in anios.items():
                valor = fila[indice] if indice < len(fila) else None
                etiqueta, err = _parsear_etiqueta(valor)
                if err is not None:
                    errores.append({
                        "tipo": err,
                        "mensaje": f"El valor '{valor}' de la fila {numero} no es una clase de cobertura válida.",
                        "fila": numero,
                    })
                    continue
                registro = datos.setdefault((punto, anio), {})
                if clave_ev in registro and registro[clave_ev] != etiqueta:
                    errores.append({
                        "tipo": ERROR_DUPLICADO_CONFLICTIVO,
                        "mensaje": f"Valor duplicado y contradictorio para el punto {punto}, año {anio}: '{registro[clave_ev]}' vs '{etiqueta}'.",
                        "fila": numero,
                    })
                else:
                    registro[clave_ev] = etiqueta
                    registros += 1
    nota = f"Se importaron {len(datos)} cruces puntuales con {registros} evaluaciones registradas y {len(errores)} errores."
    return ResultadoImportacion(datos=datos, errores=errores, nota=nota,
                                  puntos_meta=puntos_meta, movimientos=movimientos)