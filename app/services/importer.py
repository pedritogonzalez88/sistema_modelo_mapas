import re
from dataclasses import dataclass, field
from io import BytesIO
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from ..domain.classes import ALIASES_SC, ClaseCobertura, MAPA_ETIQUETAS, SIN_CONSENSO

ERROR_ETIQUETA_INVALIDA = "ETIQUETA_INVALIDA"
ERROR_ID_EV_NO_RESUELTO = "ID_EV_NO_RESUELTO"
ERROR_EVALUADORA_DESCONOCIDA = "EVALUADORA_DESCONOCIDA"
ERROR_ID_PUNTO_INVALIDO = "ID_PUNTO_INVALIDO"
ERROR_DUPLICADO_CONFLICTIVO = "DUPLICADO_CONFLICTIVO"
ERROR_FORMULA_SIN_CACHE = "FORMULA_SIN_CACHE"
ERROR_HOJA_NO_RECONOCIDA = "HOJA_NO_RECONOCIDA"

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
_RE_DECIMAL = re.compile(r"^[+-]?(\d+)[.,](\d+)$")
# Hojas de portada/ayuda que no contienen cruces y se ignoran sin error.
_RE_HOJA_AUXILIAR = re.compile(
    r"^(?:portada|car[aá]tula|indice|índice|instrucciones|ayuda|leyenda|notas?|"
    r"resumen|par[aá]metros|parametros|configuraci[oó]n|configuracion|readme)$",
    re.IGNORECASE,
)
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
    if id_ev is None or isinstance(id_ev, bool):
        return None
    # Excel puede entregar 1.0/2.0/3.0 como float; no deben convertirse en
    # "10"/"20"/"30" al quitar los separadores.
    if isinstance(id_ev, (int, float)):
        if isinstance(id_ev, float) and not id_ev.is_integer():
            return None
        return {"1": "A", "2": "B", "3": "C"}.get(str(int(id_ev)))
    texto = str(id_ev).strip()
    decimal = _RE_DECIMAL.fullmatch(texto)
    if decimal and set(decimal.group(2)) == {"0"}:
        texto = decimal.group(1)
    plano = _id_plano(texto)
    if plano in {"a", "b", "c"}:
        return plano.upper()
    if plano in {"1", "2", "3"}:
        return {"1": "A", "2": "B", "3": "C"}[plano]
    coincidencia = _RE_EVALUADORA.match(texto)
    if coincidencia:
        letra = coincidencia.group(1).upper()
        return {"1": "A", "2": "B", "3": "C"}.get(letra, letra)
    return None


def _parsear_punto(valor):
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        # 101.0 -> 101; 101.5 no es un identificador de punto válido.
        return int(valor) if valor.is_integer() else None
    texto = str(valor).strip()
    if not texto:
        return None
    decimal = _RE_DECIMAL.fullmatch(texto)
    if decimal:
        entero, fraccion = decimal.groups()
        if set(fraccion) != {"0"}:
            return None
        return int(entero)
    coincidencia = _RE_DIGITOS.search(_id_plano(texto))
    if coincidencia is None:
        return None
    return int(coincidencia.group(0))


def _celda_vacia(valor):
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _fila_vacia(fila):
    return fila is None or all(_celda_vacia(celda) for celda in fila)


def _es_formula(valor):
    return isinstance(valor, str) and valor.lstrip().startswith("=")


def _consumir_filas_con_datos(filas):
    """Consume la hoja hasta saber si tiene al menos una fila con datos."""
    for fila in filas:
        if not _fila_vacia(fila):
            return True
    return False


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


def abrir_libro(contenido: bytes, *, data_only: bool = True):
    """Abre un .xlsx y convierte errores de formato en ``ValueError``.

    Así los routers responden 400/HTML en lugar de 500 ante un archivo
    corrupto, un CSV renombrado o un ZIP inválido.
    """
    try:
        return load_workbook(BytesIO(contenido), data_only=data_only,
                             read_only=True)
    except (BadZipFile, InvalidFileException, KeyError, OSError, TypeError,
            ValueError) as exc:
        raise ValueError("El archivo no es un .xlsx válido.") from exc


def importar_excel(contenido):
    # Se abren dos vistas del mismo libro: los valores calculados y las
    # fórmulas. Así se detecta una fórmula sin caché (``data_only=True`` la
    # devuelve como None) en lugar de importarla como celda vacía.
    libro = abrir_libro(contenido, data_only=True)
    libro_formulas = None
    try:
        libro_formulas = abrir_libro(contenido, data_only=False)
        return _procesar_libro(libro, libro_formulas)
    finally:
        if libro_formulas is not None:
            libro_formulas.close()
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


def _procesar_libro(libro, libro_formulas=None):
    datos = {}
    errores = []
    puntos_meta = {}
    movimientos = {}
    registros = 0
    hojas_formulas = {}
    if libro_formulas is not None:
        hojas_formulas = {hoja.title: hoja for hoja in libro_formulas.worksheets}

    for hoja in libro.worksheets:
        hoja_formulas = hojas_formulas.get(hoja.title)
        filas = hoja.iter_rows(values_only=True)
        filas_formulas = (hoja_formulas.iter_rows(values_only=True)
                          if hoja_formulas is not None else None)
        encabezado = next(filas, None)
        if filas_formulas is not None:
            next(filas_formulas, None)
        if _fila_vacia(encabezado):
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

        clave_hoja = _clave_evaluadora(hoja.title)
        auxiliar = bool(_RE_HOJA_AUXILIAR.match(str(hoja.title).strip()))
        if idx_punto is None or not anios:
            if not auxiliar and _consumir_filas_con_datos(filas):
                errores.append({
                    "tipo": ERROR_HOJA_NO_RECONOCIDA,
                    "mensaje": (f"La hoja '{hoja.title}' no tiene una columna de "
                                "punto o de año; no se importó."),
                    "hoja": hoja.title,
                })
            continue
        if idx_ev is None and clave_hoja is None:
            if not auxiliar and _consumir_filas_con_datos(filas):
                errores.append({
                    "tipo": ERROR_HOJA_NO_RECONOCIDA,
                    "mensaje": (f"La hoja '{hoja.title}' no tiene ID_EV y su "
                                "nombre no identifica a la evaluadora (A, B o C)."),
                    "hoja": hoja.title,
                })
            continue

        indices_relevantes = {i for i in (idx_ev, idx_punto, idx_x, idx_y,
                                          idx_nota) if i is not None}
        indices_relevantes |= set(anios)

        for numero, fila in enumerate(filas, start=2):
            fila_formulas = (next(filas_formulas, None)
                             if filas_formulas is not None else None)
            if _fila_vacia(fila):
                continue

            faltantes = []
            if fila_formulas is not None:
                for indice in indices_relevantes:
                    if (indice < len(fila) and _celda_vacia(fila[indice])
                            and indice < len(fila_formulas)
                            and _es_formula(fila_formulas[indice])):
                        faltantes.append(indice)
            if faltantes:
                for indice in faltantes:
                    celda = f"{get_column_letter(indice + 1)}{numero}"
                    errores.append({
                        "tipo": ERROR_FORMULA_SIN_CACHE,
                        "mensaje": (f"La celda {celda} de la hoja {hoja.title} "
                                    "contiene una fórmula sin valor calculado; "
                                    "abra y guarde el archivo en Excel o pegue "
                                    "los valores."),
                        "fila": numero,
                        "celda": celda,
                    })
                continue

            id_ev = fila[idx_ev] if idx_ev is not None and idx_ev < len(fila) else None
            id_ev_presente = not _celda_vacia(id_ev)
            clave_ev = _clave_evaluadora(id_ev) if id_ev_presente else None
            # La hoja solo identifica a la evaluadora cuando la fila no trae
            # un ID_EV. Un ID_EV desconocido no se sustituye en silencio.
            if clave_ev is None and not id_ev_presente and clave_hoja is not None:
                clave_ev = clave_hoja

            if clave_ev is None:
                errores.append({
                    "tipo": (ERROR_EVALUADORA_DESCONOCIDA if id_ev_presente
                             else ERROR_ID_EV_NO_RESUELTO),
                    "mensaje": (
                        f"La evaluadora '{id_ev}' de la fila {numero} no es reconocida."
                        if id_ev_presente else
                        f"La fila {numero} de la hoja {hoja.title} no tiene un ID_EV."
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
                    "mensaje": f"El ID_PUNTO '{id_punto}' de la fila {numero} no contiene un número de punto válido.",
                    "fila": numero,
                })
                continue
            x = _a_float(fila[idx_x]) if idx_x is not None and idx_x < len(fila) else None
            y = _a_float(fila[idx_y]) if idx_y is not None and idx_y < len(fila) else None
            nota_fila = None
            if (idx_nota is not None and idx_nota < len(fila)
                    and not _celda_vacia(fila[idx_nota])):
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

    if not datos and not errores:
        errores.append({
            "tipo": ERROR_HOJA_NO_RECONOCIDA,
            "mensaje": "El archivo no contiene filas con datos de cruce.",
        })
    nota = f"Se importaron {len(datos)} cruces puntuales con {registros} evaluaciones registradas y {len(errores)} errores."
    return ResultadoImportacion(datos=datos, errores=errores, nota=nota,
                                  puntos_meta=puntos_meta, movimientos=movimientos)