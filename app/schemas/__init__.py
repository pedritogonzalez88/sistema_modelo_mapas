"""Esquemas Pydantic de entrada/salida HTTP."""
from datetime import datetime

from pydantic import BaseModel, Field


class EntregaCreate(BaseModel):
    asistente_letra: str = Field(pattern="^[ABC]$")
    semana: str = Field(min_length=1, max_length=20)
    tipo: str = Field(default="avance", pattern="^(avance|acumulado|correccion)$")


class EntregaOut(BaseModel):
    id: int
    asistente_letra: str
    semana: str
    tipo: str
    version: int
    estado: str
    recibida_en: datetime | None = None

    model_config = {"from_attributes": True}


class CruceOut(BaseModel):
    punto_id: int
    anio: int
    a: int | None
    b: int | None
    c: int | None
    resultado: str
    clase_consolidada: int | None
    ficha_id: int | None = None


class FichaUpdate(BaseModel):
    evidencias: list | None = None
    debate: str | None = None
    dictamen: str | None = Field(default=None, pattern="^(consenso|sin_consenso)$")
    clase_definitiva: int | None = Field(default=None, ge=1, le=9)
    reemplazo: dict | None = None


class QaqcEvento(BaseModel):
    evento: str


class DashboardOut(BaseModel):
    total_puntos: int
    total_evaluaciones: int
    unanimidad: int
    mayoria: int
    discrepancia: int
    incompleto: int
    pct_unanimidad: float
    pct_mayoria: float
    pct_discrepancia: float
    pendientes_qaqc: int
    entregas: list[dict] = []
