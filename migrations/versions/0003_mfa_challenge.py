"""Segundo factor TOTP: contador de intentos del challenge (Fase A3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29 21:40:00.000000

La Fase A3 no necesita tablas nuevas: ``mfa_totp``, ``recovery_codes``,
``auth_tokens`` y ``sesiones.mfa_at`` ya se crearon en 0002.

Lo único que falta es ``auth_tokens.mfa_attempts``: el challenge de segundo
factor se guarda como una fila de ``auth_tokens`` con ``purpose='mfa'`` y hacía
falta un contador para invalidarlo después de 5 intentos fallidos. La columna
es ``NOT NULL DEFAULT 0`` para que las filas existentes (tokens de otros
propósitos) queden válidas sin backfill.

El índice ``ix_auth_tokens_token_hash`` (único) ya existe desde 0002, que es el
que resuelve la búsqueda del challenge por hash; no se duplica aquí.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Agrega el contador de intentos del challenge de MFA."""
    op.add_column(
        'auth_tokens',
        sa.Column('mfa_attempts', sa.Integer(), server_default=sa.text('0'),
                  nullable=False),
    )


def downgrade() -> None:
    """Quita el contador de intentos del challenge de MFA."""
    op.drop_column('auth_tokens', 'mfa_attempts')
