"""Historial append-only de recálculos de cruce (Fase 1).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03

``resultados_cruce`` sigue siendo el estado vigente. Esta tabla conserva cada
versión calculada con su entrega de origen, regla, actor y fecha.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "resultados_cruce_historial" in set(
            sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "resultados_cruce_historial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proyecto_id", sa.Integer(), nullable=False),
        sa.Column("punto_id", sa.Integer(), nullable=False),
        sa.Column("anio", sa.Integer(), nullable=False),
        sa.Column("entrega_id", sa.Integer(), nullable=True),
        sa.Column("a", sa.SmallInteger(), nullable=True),
        sa.Column("b", sa.SmallInteger(), nullable=True),
        sa.Column("c", sa.SmallInteger(), nullable=True),
        sa.Column("resultado", sa.String(length=30), nullable=False),
        sa.Column("clase_consolidada", sa.SmallInteger(), nullable=True),
        sa.Column("regla_version", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=True),
        sa.Column("cambiado_en", sa.DateTime(),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(a IS NULL OR (a >= 1 AND a <= 9)) AND "
            "(b IS NULL OR (b >= 1 AND b <= 9)) AND "
            "(c IS NULL OR (c >= 1 AND c <= 9))",
            name="ck_resultados_cruce_historial_abc_1_9",
        ),
        sa.CheckConstraint(
            "clase_consolidada IS NULL OR "
            "(clase_consolidada >= 1 AND clase_consolidada <= 9)",
            name="ck_resultados_cruce_historial_consolidada_1_9",
        ),
        sa.ForeignKeyConstraint(["proyecto_id"], ["proyectos.id"]),
        sa.ForeignKeyConstraint(["entrega_id"], ["entregas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resultados_cruce_historial_proyecto_id",
                    "resultados_cruce_historial", ["proyecto_id"])
    op.create_index("ix_resultados_cruce_historial_punto_id",
                    "resultados_cruce_historial", ["punto_id"])
    op.create_index("ix_resultados_cruce_historial_anio",
                    "resultados_cruce_historial", ["anio"])
    op.create_index("ix_resultados_cruce_historial_entrega_id",
                    "resultados_cruce_historial", ["entrega_id"])
    op.create_index(
        "ix_resultados_cruce_historial_punto",
        "resultados_cruce_historial",
        ["proyecto_id", "punto_id", "anio", "cambiado_en"],
    )


def downgrade() -> None:
    op.drop_index("ix_resultados_cruce_historial_punto",
                  table_name="resultados_cruce_historial")
    op.drop_index("ix_resultados_cruce_historial_entrega_id",
                  table_name="resultados_cruce_historial")
    op.drop_index("ix_resultados_cruce_historial_anio",
                  table_name="resultados_cruce_historial")
    op.drop_index("ix_resultados_cruce_historial_punto_id",
                  table_name="resultados_cruce_historial")
    op.drop_index("ix_resultados_cruce_historial_proyecto_id",
                  table_name="resultados_cruce_historial")
    op.drop_table("resultados_cruce_historial")
