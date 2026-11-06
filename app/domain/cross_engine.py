from dataclasses import dataclass, field
from enum import Enum

from app.domain.classes import SIN_CONSENSO, ClaseCobertura, normalizar_etiqueta
from app.domain.transition_rules import Plausibilidad, resolver_plausibilidad


class TipoResultado(str, Enum):
    VALIDACION_AUTOMATICA = "Validación automática"
    REQUIERE_REVISION = "Requiere revisión"
    CONSENSO_TECNICO = "Consenso técnico"
    IRRESOLUBLE = "Irresoluble"


@dataclass
class ResultadoTabla7:
    consenso: ClaseCobertura | str | None
    resultado: TipoResultado
    requiere_consenso_tecnico: bool
    requiere_revision: bool
    minorias: list = field(default_factory=list)


@dataclass
class Cruce:
    id_punto: str
    anio: int
    evaluadoras: dict
    consenso: ClaseCobertura | str | None
    resultado: TipoResultado
    requiere_consenso_tecnico: bool
    requiere_revision: bool
    minorias: list
    plausibilidad: Plausibilidad | None


def resolver_tabla7(a: ClaseCobertura | str | None,
                    b: ClaseCobertura | str | None,
                    c: ClaseCobertura | str | None) -> ResultadoTabla7:
    claves = [a, b, c]
    if any(v is None or v == SIN_CONSENSO for v in claves):
        return ResultadoTabla7(
            consenso=SIN_CONSENSO if SIN_CONSENSO in claves else None,
            resultado=TipoResultado.IRRESOLUBLE,
            requiere_consenso_tecnico=True,
            requiere_revision=True,
        )
    valores: dict[ClaseCobertura | str | None, int] = {}
    for v in claves:
        valores[v] = valores.get(v, 0) + 1
    if len(valores) == 1:
        return ResultadoTabla7(
            consenso=next(iter(valores)),
            resultado=TipoResultado.VALIDACION_AUTOMATICA,
            requiere_consenso_tecnico=False,
            requiere_revision=False,
        )
    if len(valores) == 2:
        consenso = max(valores, key=lambda clave: valores[clave])
        minorias = []
        for v in claves:
            if v != consenso and v not in minorias:
                minorias.append(v)
        return ResultadoTabla7(
            consenso=consenso,
            resultado=TipoResultado.REQUIERE_REVISION,
            requiere_consenso_tecnico=False,
            requiere_revision=True,
            minorias=minorias,
        )
    return ResultadoTabla7(
        consenso=None,
        resultado=TipoResultado.CONSENSO_TECNICO,
        requiere_consenso_tecnico=True,
        requiere_revision=True,
    )


def _extraer_evaluadora(d, letra):
    for k, v in d.items():
        k_str = str(k).strip().upper()
        if k_str in {letra, f"EVALUADORA_{letra}", f"EVALUADOR_{letra}", f"ASISTENTE_{letra}"}:
            return v
    return d.get(f"Evaluadora_{letra}") or d.get(f"Evaluador_{letra}") or d.get(letra)


def ejecutar_cruce(id_punto, anio, evaluadoras=None, mapa_anterior=None):
    if evaluadoras is None:
        evaluadoras = {"Evaluadora_A": None, "Evaluadora_B": None, "Evaluadora_C": None}
    normalizadas = {k: normalizar_etiqueta(v) for k, v in evaluadoras.items()}
    resultado = resolver_tabla7(
        _extraer_evaluadora(normalizadas, "A"),
        _extraer_evaluadora(normalizadas, "B"),
        _extraer_evaluadora(normalizadas, "C"),
    )
    plausibilidad = None
    if isinstance(resultado.consenso, ClaseCobertura):
        plausibilidad = resolver_plausibilidad(mapa_anterior, resultado.consenso)
    return Cruce(
        id_punto=id_punto,
        anio=anio,
        evaluadoras=normalizadas,
        consenso=resultado.consenso,
        resultado=resultado.resultado,
        requiere_consenso_tecnico=resultado.requiere_consenso_tecnico,
        requiere_revision=resultado.requiere_revision,
        minorias=resultado.minorias,
        plausibilidad=plausibilidad,
    )