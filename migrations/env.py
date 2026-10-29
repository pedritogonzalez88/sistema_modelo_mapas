"""Entorno de Alembic para PostgreSQL/PostGIS.

Decisiones:

- La URL sale de ``settings.database_url`` (prefijo ``EET_``), no de
  ``alembic.ini``: una sola fuente de verdad para la conexión.
- ``target_metadata`` es ``Base.metadata`` de la aplicación.
- Se excluyen las tablas y esquemas que administra PostGIS (``spatial_ref_sys``,
  ``tiger.*``, ``topology.*``, ``raster_*``) para que el autogenerate no intente
  borrarlas. Solo se compara el esquema ``public``.
- ``compare_type=False``: GeoAlchemy2 devuelve ``geometry(POINT,4326)`` como
  ``Geometry`` y la comparación de tipos produce ruido sin valor.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Una sola fuente de verdad para la conexión (el '%' se escapa para ConfigParser).
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata

# Tablas internas de PostGIS y de Alembic que nunca deben tocarse.
TABLAS_EXCLUIDAS = frozenset({
    "spatial_ref_sys",
    "geometry_columns",
    "geography_columns",
    "raster_columns",
    "raster_overviews",
    "alembic_version",
    "layer",
    "topology",
})
ESQUEMAS_PROPIOS = frozenset({"public"})


def include_name(name, type_, parent_names):
    """Filtra qué objetos refleja Alembic de la base de datos."""
    if type_ == "schema":
        # ``None`` es el esquema por defecto; cualquier otro (tiger, topology)
        # queda fuera de la comparación.
        return name is None or name in ESQUEMAS_PROPIOS
    if type_ == "table":
        esquema = (parent_names or {}).get("schema_name")
        if esquema not in (None, *ESQUEMAS_PROPIOS):
            return False
        if name in TABLAS_EXCLUIDAS or name.startswith("raster_"):
            return False
    return True


def include_object(obj, name, type_, reflected, compare_to):
    """Descarta objetos gestionados por PostGIS o por el propio Alembic."""
    if type_ == "table":
        if name in TABLAS_EXCLUIDAS or (name or "").startswith("raster_"):
            return False
        esquema = getattr(obj, "schema", None)
        if esquema not in (None, *ESQUEMAS_PROPIOS):
            return False
    if type_ == "index" and reflected:
        tabla = getattr(obj, "table", None)
        if tabla is not None and tabla.name in TABLAS_EXCLUIDAS:
            return False
    return True


def _configurar_contexto(**extra) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_name=include_name,
        include_object=include_object,
        include_schemas=False,
        compare_type=False,
        **extra,
    )


def run_migrations_offline() -> None:
    """Modo sin conexión: emite el SQL sin ejecutarlo."""
    _configurar_contexto(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo normal: aplica las migraciones sobre la base configurada."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # El contenedor PostGIS deja ``topology`` y ``tiger`` en el search_path
        # del rol: sin fijarlo, el autogenerate refleja esas tablas como si
        # fueran nuestras y propone borrarlas. Se cierra la transacción que abre
        # el propio SET para que Alembic administre la suya y confirme al final.
        connection.exec_driver_sql("SET search_path TO public")
        connection.commit()
        _configurar_contexto(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
