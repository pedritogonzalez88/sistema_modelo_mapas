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
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && \
    rm -rf /root/.cache/uv

COPY app ./app
# CSS compilado en la etapa 1 (app.css está en .dockerignore)
COPY --from=css /build/app/static/css/app.css ./app/static/css/app.css

RUN mkdir -p uploads

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "--frozen", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
