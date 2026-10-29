"""Pruebas para app.services.analysis."""
import pytest

from app.domain.classes import ClaseCobertura
from app.services.analysis import ResultadoSeccion5, analisis_seccion5

BN = ClaseCobertura.BOSQUE_NATURAL
PA = ClaseCobertura.PASTIZALES


def test_resultado_con_muestra_perfecta():
    # Protocolo 5.3: exactitud global = suma de la diagonal de la matriz
    # ponderada. Con dos estratos perfectos (BN y PA, peso 0.5 c/u) da 1.0.
    muestra = [(BN, BN)] * 10 + [(PA, PA)] * 10
    pesos = [0.5, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0]
    resultado = analisis_seccion5(muestra, area_total_ha=1000.0, pesos=pesos)
    assert isinstance(resultado, ResultadoSeccion5)
    assert resultado.exactitud_total == pytest.approx(1.0, abs=1e-3)
    assert resultado.areas_estimadas[list(ClaseCobertura).index(BN)] == pytest.approx(500.0)
    assert resultado.areas_estimadas[list(ClaseCobertura).index(PA)] == pytest.approx(500.0)


def test_pesos_invalidos_usan_uniforme():
    resultado = analisis_seccion5([(BN, BN)], area_total_ha=100.0, pesos=[1.0])
    assert "uniforme" in resultado.nota.lower()


def test_pesos_validos_nota_vacia():
    pesos = [1.0 / 9] * 9
    resultado = analisis_seccion5([(BN, BN)], area_total_ha=900.0, pesos=pesos)
    assert resultado.nota == ""


def test_intervalos_dentro_de_limites():
    muestra = [(BN, BN)] * 5 + [(PA, PA)] * 5
    resultado = analisis_seccion5(muestra, area_total_ha=500.0, pesos=[1.0 / 9] * 9)
    for bajo, alto in resultado.intervalos_confianza:
        assert 0.0 <= bajo <= alto <= 500.0


def test_kappa_muestra_perfecta():
    # Kappa = 1.0 exige al menos dos estratos con peso (una sola clase
    # es un caso degenerado: el acuerdo esperado por azar no se define).
    muestra = [(BN, BN)] * 10 + [(PA, PA)] * 10
    pesos = [0.5, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0]
    resultado = analisis_seccion5(muestra, area_total_ha=100.0, pesos=pesos)
    assert resultado.kappa == pytest.approx(1.0, abs=1e-3)
