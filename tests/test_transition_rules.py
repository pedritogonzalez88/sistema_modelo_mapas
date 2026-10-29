"""Pruebas para app.domain.transition_rules."""
import pytest

from app.domain.classes import ClaseCobertura
from app.domain.transition_rules import Plausibilidad, resolver_plausibilidad

BN = ClaseCobertura.BOSQUE_NATURAL
PL = ClaseCobertura.PLANTACIONES_FORESTALES
OT = ClaseCobertura.OTRAS_TIERRAS
OTF = ClaseCobertura.OTRAS_TIERRAS_FORESTALES
TC = ClaseCobertura.TIERRAS_DE_CULTIVO
PA = ClaseCobertura.PASTIZALES
AS = ClaseCobertura.ASENTAMIENTOS


def test_sin_mapa_anterior_es_probable():
    assert resolver_plausibilidad(None, BN) is Plausibilidad.PROBABLE


def test_misma_clase_es_probable():
    assert resolver_plausibilidad(BN, BN) is Plausibilidad.PROBABLE


def test_asentamientos_previos_son_improbables():
    assert resolver_plausibilidad(AS, BN) is Plausibilidad.IMPROBABLE


@pytest.mark.parametrize(
    "consenso, esperado",
    [
        (BN, Plausibilidad.IMPROBABLE),
        # Protocolo 4.10.4: "Otras tierras a Plantaciones forestales (Posible)".
        (PL, Plausibilidad.POSIBLE),
        (ClaseCobertura.PALMAR, Plausibilidad.IMPROBABLE),
        (ClaseCobertura.OTRAS_TIERRAS_FORESTALES, Plausibilidad.IMPROBABLE),
        (PA, Plausibilidad.POSIBLE),
        (TC, Plausibilidad.POSIBLE),
        (ClaseCobertura.HUMEDALES, Plausibilidad.POSIBLE),
        (AS, Plausibilidad.POSIBLE),
    ],
)
def test_reglas_desde_otras_tierras(consenso, esperado):
    assert resolver_plausibilidad(OT, consenso) is esperado


def test_permanencia_en_otras_tierras_es_probable():
    # La autotransicion (sin cambio) no esta en la Tabla 6: es Probable.
    assert resolver_plausibilidad(OT, OT) is Plausibilidad.PROBABLE


@pytest.mark.parametrize(
    "consenso, esperado",
    [
        (PA, Plausibilidad.POSIBLE),
        (ClaseCobertura.HUMEDALES, Plausibilidad.CONDICIONADA),
        (AS, Plausibilidad.POSIBLE),
        (OT, Plausibilidad.POSIBLE),
    ],
)
def test_reglas_desde_tierras_de_cultivo(consenso, esperado):
    assert resolver_plausibilidad(TC, consenso) is esperado


def test_fallback_condicionada():
    # Destino fuera del esquema 1-9 o sin consenso: precaucion (revisar).
    assert resolver_plausibilidad(BN, "Zona ignota") is Plausibilidad.CONDICIONADA
    assert resolver_plausibilidad(OT, None) is Plausibilidad.CONDICIONADA


# Matriz completa de la Tabla 6 (4.10.4): origen -> {destino: plausibilidad}.
# Cualquier cambio futuro del protocolo debe actualizar esta tabla y el codigo.
_MATRIZ_TABLA6 = {
    BN: {"PL": "Condicionada", "PAL": "Condicionada", "OTF": "Condicionada",
         "PA": "Posible", "TC": "Posible", "HUM": "Condicionada",
         "AS": "Posible", "OT": "Posible"},
    PL: {"BN": "Improbable", "PAL": "Improbable", "OTF": "Improbable",
         "PA": "Posible", "TC": "Posible", "HUM": "Improbable",
         "AS": "Improbable", "OT": "Posible"},
    "PAL": {"BN": "Improbable", "PL": "Posible", "OTF": "Condicionada",
            "PA": "Posible", "TC": "Posible", "HUM": "Condicionada",
            "AS": "Posible", "OT": "Posible"},
    OTF: {"BN": "Improbable", "PL": "Condicionada", "PAL": "Condicionada",
          "PA": "Posible", "TC": "Posible", "HUM": "Condicionada",
          "AS": "Posible", "OT": "Posible"},
    PA: {"BN": "Improbable", "PL": "Posible", "PAL": "Improbable",
         "OTF": "Posible", "TC": "Posible", "HUM": "Condicionada",
         "AS": "Posible", "OT": "Posible"},
    TC: {"BN": "Improbable", "PL": "Posible", "PAL": "Improbable",
         "OTF": "Improbable", "PA": "Posible", "HUM": "Condicionada",
         "AS": "Posible", "OT": "Posible"},
    "HUM": {"BN": "Improbable", "PL": "Improbable", "PAL": "Condicionada",
            "OTF": "Condicionada", "PA": "Condicionada", "TC": "Condicionada",
            "AS": "Improbable", "OT": "Improbable"},
    AS: {"BN": "Improbable", "PL": "Improbable", "PAL": "Improbable",
         "OTF": "Improbable", "PA": "Improbable", "TC": "Improbable",
         "HUM": "Improbable", "OT": "Improbable"},
    OT: {"BN": "Improbable", "PL": "Posible", "PAL": "Improbable",
         "OTF": "Improbable", "PA": "Posible", "TC": "Posible",
         "HUM": "Posible", "AS": "Posible"},
}

_ALIAS = {"PAL": ClaseCobertura.PALMAR, "HUM": ClaseCobertura.HUMEDALES,
          "OTF": ClaseCobertura.OTRAS_TIERRAS_FORESTALES}


def _clase(nombre):
    # Ojo: ClaseCobertura hereda de str; el chequeo de Enum va primero.
    if isinstance(nombre, ClaseCobertura):
        return nombre
    if nombre in _ALIAS:
        return _ALIAS[nombre]
    return {"BN": BN, "PL": PL, "PA": PA, "TC": TC, "AS": AS, "OT": OT}[nombre]


def test_matriz_completa_tabla6():
    casos = []
    for origen, destinos in _MATRIZ_TABLA6.items():
        origen_cls = _clase(origen)
        for destino, esperado in destinos.items():
            casos.append((origen_cls, _clase(destino), Plausibilidad(esperado)))
    assert len(casos) == 72  # 9 origenes x 8 destinos (sin autotransicion)
    for origen, destino, esperado in casos:
        assert resolver_plausibilidad(origen, destino) is esperado, f"{origen}->{destino}"
