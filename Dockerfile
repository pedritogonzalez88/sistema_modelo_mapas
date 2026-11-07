# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Etapa 1: compilación del CSS (Tailwind v4) — no se necesita Node en runtime
# ---------------------------------------------------------------------------
FROM node:22-alpine AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY app/static/css/input.css ./app/static/css/input.css
COPY app/templates ./app/templates
COPY app/static/js ./app/static/js
RUN npm run css:build

# ---------------------------------------------------------------------------
# Etapa 2: aplicación Python
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && \
    rm -rf /root/.cache/uv

COPY app ./app
# Esquema versionado: el arranque aplica las migraciones antes de Uvicorn.
COPY alembic.ini ./
COPY migrations ./migrations
# Utilidades de operación: preflight, bootstrap de administrador, smoke.
COPY scripts ./scripts
# CSS compilado en la etapa 1 (app.css está en .dockerignore)
COPY --from=css /build/app/static/css/app.css ./app/static/css/app.css

RUN mkdir -p uploads \
    && addgroup --system --gid 10001 eet \
    && adduser --system --uid 10001 --ingroup eet --home /app eet \
    && chown -R eet:eet /app

# La aplicación corre sin privilegios. El volumen de uploads debe pertenecer a
# 10001:10001 en el host (ver docs/deploy-production.md).
USER 10001:10001

EXPOSE 8000

# Easypanel y otros PaaS pueden usar el healthcheck de la imagen.
HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
    CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5)"

# ``alembic upgrade head`` es idempotente. Se ejecuta con el mismo entorno que
# Uvicorn (``uv run --no-sync --frozen``) para no resolver dependencias en
# caliente; si falla, el contenedor no arranca (fail fast).
CMD ["sh", "-c", "uv run --no-sync --frozen alembic upgrade head && exec uv run --no-sync --frozen uvicorn app.main:app --host 0.0.0.0 --port 8000"]
