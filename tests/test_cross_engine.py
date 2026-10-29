"""Pruebas para app.domain.cross_engine."""
from app.domain.classes import ClaseCobertura, SIN_CONSENSO
from app.domain.cross_engine import (
    Cruce,
    TipoResultado,
    ejecutar_cruce,
    resolver_tabla7,
)
from app.domain.transition_rules import Plausibilidad

BN = ClaseCobertura.BOSQUE_NATURAL
PA = ClaseCobertura.PASTIZALES
TC = ClaseCobertura.TIERRAS_DE_CULTIVO
AS = ClaseCobertura.ASENTAMIENTOS


def test_unanimidad_genera_validacion_automatica():
    resultado = resolver_tabla7(BN, BN, BN)
    assert resultado.consenso is BN
    assert resultado.resultado is TipoResultado.VALIDACION_AUTOMATICA
    assert resultado.requiere_consenso_tecnico is False
    assert resultado.requiere_revision is False
    assert resultado.minorias == []


def test_mayoria_simple_requiere_revision():
    resultado = resolver_tabla7(BN, BN, PA)
    assert resultado.consenso is BN
    assert resultado.resultado is TipoResultado.REQUIERE_REVISION
    assert resultado.requiere_revision is True
    assert resultado.requiere_consenso_tecnico is False
    assert resultado.minorias == [PA]


def test_tres_distintas_consenso_tecnico():
    resultado = resolver_tabla7(BN, PA, TC)
    assert resultado.consenso is None
    assert resultado.resultado is TipoResultado.CONSENSO_TECNICO
    assert resultado.requiere_consenso_tecnico is True
    assert resultado.requiere_revision is True


def test_valor_none_es_irresoluble():
    resultado = resolver_tabla7(BN, BN, None)
    assert resultado.resultado is TipoResultado.IRRESOLUBLE
    assert resultado.consenso is None
    assert resultado.requiere_consenso_tecnico is True
    assert resultado.requiere_revision is True


def test_valor_sc_es_irresoluble_con_consenso_sc():
    resultado = resolver_tabla7(BN, SIN_CONSENSO, PA)
    assert resultado.resultado is TipoResultado.IRRESOLUBLE
    assert resultado.consenso == SIN_CONSENSO


def test_ejecutar_cruce_normaliza_evaluadoras():
    cruce = ejecutar_cruce(1, 2024, {"Evaluadora_A": BN, "Evaluadora_B": "1", "Evaluadora_C": BN})
    assert isinstance(cruce, Cruce)
    assert cruce.id_punto == 1
    assert cruce.anio == 2024
    assert cruce.consenso is BN
    assert cruce.plausibilidad is None or isinstance(cruce.plausibilidad, Plausibilidad)


def test_ejecutar_cruce_sin_evaluadoras_es_irresoluble():
    cruce = ejecutar_cruce("P-1", 2023)
    assert cruce.resultado is TipoResultado.IRRESOLUBLE


def test_ejecutar_cruce_calcula_plausibilidad_con_consenso():
    cruce = ejecutar_cruce(1, 2024, {"Evaluadora_A": BN, "Evaluadora_B": BN, "Evaluadora_C": BN},
                           mapa_anterior=PA)
    assert cruce.consenso is BN
    assert isinstance(cruce.plausibilidad, Plausibilidad)


def test_ejecutar_cruce_sin_consenso_no_calcula_plausibilidad():
    cruce = ejecutar_cruce(1, 2024, {"Evaluadora_A": BN, "Evaluadora_B": PA, "Evaluadora_C": TC})
    assert cruce.consenso is None
    assert cruce.plausibilidad is None
