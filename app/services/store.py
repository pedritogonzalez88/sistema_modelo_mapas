from dataclasses import dataclass

from ..domain.classes import ClaseCobertura
from ..domain.cross_engine import Cruce, TipoResultado, resolver_tabla7
from ..domain.qaqc import ESTADO_INICIAL, EstadoQAQC, EventoQAQC, eventos_disponibles, transicion
from ..domain.transition_rules import resolver_plausibilidad
from .analysis import ResultadoSeccion5, analisis_seccion5

ERROR_PUNTO_INEXISTENTE = "PUNTO_INEXISTENTE"
ERROR_SIN_FLUJO_QAQC = "SIN_FLUJO_QAQC"
ERROR_EVENTO_INVALIDO = "EVENTO_INVALIDO"
ERROR_TRANSICION_NO_PERMITIDA = "TRANSICION_NO_PERMITIDA"
ERROR_AREA_NO_CONFIGURADA = "AREA_NO_CONFIGURADA"
ERROR_PESOS_INVALIDOS = "PESOS_INVALIDOS"

_CLASES = list(ClaseCobertura)
_ESTADOS_CERRADOS = {EstadoQAQC.RESUELTO_POR_CONSENSO, EstadoQAQC.REEMPLAZO_COMPLETADO}
_CONTEOS_VACIOS = {
    TipoResultado.VALIDACION_AUTOMATICA: 0,
    TipoResultado.REQUIERE_REVISION: 0,
    TipoResultado.CONSENSO_TECNICO: 0,
    TipoResultado.IRRESOLUBLE: 0,
}


class StoreError(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


@dataclass
class MatrizConsenso:
    clases: list
    matriz: list
    pesos_observados: list
    total_registros: int


@dataclass
class ResumenDashboard:
    total_puntos: int
    total_evaluaciones: int
    en_validacion_automatica: int
    requiere_revision: int
    consenso_tecnico: int
    irresolubles: int
    pendientes_qaqc: int
    area_total_ha: float | None = None


class Store:
    def __init__(self):
        self.limpiar()

    def limpiar(self):
        self._datos = {}
        self._puntos = set()
        self._resultados = {}
        self._qaqc = {}
        self._area_total_ha = None
        self._pesos_cartografia = None
        self.total_evaluaciones = 0

    def cargar(self, datos):
        self._datos = {clave: dict(valores) for clave, valores in datos.items()}
        self._puntos = {clave[0] for clave in self._datos}
        self._resultados = {}
        self._qaqc = {}
        self.total_evaluaciones = sum(len(valores) for valores in self._datos.values())
        return self.total_evaluaciones

    @property
    def puntos(self):
        return sorted(self._puntos)

    def ejecutar_cruce(self, punto, anio, mapa_anterior=None):
        evaluadoras = self._datos.get((punto, anio))
        if evaluadoras is None:
            raise StoreError(ERROR_PUNTO_INEXISTENTE, f"No existe el cruce del punto {punto}, año {anio}.")
        if mapa_anterior is None:
            mapa_anterior = self._consenso_anterior(punto, anio)
        resultado = resolver_tabla7(evaluadoras.get("A"), evaluadoras.get("B"), evaluadoras.get("C"))
        consenso = resultado.consenso if isinstance(resultado.consenso, ClaseCobertura) else None
        plausibilidad = resolver_plausibilidad(mapa_anterior, consenso) if consenso is not None else None
        self._resultados[(punto, anio)] = resultado
        if resultado.requiere_consenso_tecnico or resultado.requiere_revision:
            self._qaqc[(punto, anio)] = ESTADO_INICIAL
        return Cruce(
            id_punto=str(punto),
            anio=anio,
            evaluadoras=evaluadoras,
            consenso=resultado.consenso,
            resultado=resultado.resultado,
            requiere_consenso_tecnico=resultado.requiere_consenso_tecnico,
            requiere_revision=resultado.requiere_revision,
            minorias=resultado.minorias,
            plausibilidad=plausibilidad,
        )

    def _consenso_anterior(self, punto, anio):
        resultado = self._resultados.get((punto, anio - 1))
        if resultado is None or not isinstance(resultado.consenso, ClaseCobertura):
            return None
        return resultado.consenso

    def pendientes(self):
        registros = []
        for (punto, anio), estado in self._qaqc.items():
            if estado in _ESTADOS_CERRADOS:
                continue
            cruce = self._resultados.get((punto, anio))
            registros.append({
                "punto": punto,
                "anio": anio,
                "estado": estado.value,
                "resultado": cruce.resultado.value if cruce else None,
                "consenso": cruce.consenso.value if cruce and isinstance(cruce.consenso, ClaseCobertura) else None,
            })
        return sorted(registros, key=lambda registro: (registro["punto"], registro["anio"]))

    def resolver_qaqc(self, punto, anio, evento):
        clave = (punto, anio)
        if clave not in self._resultados:
            raise StoreError(ERROR_PUNTO_INEXISTENTE, f"No existe el cruce del punto {punto}, año {anio}.")
        if clave not in self._qaqc:
            raise StoreError(ERROR_SIN_FLUJO_QAQC, f"El cruce del punto {punto}, año {anio} no requiere revisión QAQC.")
        try:
            evento_obj = EventoQAQC(evento)
        except ValueError:
            raise StoreError(ERROR_EVENTO_INVALIDO, f"El evento QAQC '{evento}' no es válido.") from None
        estado = self._qaqc[clave]
        nuevo = transicion(estado, evento_obj)
        if nuevo is None:
            raise StoreError(ERROR_TRANSICION_NO_PERMITIDA, f"No se permite pasar de '{estado.value}' mediante '{evento_obj.value}'.")
        self._qaqc[clave] = nuevo
        return {
            "punto": punto,
            "anio": anio,
            "estado": nuevo.value,
            "eventos_disponibles": [evento_disponible.value for evento_disponible in eventos_disponibles(nuevo)],
        }

    def matriz_consenso(self):
        pares = []
        for (punto, anio), resultado in self._resultados.items():
            if not isinstance(resultado.consenso, ClaseCobertura):
                continue
            mapa = self._datos.get((punto, anio), {}).get("A")
            if not isinstance(mapa, ClaseCobertura):
                continue
            pares.append((resultado.consenso, mapa))
        k = len(_CLASES)
        matriz = [[0] * k for _ in range(k)]
        for consenso, mapa in pares:
            matriz[_CLASES.index(consenso)][_CLASES.index(mapa)] += 1
        pesos_observados = []
        for fila in matriz:
            total = sum(fila)
            pesos_observados.append([round(valor / total, 4) for valor in fila] if total else [0.0] * k)
        return MatrizConsenso(
            clases=_CLASES,
            matriz=matriz,
            pesos_observados=pesos_observados,
            total_registros=len(pares),
        )

    def configurar_cartografia(self, area_total_ha, pesos=None):
        k = len(_CLASES)
        if area_total_ha is None or area_total_ha <= 0:
            raise StoreError(ERROR_PESOS_INVALIDOS, "El área total debe ser un número positivo.")
        if pesos is None:
            pesos = [1.0 / k] * k
        if len(pesos) != k or sum(pesos) <= 0:
            raise StoreError(ERROR_PESOS_INVALIDOS, "Debe proveerse un peso por cada clase de cobertura con suma positiva.")
        self._area_total_ha = float(area_total_ha)
        self._pesos_cartografia = [float(valor) for valor in pesos]

    def seccion5(self):
        if self._area_total_ha is None or self._pesos_cartografia is None:
            raise StoreError(ERROR_AREA_NO_CONFIGURADA, "El área total no está configurada.")
        muestra = []
        for (punto, anio), resultado in self._resultados.items():
            if not isinstance(resultado.consenso, ClaseCobertura):
                continue
            mapa = self._datos.get((punto, anio), {}).get("A")
            if not isinstance(mapa, ClaseCobertura):
                continue
            muestra.append((resultado.consenso, mapa))
        return analisis_seccion5(muestra, self._area_total_ha, self._pesos_cartografia)

    def resumen(self):
        conteo = dict(_CONTEOS_VACIOS)
        pendientes = 0
        for (punto, anio), resultado in self._resultados.items():
            conteo[resultado.resultado] = conteo.get(resultado.resultado, 0) + 1
            estado = self._qaqc.get((punto, anio))
            if estado is not None and estado not in _ESTADOS_CERRADOS:
                pendientes += 1
        return ResumenDashboard(
            total_puntos=len(self._puntos),
            total_evaluaciones=self.total_evaluaciones,
            en_validacion_automatica=conteo[TipoResultado.VALIDACION_AUTOMATICA],
            requiere_revision=conteo[TipoResultado.REQUIERE_REVISION],
            consenso_tecnico=conteo[TipoResultado.CONSENSO_TECNICO],
            irresolubles=conteo[TipoResultado.IRRESOLUBLE],
            pendientes_qaqc=pendientes,
            area_total_ha=self._area_total_ha,
        )


store = Store()