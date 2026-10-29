from enum import Enum

SIN_CONSENSO = "SC"


class ClaseCobertura(str, Enum):
    BOSQUE_NATURAL = "Bosque natural"
    PLANTACIONES_FORESTALES = "Plantaciones forestales"
    PALMAR = "Palmar"
    OTRAS_TIERRAS_FORESTALES = "Otras tierras forestales"
    PASTIZALES = "Pastizales"
    TIERRAS_DE_CULTIVO = "Tierras de cultivo"
    HUMEDALES = "Humedales"
    ASENTAMIENTOS = "Asentamientos"
    OTRAS_TIERRAS = "Otras tierras"


MAPA_ETIQUETAS = {
    "1": ClaseCobertura.BOSQUE_NATURAL,
    "2": ClaseCobertura.PLANTACIONES_FORESTALES,
    "3": ClaseCobertura.PALMAR,
    "4": ClaseCobertura.OTRAS_TIERRAS_FORESTALES,
    "5": ClaseCobertura.PASTIZALES,
    "6": ClaseCobertura.TIERRAS_DE_CULTIVO,
    "7": ClaseCobertura.HUMEDALES,
    "8": ClaseCobertura.ASENTAMIENTOS,
    "9": ClaseCobertura.OTRAS_TIERRAS,
    "bosque": ClaseCobertura.BOSQUE_NATURAL,
    "bosque natural": ClaseCobertura.BOSQUE_NATURAL,
    "plantacion forestal": ClaseCobertura.PLANTACIONES_FORESTALES,
    "plantaciones forestales": ClaseCobertura.PLANTACIONES_FORESTALES,
    "palmar": ClaseCobertura.PALMAR,
    "otras tierras forestales": ClaseCobertura.OTRAS_TIERRAS_FORESTALES,
    "otras tierras": ClaseCobertura.OTRAS_TIERRAS,
    "pastizales": ClaseCobertura.PASTIZALES,
    "tierra de cultivo": ClaseCobertura.TIERRAS_DE_CULTIVO,
    "tierras de cultivo": ClaseCobertura.TIERRAS_DE_CULTIVO,
    "humedales": ClaseCobertura.HUMEDALES,
    "asentamientos": ClaseCobertura.ASENTAMIENTOS,
}

ALIASES_SC = {"sc", "sin consenso", "s/c", "s/consenso"}


def normalizar_etiqueta(valor):
    if valor is None:
        return None
    if isinstance(valor, ClaseCobertura):
        return valor
    if valor == SIN_CONSENSO:
        return SIN_CONSENSO
    texto = str(valor).strip()
    if texto == "":
        return None
    if texto in ALIASES_SC:
        return SIN_CONSENSO
    return MAPA_ETIQUETAS.get(texto.lower(), texto)


def es_valido(valor):
    if valor is None:
        return True
    if isinstance(valor, ClaseCobertura):
        return True
    return valor == SIN_CONSENSO