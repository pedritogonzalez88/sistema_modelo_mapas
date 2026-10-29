"""Pruebas para app.domain.classes."""
import pytest

from app.domain.classes import (
    ALIASES_SC,
    MAPA_ETIQUETAS,
    ClaseCobertura,
    SIN_CONSENSO,
    es_valido,
    normalizar_etiqueta,
)


def test_sin_consenso_es_sc():
    assert SIN_CONSENSO == "SC"


def test_nueve_clases_de_cobertura():
    assert len(list(ClaseCobertura)) == 9
    assert ClaseCobertura.BOSQUE_NATURAL.value == "Bosque natural"
    assert ClaseCobertura.OTRAS_TIERRAS.value == "Otras tierras"


@pytest.mark.parametrize(
    "etiqueta, esperado",
    [
        ("1", ClaseCobertura.BOSQUE_NATURAL),
        ("2", ClaseCobertura.PLANTACIONES_FORESTALES),
        ("3", ClaseCobertura.PALMAR),
        ("4", ClaseCobertura.OTRAS_TIERRAS_FORESTALES),
        ("5", ClaseCobertura.PASTIZALES),
        ("6", ClaseCobertura.TIERRAS_DE_CULTIVO),
        ("7", ClaseCobertura.HUMEDALES),
        ("8", ClaseCobertura.ASENTAMIENTOS),
        ("9", ClaseCobertura.OTRAS_TIERRAS),
    ],
)
def test_mapa_etiquetas_numericas(etiqueta, esperado):
    assert MAPA_ETIQUETAS[etiqueta] is esperado


def test_normalizar_etiqueta_none():
    assert normalizar_etiqueta(None) is None


def test_normalizar_etiqueta_instancia_passthrough():
    clase = ClaseCobertura.PALMAR
    assert normalizar_etiqueta(clase) is clase


def test_normalizar_etiqueta_sc_exacto():
    assert normalizar_etiqueta(SIN_CONSENSO) == SIN_CONSENSO


@pytest.mark.parametrize("alias", sorted(ALIASES_SC))
def test_normalizar_etiqueta_alias_sc(alias):
    assert normalizar_etiqueta(alias) == SIN_CONSENSO


def test_normalizar_etiqueta_vacia_tras_strip():
    assert normalizar_etiqueta("   ") is None
    assert normalizar_etiqueta("") is None


def test_normalizar_etiqueta_texto_conocido():
    assert normalizar_etiqueta("Bosque Natural") is ClaseCobertura.BOSQUE_NATURAL
    assert normalizar_etiqueta("tierras de cultivo") is ClaseCobertura.TIERRAS_DE_CULTIVO


def test_normalizar_etiqueta_desconocida_devuelve_crudo():
    # No está en MAPA_ETIQUETAS ni en ALIASES_SC: devuelve el texto original.
    assert normalizar_etiqueta("Zona ignota") == "Zona ignota"


def test_normalizar_etiqueta_alias_sc_case_sensitive():
    # ``normalizar_etiqueta`` no baja a minúsculas antes de comparar ALIASES_SC.
    assert normalizar_etiqueta("Sin consenso") == "Sin consenso"


def test_es_valido_none():
    assert es_valido(None) is True


def test_es_valido_clase():
    assert es_valido(ClaseCobertura.HUMEDALES) is True


def test_es_valido_sin_consenso():
    assert es_valido(SIN_CONSENSO) is True


def test_es_valido_texto_invalido():
    assert es_valido("algo") is False
