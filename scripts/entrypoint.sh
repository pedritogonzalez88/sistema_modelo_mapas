#!/usr/bin/env sh
# Punto de entrada de la imagen de producción.
#
# Aplica las migraciones (idempotente) y, si se definieron las variables
# EET_SUPER_ADMIN_*, siembra el administrador inicial. Luego arranca Uvicorn.
set -e

cd /app

uv run --no-sync --frozen alembic upgrade head

if [ -n "${EET_SUPER_ADMIN_USERNAME:-}" ] && [ -n "${EET_SUPER_ADMIN_PASSWORD:-}" ]; then
    uv run --no-sync --frozen python scripts/seed_super_admin.py
fi

exec uv run --no-sync --frozen uvicorn app.main:app --host 0.0.0.0 --port 8000
