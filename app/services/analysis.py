"""Estimadores de exactitud tematica (disenio de analisis, seccion 5).

Sigue a Olofsson et al. (2014) y al protocolo 5.1-5.5:
  filas i    = clases del mapa MADES (estratos del muestreo),
  columnas j = clases de referencia de la terna ("verdad de terreno"),
  W_i        = proporcion de superficie del estrato i en el mapa.

  muestra: lista de pares (mapa, consenso) como ClaseCobertura.
"""
from dataclasses import dataclass

from ..domain.classes import ClaseCobertura

_CLASES = list(ClaseCobertura)


@dataclass
class ResultadoSeccion5:
    clases: list
    ponderadores: list
    exactitud_usuario: list
    exactitud_productor: list
    exactitud_total: float
    kappa: float
    areas_estimadas: list
    intervalos_confianza: list
    nota: str = ""


def _indice_clase(clase):
    return _CLASES.index(clase)


def _redondear(valor):
    return round(valor, 4)


def analisis_seccion5(muestra, area_total_ha, pesos):
    k = len(_CLASES)
    if pesos is None or len(pesos) != k or sum(pesos) <= 0:
        pesos = [1.0 / k] * k
        nota = "Ponderadores no válidos; se usó una distribución uniforme."
    else:
        pesos = [float(valor) for valor in pesos]
        nota = ""
    # Matriz de confusion muestral: filas = mapa, columnas = referencia.
    matriz = [[0] * k for _ in range(k)]
    for mapa, consenso in muestra:
        if consenso is None or mapa is None:
            continue
        matriz[_indice_clase(mapa)][_indice_clase(consenso)] += 1
    filas = [sum(fila) for fila in matriz]
    columnas = [sum(matriz[i][j] for i in range(k)) for j in range(k)]
    # Matriz de error ponderada: p_ij = W_i * n_ij / n_i (proporciones de area).
    proporcion = [[0.0] * k for _ in range(k)]
    for i in range(k):
        if filas[i] > 0:
            proporcion[i] = [matriz[i][j] / filas[i] for j in range(k)]
    p_col = [sum(pesos[i] * proporcion[i][j] for i in range(k)) for j in range(k)]
    p_fila = [sum(proporcion[i][j] * pesos[i] for j in range(k)) for i in range(k)]
    varianza = []
    for j in range(k):
        valor = 0.0
        for i in range(k):
            if filas[i] > 1:
                p = proporcion[i][j]
                valor += pesos[i] ** 2 * p * (1 - p) / (filas[i] - 1)
        varianza.append(valor)
    areas = [area_total_ha * probabilidad for probabilidad in p_col]
    intervalos = []
    for j in range(k):
        semidesviacion = 1.96 * varianza[j] ** 0.5
        minimo = max(0.0, area_total_ha * (p_col[j] - semidesviacion))
        maximo = min(area_total_ha, area_total_ha * (p_col[j] + semidesviacion))
        intervalos.append((minimo, maximo))
    # 5.3: exactitud global = diagonal de la matriz ponderada.
    exactitud_total = sum(pesos[j] * proporcion[j][j] for j in range(k))
    # Usuario (mapa i): p_ii / total de la fila; productor (referencia j):
    # p_jj / total de la columna.
    exactitud_usuario = []
    for i in range(k):
        if p_fila[i] > 0:
            exactitud_usuario.append(pesos[i] * proporcion[i][i] / p_fila[i])
        else:
            exactitud_usuario.append(0.0)
    exactitud_productor = []
    for j in range(k):
        if p_col[j] > 0:
            exactitud_productor.append(pesos[j] * proporcion[j][j] / p_col[j])
        else:
            exactitud_productor.append(0.0)
    # Kappa sobre conteos muestrales (acuerdo observado vs. esperado por azar).
    total = sum(filas)
    if total > 0:
        po = sum(matriz[j][j] for j in range(k)) / total
        pe = sum(filas[i] * columnas[i] for i in range(k)) / total ** 2
        kappa = (po - pe) / (1 - pe) if pe < 1 else 0.0
    else:
        kappa = 0.0
    return ResultadoSeccion5(
        clases=_CLASES,
        ponderadores=[_redondear(valor) for valor in pesos],
        exactitud_usuario=[_redondear(valor) for valor in exactitud_usuario],
        exactitud_productor=[_redondear(valor) for valor in exactitud_productor],
        exactitud_total=_redondear(exactitud_total),
        kappa=_redondear(kappa),
        areas_estimadas=[_redondear(valor) for valor in areas],
        intervalos_confianza=[(_redondear(minimo), _redondear(maximo)) for minimo, maximo in intervalos],
        nota=nota,
    )
