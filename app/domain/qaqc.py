from enum import Enum

from app.domain.classes import SIN_CONSENSO


class EstadoQAQC(str, Enum):
    DETECTADO = "Detectado"
    EN_REVISION = "En revisión"
    PENDIENTE_APROBACION = "Pendiente de aprobación"
    RESUELTO_POR_CONSENSO = "Resuelto por consenso"
    SIN_CONSENSO = "Sin consenso"
    REEMPLAZO_AUTORIZADO = "Reemplazo autorizado"
    RESERVA_EN_INTERPRETACION = "Reserva en interpretación"
    REEMPLAZO_COMPLETADO = "Reemplazo completado"


class EventoQAQC(str, Enum):
    INICIAR_REVISION = "Iniciar revisión"
    SOLICITAR_APROBACION = "Solicitar aprobación"
    APROBAR_CONSENSO = "Aprobar consenso"
    DECLARAR_SIN_CONSENSO = "Declarar sin consenso"
    AUTORIZAR_REEMPLAZO = "Autorizar reemplazo"
    RESERVAR_EN_INTERPRETACION = "Reservar en interpretación"
    COMPLETAR_REEMPLAZO = "Completar reemplazo"


ESTADO_INICIAL = EstadoQAQC.DETECTADO


def requiere_ficha_qaqc(valores) -> bool:
    """``True`` solo para discrepancia absoluta o ``SC`` en terna completa.

    El protocolo (secciones 3, 4 y 6.3) abre ficha QA/QC únicamente cuando las
    tres evaluadoras entregaron y hay tres clases distintas o un ``SC``. Una
    mayoría 2/3 se consolida automáticamente y una terna incompleta no es un
    desacuerdo: no generan expediente.
    """
    presentes = [valor for valor in valores if valor is not None]
    return (len(presentes) == 3
            and (len(set(presentes)) == 3 or SIN_CONSENSO in presentes))

OUT = {
    (EstadoQAQC.DETECTADO, EventoQAQC.INICIAR_REVISION): EstadoQAQC.EN_REVISION,
    (EstadoQAQC.EN_REVISION, EventoQAQC.SOLICITAR_APROBACION): EstadoQAQC.PENDIENTE_APROBACION,
    (EstadoQAQC.PENDIENTE_APROBACION, EventoQAQC.APROBAR_CONSENSO): EstadoQAQC.RESUELTO_POR_CONSENSO,
    (EstadoQAQC.PENDIENTE_APROBACION, EventoQAQC.DECLARAR_SIN_CONSENSO): EstadoQAQC.SIN_CONSENSO,
    (EstadoQAQC.RESUELTO_POR_CONSENSO, EventoQAQC.AUTORIZAR_REEMPLAZO): EstadoQAQC.REEMPLAZO_AUTORIZADO,
    (EstadoQAQC.SIN_CONSENSO, EventoQAQC.AUTORIZAR_REEMPLAZO): EstadoQAQC.REEMPLAZO_AUTORIZADO,
    (EstadoQAQC.SIN_CONSENSO, EventoQAQC.RESERVAR_EN_INTERPRETACION): EstadoQAQC.RESERVA_EN_INTERPRETACION,
    (EstadoQAQC.REEMPLAZO_AUTORIZADO, EventoQAQC.RESERVAR_EN_INTERPRETACION): EstadoQAQC.RESERVA_EN_INTERPRETACION,
    (EstadoQAQC.REEMPLAZO_AUTORIZADO, EventoQAQC.COMPLETAR_REEMPLAZO): EstadoQAQC.REEMPLAZO_COMPLETADO,
    (EstadoQAQC.RESERVA_EN_INTERPRETACION, EventoQAQC.COMPLETAR_REEMPLAZO): EstadoQAQC.REEMPLAZO_COMPLETADO,
    (EstadoQAQC.RESERVA_EN_INTERPRETACION, EventoQAQC.AUTORIZAR_REEMPLAZO): EstadoQAQC.REEMPLAZO_AUTORIZADO,
}


def transicion(estado, evento):
    return OUT.get((estado, evento))


def eventos_disponibles(estado):
    return [evento for (origen, evento) in OUT if origen == estado]
