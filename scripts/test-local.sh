#!/usr/bin/env bash
# Pruebas locales del Validador EET dentro de WSL 2 + Docker.
#
# Uso desde Windows:
#   powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1
#
# Uso directo dentro de WSL:
#   bash scripts/test-local.sh /mnt/c/Users/<usuario>/PycharmProjects/sistema_modelo_mapas
#
# Argumentos:
#   1) ruta del repositorio fuente (por defecto: directorio actual)
#   2) destino en WSL (por defecto: /opt/sistema_modelo_mapas)
#   3) saltar build (1/0)
#   4) saltar integración (1/0)
#   5) saltar sincronización (1/0)
set -euo pipefail

SRC="${1:-$(pwd)}"
DEST="${2:-/opt/sistema_modelo_mapas}"
SKIP_BUILD="${3:-0}"
SKIP_INTEGRATION="${4:-0}"
SKIP_SYNC="${5:-0}"

log() { printf '\n== %s ==\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail "Ejecute como root dentro de WSL: wsl -d Ubuntu -u root"
command -v docker >/dev/null 2>&1 || fail "Docker no está instalado dentro de WSL."
command -v tar >/dev/null 2>&1 || fail "Falta tar."
command -v curl >/dev/null 2>&1 || fail "Falta curl."
command -v openssl >/dev/null 2>&1 || fail "Falta openssl."

if [[ "$SKIP_SYNC" != "1" ]]; then
    log "Sincronizando $SRC -> $DEST"
    mkdir -p "$DEST"
    # No se borra el destino: se preservan .venv, uploads y deploy/.env.local.
    tar -C "$SRC" \
        --exclude=.git \
        --exclude=.venv \
        --exclude=node_modules \
        --exclude=.idea \
        --exclude=__pycache__ \
        --exclude='*.pyc' \
        --exclude=.pytest_cache \
        --exclude=.ruff_cache \
        --exclude=uploads \
        --exclude=docs/qa-visual \
        -cf - . | tar -C "$DEST" -xf -
else
    log "Sincronización omitida (-SkipSync)"
fi
mkdir -p "$DEST/uploads"
cd "$DEST"

if ! docker info >/dev/null 2>&1; then
    log "Iniciando Docker"
    systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true
    for _ in $(seq 1 30); do
        docker info >/dev/null 2>&1 && break
        sleep 1
    done
fi
docker info >/dev/null 2>&1 || fail "El daemon de Docker no responde."

if [[ ! -f deploy/.env.local ]]; then
    log "Generando deploy/.env.local"
    SECRET=$(openssl rand -base64 48 | tr -d '\n')
    PEPPER=$(openssl rand -base64 32 | tr -d '\n')
    cat > deploy/.env.local <<EOF
EET_AUTH_ENABLED=true
EET_ENVIRONMENT=dev
EET_COOKIE_SECURE=false
EET_SECRET_KEY=$SECRET
EET_AUTH_PEPPER=$PEPPER
EET_EMAIL_BACKEND=file
EET_EMAIL_DIR=/app/uploads/mail
EET_PUBLIC_BASE_URL=http://localhost:8000
EOF
    chmod 600 deploy/.env.local
fi

# Credenciales demo solo para el smoke local. Si no existen se generan y se
# guardan en deploy/.env.local (gitignored); nunca se versionan.
if ! grep -q '^EET_DEMO_ADMIN_PASSWORD=' deploy/.env.local 2>/dev/null; then
    ADMIN_PASS=$(openssl rand -base64 24 | tr -d '\n/+=' | cut -c1-16)
    echo "EET_DEMO_ADMIN_PASSWORD=$ADMIN_PASS" >> deploy/.env.local
fi
if ! grep -q '^EET_DEMO_CONSULTA_PASSWORD=' deploy/.env.local 2>/dev/null; then
    CONSULTA_PASS=$(openssl rand -base64 24 | tr -d '\n/+=' | cut -c1-16)
    echo "EET_DEMO_CONSULTA_PASSWORD=$CONSULTA_PASS" >> deploy/.env.local
fi

# El repositorio puede venir de Windows con CRLF; un \r al final de la
# contraseña demo haría fallar el login de forma silenciosa.
sed -i 's/\r$//' deploy/.env.local

set -a
# shellcheck disable=SC1091
source deploy/.env.local
set +a

log "Levantando el stack"
BUILD_ARG=""
[[ "$SKIP_BUILD" != "1" ]] && BUILD_ARG="--build"
docker compose up -d $BUILD_ARG --wait --wait-timeout 240
docker compose ps

log "Preparando entorno de pruebas"
export PATH="/root/.local/bin:${PATH}"
command -v uv >/dev/null 2>&1 || fail "uv no está instalado (https://astral.sh/uv)."
uv sync --frozen --dev

log "Sembrando datos demo"
export EET_DATABASE_URL="postgresql+psycopg://eet:eet@localhost:5432/eet"
.venv/bin/python scripts/seed_demo_data.py

log "Pruebas sin integración"
EET_ENVIRONMENT=dev EET_AUTH_ENABLED=false \
    .venv/bin/pytest -q -p no:warnings

if [[ "$SKIP_INTEGRATION" != "1" ]]; then
    log "Pruebas de integración (PostgreSQL/PostGIS)"
    EET_ENVIRONMENT=dev EET_AUTH_ENABLED=false \
        .venv/bin/pytest -m integration -o addopts= -q -p no:warnings
fi

log "Smoke HTTP"
curl -fsS http://localhost:8000/salud >/dev/null \
    || fail "/salud no responde 200."

curl -fsS http://localhost:8000/ready >/dev/null \
    || fail "/ready no responde 200."

AUTH_ENABLED=$(grep -E '^EET_AUTH_ENABLED=' deploy/.env.local \
    | head -1 | cut -d= -f2 | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
if [[ "$AUTH_ENABLED" == "true" ]]; then
    [[ -n "${EET_DEMO_ADMIN_PASSWORD:-}" ]] \
        || fail "Falta EET_DEMO_ADMIN_PASSWORD para el smoke de login."
    [[ -n "${EET_DEMO_CONSULTA_PASSWORD:-}" ]] \
        || fail "Falta EET_DEMO_CONSULTA_PASSWORD para el smoke de login."
    .venv/bin/python scripts/smoke_http.py \
        --base-url http://localhost:8000 \
        --admin-user demo.admin \
        --admin-password "$EET_DEMO_ADMIN_PASSWORD" \
        --consulta-user demo.consulta \
        --consulta-password "$EET_DEMO_CONSULTA_PASSWORD"
else
    curl -fsS http://localhost:8000/ >/dev/null \
        || fail "La portada no responde (auth desactivada)."
    echo "Autenticación desactivada: se omite el smoke de login."
fi

log "RESUMEN"
echo "App:          http://localhost:8000"
echo "Contenedores:"
docker compose ps
if [[ "$AUTH_ENABLED" == "true" ]]; then
    echo "Usuarios demo: demo.admin / demo.consulta"
    echo "Credenciales en deploy/.env.local (EET_DEMO_*_PASSWORD)."
fi
echo "Todo OK."
