"""Motor PostgreSQL + PostGIS y sesión por petición.

Desde la Fase A0 el esquema se versiona con **Alembic** (``alembic upgrade
head``). ``init_db()`` queda para desarrollo local y pruebas, donde no se
quiere depender de la cadena de migraciones; en producción el arranque del
contenedor aplica las migraciones antes de levantar Uvicorn.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columnas agregadas tras la creación inicial (MVP sin Alembic).
# Se conserva solo por compatibilidad con bases de desarrollo ya creadas; las
# migraciones nuevas se escriben en ``migrations/versions`` (Alembic).
_MIGRACIONES = [
    ("puntos_malla", "origen_anio", "INTEGER"),
    ("puntos_malla", "peso", "DOUBLE PRECISION"),
    ("fichas_qaqc", "sensores", "JSONB DEFAULT '[]'"),
    ("fichas_qaqc", "causa_descarte", "VARCHAR(60)"),
    ("fichas_qaqc", "fecha_sesion", "DATE"),
    ("fichas_qaqc", "firmas", "JSONB DEFAULT '{}'"),
    ("interpretaciones", "es_sc", "BOOLEAN DEFAULT FALSE"),
]


def init_db() -> None:
    """Crea la extensión PostGIS, las tablas y las columnas faltantes.

    Uso exclusivo de desarrollo y pruebas (bases efímeras). En producción el
    esquema lo administra Alembic: ``alembic upgrade head`` en el arranque del
    contenedor. No se elimina todavía porque los tests y el primer arranque
    local dependen de él.
    """
    from app import models  # noqa: F401  (registra las tablas en Base.metadata)

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for tabla, columna, tipo in _MIGRACIONES:
            conn.execute(text(
                f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {columna} {tipo}"))
