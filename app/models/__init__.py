"""Modelo relacional MVP (Etapa 1 operativa).

Trazabilidad en 3 capas separadas:
  Interpretacion  = lo que interpreto cada asistente (origen: entrega versionada)
  ResultadoCruce  = lo que calculo el cruce automatico (regla versionada)
  FichaQAQC       = lo que resolvio la supervision (dictamen estructurado)

La clase consolidada nunca sobrescribe las interpretaciones originales.
"""
from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Proyecto(Base):
    __tablename__ = "proyectos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200), unique=True)
    periodo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    protocolo_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    srid: Mapped[int] = mapped_column(Integer, default=4326)
    creado_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    entregas: Mapped[list["Entrega"]] = relationship(back_populates="proyecto")
    puntos_malla: Mapped[list["PuntoMalla"]] = relationship(back_populates="proyecto")


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    # cargador | supervision | consulta | asistente
    rol: Mapped[str] = mapped_column(String(30), default="consulta")
    # Letra de la terna (A/B/C) solo si rol = asistente
    letra_terna: Mapped[str | None] = mapped_column(String(1), nullable=True)


class PuntoMalla(Base):
    """Malla oficial de muestreo. Las coordenadas de una entrega se contrastan
    contra esta tabla; nunca desplazan el punto automaticamente."""

    __tablename__ = "puntos_malla"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)  # ID_PUNTO oficial
    geom = mapped_column(Geometry("POINT", srid=4326))
    estrato: Mapped[str | None] = mapped_column(String(100), nullable=True)
    origen: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # ORIGEN Tabla 9: anio de referencia del disenio (1990|2007|2017|2024)
    origen_anio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Peso muestral del estrato (superficie relativa consolidada)
    peso: Mapped[float | None] = mapped_column(Float, nullable=True)
    es_reserva: Mapped[bool] = mapped_column(default=False)
    activo: Mapped[bool] = mapped_column(default=True)

    proyecto: Mapped["Proyecto"] = relationship(back_populates="puntos_malla")


class Entrega(Base):
    """Recepcion semanal versionada. El archivo original se conserva con su
    huella SHA-256 como evidencia, incluso si la entrega es rechazada."""

    __tablename__ = "entregas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    asistente_letra: Mapped[str] = mapped_column(String(1))  # A | B | C
    semana: Mapped[str] = mapped_column(String(20), index=True)
    # avance | acumulado | correccion
    tipo: Mapped[str] = mapped_column(String(20), default="avance")
    version: Mapped[int] = mapped_column(Integer, default=1)
    archivo_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # pendiente | aceptada | rechazada
    estado: Mapped[str] = mapped_column(String(20), default="pendiente", index=True)
    detalle_validacion = mapped_column(JSONB, default=dict)
    recibida_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cargada_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    proyecto: Mapped["Proyecto"] = relationship(back_populates="entregas")
    interpretaciones: Mapped[list["Interpretacion"]] = relationship(back_populates="entrega")


class Interpretacion(Base):
    """Una clase 1-9 (o ausencia = NULL) para proyecto+punto+anio+asistente+version.
    Formato largo: las exportaciones reconstruyen las columnas A1990-A2024."""

    __tablename__ = "interpretaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entrega_id: Mapped[int] = mapped_column(ForeignKey("entregas.id"), index=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    asistente_letra: Mapped[str] = mapped_column(String(1))
    clase: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # True cuando la celda contenia SC: estado de resolucion, no codigo de cobertura
    es_sc: Mapped[bool] = mapped_column(default=False)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)

    entrega: Mapped["Entrega"] = relationship(back_populates="interpretaciones")


class ResultadoCruce(Base):
    """Ultimo cruce calculado por punto-anio. El historial de recalculos
    queda en auditoria; las interpretaciones de origen no se tocan."""

    __tablename__ = "resultados_cruce"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    a: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    b: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    c: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # unanimidad | mayoria | discrepancia | incompleto
    resultado: Mapped[str] = mapped_column(String(30), index=True)
    clase_consolidada: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    regla_version: Mapped[str] = mapped_column(String(30), default="tabla7-v1")
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class FichaQAQC(Base):
    """Expediente de resolucion de discrepancias absolutas. El anio afectado
    es obligatorio: identifica inequivocamente que cruce se resuelve."""

    __tablename__ = "fichas_qaqc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    estado: Mapped[str] = mapped_column(String(40), default="Detectado", index=True)
    valores_iniciales = mapped_column(JSONB, default=dict)  # {A, B, C, versiones}
    evidencias = mapped_column(JSONB, default=list)
    debate: Mapped[str | None] = mapped_column(Text, nullable=True)
    # consenso | sin_consenso | null
    dictamen: Mapped[str | None] = mapped_column(String(20), nullable=True)
    clase_definitiva: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    reemplazo = mapped_column(JSONB, nullable=True)
    # Ficha del protocolo 4.12.1: sensores, causa de descarte, fecha y firmas
    sensores = mapped_column(JSONB, default=list)
    causa_descarte: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fecha_sesion: Mapped[date | None] = mapped_column(Date, nullable=True)
    firmas = mapped_column(JSONB, default=dict)
    responsable_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    accion: Mapped[str] = mapped_column(String(100))
    entidad: Mapped[str] = mapped_column(String(100))
    entidad_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detalle = mapped_column(JSONB, default=dict)
    fecha: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Reemplazo(Base):
    """Sustitucion de un punto principal por uno de reserva (4.5-4.9).

    La notacion R/A de la columna Notas implica autorizacion de la
    supervision; los puntos ad-hoc estan prohibidos: la reserva debe
    existir en la malla y pertenecer al mismo estrato (4.7)."""

    __tablename__ = "reemplazos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_origen: Mapped[int] = mapped_column(Integer, index=True)
    # Puede ser NULL en un reporte propuesto aun sin reserva asignada
    punto_reserva: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # N = nubosidad | B = bandeado | T = transicion oculta
    causa: Mapped[str | None] = mapped_column(String(1), nullable=True)
    # propuesto | autorizado | completado
    estado: Mapped[str] = mapped_column(String(20), default="propuesto", index=True)
    autorizada_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())


class ClaseMapa(Base):
    """Clase asignada por los mapas MADES (UTCUTS_FINAL_YYYY) por punto-anio.

    Filas de la matriz de confusion (5.1). Sin estos datos la Etapa 2
    no puede calcularse: esta prohibido usar una evaluadora como mapa."""

    __tablename__ = "clases_mapa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    clase: Mapped[int] = mapped_column(SmallInteger)
    fuente: Mapped[str] = mapped_column(String(100), default="UTCUTS")
    cargada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
