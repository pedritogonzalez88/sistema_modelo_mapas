"""Restricciones de integridad y rangos (Fase 0).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03

Agrega las claves naturales y los rangos 1–9 que el dominio ya asumía. Antes de
aplicarla, ejecute `scripts/audit_duplicates.py` contra la base de destino:

- Si no hay duplicados ni valores fuera de rango, la migración aplica directo.
- Si hay, haga backup, consolide los datos y use una ventana de mantenimiento.

La migración falla de forma explícita si la base no está limpia; no borra ni
reescribe datos.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Agrega unicidad de claves naturales y CHECK de clases 1–9."""
    op.create_unique_constraint(
        "uq_interpretaciones_entrega_punto_anio_letra",
        "interpretaciones",
        ["entrega_id", "punto_id", "anio", "asistente_letra"],
    )
    op.create_unique_constraint(
        "uq_resultados_cruce_proyecto_punto_anio",
        "resultados_cruce",
        ["proyecto_id", "punto_id", "anio"],
    )
    op.create_unique_constraint(
        "uq_puntos_malla_proyecto_punto",
        "puntos_malla",
        ["proyecto_id", "punto_id"],
    )
    op.create_unique_constraint(
        "uq_clases_mapa_proyecto_punto_anio",
        "clases_mapa",
        ["proyecto_id", "punto_id", "anio"],
    )
    op.create_unique_constraint(
        "uq_fichas_qaqc_proyecto_punto_anio",
        "fichas_qaqc",
        ["proyecto_id", "punto_id", "anio"],
    )
    op.create_unique_constraint(
        "uq_reemplazos_proyecto_origen_reserva",
        "reemplazos",
        ["proyecto_id", "punto_origen", "punto_reserva"],
    )
    op.create_unique_constraint(
        "uq_entregas_proyecto_letra_semana_version",
        "entregas",
        ["proyecto_id", "asistente_letra", "semana", "version"],
    )

    op.create_check_constraint(
        "ck_interpretaciones_clase_1_9",
        "interpretaciones",
        "clase IS NULL OR (clase >= 1 AND clase <= 9)",
    )
    op.create_check_constraint(
        "ck_resultados_cruce_abc_1_9",
        "resultados_cruce",
        "(a IS NULL OR (a >= 1 AND a <= 9)) AND "
        "(b IS NULL OR (b >= 1 AND b <= 9)) AND "
        "(c IS NULL OR (c >= 1 AND c <= 9))",
    )
    op.create_check_constraint(
        "ck_resultados_cruce_consolidada_1_9",
        "resultados_cruce",
        "clase_consolidada IS NULL OR "
        "(clase_consolidada >= 1 AND clase_consolidada <= 9)",
    )
    op.create_check_constraint(
        "ck_fichas_qaqc_clase_1_9",
        "fichas_qaqc",
        "clase_definitiva IS NULL OR "
        "(clase_definitiva >= 1 AND clase_definitiva <= 9)",
    )
    op.create_check_constraint(
        "ck_clases_mapa_clase_1_9",
        "clases_mapa",
        "clase >= 1 AND clase <= 9",
    )


def downgrade() -> None:
    """Quita las restricciones agregadas."""
    op.drop_constraint("ck_clases_mapa_clase_1_9", "clases_mapa",
                       type_="check")
    op.drop_constraint("ck_fichas_qaqc_clase_1_9", "fichas_qaqc",
                       type_="check")
    op.drop_constraint("ck_resultados_cruce_consolidada_1_9",
                       "resultados_cruce", type_="check")
    op.drop_constraint("ck_resultados_cruce_abc_1_9", "resultados_cruce",
                       type_="check")
    op.drop_constraint("ck_interpretaciones_clase_1_9", "interpretaciones",
                       type_="check")

    op.drop_constraint("uq_entregas_proyecto_letra_semana_version",
                       "entregas", type_="unique")
    op.drop_constraint("uq_reemplazos_proyecto_origen_reserva",
                       "reemplazos", type_="unique")
    op.drop_constraint("uq_fichas_qaqc_proyecto_punto_anio", "fichas_qaqc",
                       type_="unique")
    op.drop_constraint("uq_clases_mapa_proyecto_punto_anio", "clases_mapa",
                       type_="unique")
    op.drop_constraint("uq_puntos_malla_proyecto_punto", "puntos_malla",
                       type_="unique")
    op.drop_constraint("uq_resultados_cruce_proyecto_punto_anio",
                       "resultados_cruce", type_="unique")
    op.drop_constraint("uq_interpretaciones_entrega_punto_anio_letra",
                       "interpretaciones", type_="unique")
