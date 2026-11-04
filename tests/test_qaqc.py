"""Pruebas para app.domain.qaqc."""
import pytest

from app.domain.classes import SIN_CONSENSO, ClaseCobertura
from app.domain.qaqc import (
    ESTADO_INICIAL,
    EstadoQAQC,
    EventoQAQC,
    eventos_disponibles,
    requiere_ficha_qaqc,
    transicion,
)
from app.services.store import Store


def test_estado_inicial():
    assert ESTADO_INICIAL is EstadoQAQC.DETECTADO


def test_transicion_valida():
    assert transicion(EstadoQAQC.DETECTADO, EventoQAQC.INICIAR_REVISION) is EstadoQAQC.EN_REVISION
    assert (
        transicion(EstadoQAQC.EN_REVISION, EventoQAQC.SOLICITAR_APROBACION)
        is EstadoQAQC.PENDIENTE_APROBACION
    )


def test_transicion_aprobacion():
    assert (
        transicion(EstadoQAQC.PENDIENTE_APROBACION, EventoQAQC.APROBAR_CONSENSO)
        is EstadoQAQC.RESUELTO_POR_CONSENSO
    )
    assert (
        transicion(EstadoQAQC.PENDIENTE_APROBACION, EventoQAQC.DECLARAR_SIN_CONSENSO)
        is EstadoQAQC.SIN_CONSENSO
    )


def test_transicion_reemplazo():
    assert (
        transicion(EstadoQAQC.REEMPLAZO_AUTORIZADO, EventoQAQC.COMPLETAR_REEMPLAZO)
        is EstadoQAQC.REEMPLAZO_COMPLETADO
    )
    assert (
        transicion(EstadoQAQC.RESERVA_EN_INTERPRETACION, EventoQAQC.COMPLETAR_REEMPLAZO)
        is EstadoQAQC.REEMPLAZO_COMPLETADO
    )


def test_transicion_invalida_es_none():
    assert transicion(EstadoQAQC.DETECTADO, EventoQAQC.APROBAR_CONSENSO) is None
    assert transicion(EstadoQAQC.REEMPLAZO_COMPLETADO, EventoQAQC.INICIAR_REVISION) is None


def test_eventos_disponibles():
    disponibles = eventos_disponibles(EstadoQAQC.DETECTADO)
    assert disponibles == [EventoQAQC.INICIAR_REVISION]


def test_eventos_disponibles_pendiente_aprobacion():
    disponibles = set(eventos_disponibles(EstadoQAQC.PENDIENTE_APROBACION))
    assert disponibles == {EventoQAQC.APROBAR_CONSENSO, EventoQAQC.DECLARAR_SIN_CONSENSO}


def test_eventos_disponibles_estado_final():
    assert eventos_disponibles(EstadoQAQC.REEMPLAZO_COMPLETADO) == []


@pytest.mark.parametrize("valores,esperado", [
    ((1, 1, 1), False),                 # unanimidad
    ((1, 1, 5), False),                 # mayoría 2/3: se consolida sin ficha
    ((1, None, 1), False),              # terna incompleta: no es desacuerdo
    ((1, 5, None), False),
    ((None, None, None), False),
    ((1, 5, 6), True),                  # discrepancia absoluta
    ((1, 1, SIN_CONSENSO), True),       # SC en terna completa
])
def test_requiere_ficha_qaqc(valores, esperado):
    assert requiere_ficha_qaqc(valores) is esperado


def test_store_no_abre_ficha_para_mayoria():
    store = Store()
    store.cargar({(1, 2020): {
        "A": ClaseCobertura.BOSQUE_NATURAL,
        "B": ClaseCobertura.BOSQUE_NATURAL,
        "C": ClaseCobertura.PASTIZALES,
    }})
    store.ejecutar_cruce(1, 2020)
    assert store.pendientes() == []


def test_store_abre_ficha_para_discrepancia():
    store = Store()
    store.cargar({(1, 2020): {
        "A": ClaseCobertura.BOSQUE_NATURAL,
        "B": ClaseCobertura.PASTIZALES,
        "C": ClaseCobertura.HUMEDALES,
    }})
    store.ejecutar_cruce(1, 2020)
    assert len(store.pendientes()) == 1
