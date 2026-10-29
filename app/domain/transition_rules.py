"""Restricciones logicas de transicion anual entre coberturas (Tabla 6, 4.10.4).

Matriz completa 9x9 relevada del Protocolo EET V4:
  Posible      = el cambio puede ocurrir en un anio (no requiere revision).
  Condicionada = poco comun en un anio; requiere validacion con datos puntuales.
  Improbable   = ecologica/biologicamente inviable en un anio; revision exhaustiva.

Estas reglas son SOLO un mecanismo de deteccion de posibles errores
(4.10: "no deben utilizarse automaticamente para corregir la clasificacion").
La permanencia en la misma clase se considera Probable (sin cambio).
"""
from enum import Enum

from app.domain.classes import ClaseCobertura


class Plausibilidad(str, Enum):
    PROBABLE = "Probable"
    POSIBLE = "Posible"
    CONDICIONADA = "Condicionada"
    IMPROBABLE = "Improbable"


BN = ClaseCobertura.BOSQUE_NATURAL
PL = ClaseCobertura.PLANTACIONES_FORESTALES
PAL = ClaseCobertura.PALMAR
OTF = ClaseCobertura.OTRAS_TIERRAS_FORESTALES
PA = ClaseCobertura.PASTIZALES
TC = ClaseCobertura.TIERRAS_DE_CULTIVO
HUM = ClaseCobertura.HUMEDALES
AS = ClaseCobertura.ASENTAMIENTOS
OT = ClaseCobertura.OTRAS_TIERRAS

_REGLA = {
    BN: {PL: Plausibilidad.CONDICIONADA, PAL: Plausibilidad.CONDICIONADA,
         OTF: Plausibilidad.CONDICIONADA, PA: Plausibilidad.POSIBLE,
         TC: Plausibilidad.POSIBLE, HUM: Plausibilidad.CONDICIONADA,
         AS: Plausibilidad.POSIBLE, OT: Plausibilidad.POSIBLE},
    PL: {BN: Plausibilidad.IMPROBABLE, PAL: Plausibilidad.IMPROBABLE,
         OTF: Plausibilidad.IMPROBABLE, PA: Plausibilidad.POSIBLE,
         TC: Plausibilidad.POSIBLE, HUM: Plausibilidad.IMPROBABLE,
         AS: Plausibilidad.IMPROBABLE, OT: Plausibilidad.POSIBLE},
    PAL: {BN: Plausibilidad.IMPROBABLE, PL: Plausibilidad.POSIBLE,
          OTF: Plausibilidad.CONDICIONADA, PA: Plausibilidad.POSIBLE,
          TC: Plausibilidad.POSIBLE, HUM: Plausibilidad.CONDICIONADA,
          AS: Plausibilidad.POSIBLE, OT: Plausibilidad.POSIBLE},
    OTF: {BN: Plausibilidad.IMPROBABLE, PL: Plausibilidad.CONDICIONADA,
          PAL: Plausibilidad.CONDICIONADA, PA: Plausibilidad.POSIBLE,
          TC: Plausibilidad.POSIBLE, HUM: Plausibilidad.CONDICIONADA,
          AS: Plausibilidad.POSIBLE, OT: Plausibilidad.POSIBLE},
    PA: {BN: Plausibilidad.IMPROBABLE, PL: Plausibilidad.POSIBLE,
         PAL: Plausibilidad.IMPROBABLE, OTF: Plausibilidad.POSIBLE,
         TC: Plausibilidad.POSIBLE, HUM: Plausibilidad.CONDICIONADA,
         AS: Plausibilidad.POSIBLE, OT: Plausibilidad.POSIBLE},
    TC: {BN: Plausibilidad.IMPROBABLE, PL: Plausibilidad.POSIBLE,
         PAL: Plausibilidad.IMPROBABLE, OTF: Plausibilidad.IMPROBABLE,
         PA: Plausibilidad.POSIBLE, HUM: Plausibilidad.CONDICIONADA,
         AS: Plausibilidad.POSIBLE, OT: Plausibilidad.POSIBLE},
    HUM: {BN: Plausibilidad.IMPROBABLE, PL: Plausibilidad.IMPROBABLE,
          PAL: Plausibilidad.CONDICIONADA, OTF: Plausibilidad.CONDICIONADA,
          PA: Plausibilidad.CONDICIONADA, TC: Plausibilidad.CONDICIONADA,
          AS: Plausibilidad.IMPROBABLE, OT: Plausibilidad.IMPROBABLE},
    AS: {BN: Plausibilidad.IMPROBABLE, PL: Plausibilidad.IMPROBABLE,
         PAL: Plausibilidad.IMPROBABLE, OTF: Plausibilidad.IMPROBABLE,
         PA: Plausibilidad.IMPROBABLE, TC: Plausibilidad.IMPROBABLE,
         HUM: Plausibilidad.IMPROBABLE, OT: Plausibilidad.IMPROBABLE},
    OT: {BN: Plausibilidad.IMPROBABLE, PL: Plausibilidad.POSIBLE,
         PAL: Plausibilidad.IMPROBABLE, OTF: Plausibilidad.IMPROBABLE,
         PA: Plausibilidad.POSIBLE, TC: Plausibilidad.POSIBLE,
         HUM: Plausibilidad.POSIBLE, AS: Plausibilidad.POSIBLE},
}


def resolver_plausibilidad(mapa_anterior, consenso):
    if mapa_anterior is None or mapa_anterior == consenso:
        return Plausibilidad.PROBABLE
    if mapa_anterior in _REGLA:
        destino = _REGLA[mapa_anterior].get(consenso)
        if destino is not None:
            return destino
    return Plausibilidad.CONDICIONADA
