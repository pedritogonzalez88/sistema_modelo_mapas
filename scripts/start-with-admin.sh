#!/usr/bin/env bash
# Levanta el Validador EET y garantiza un usuario administrador.
#
# Uso:
#   # Entorno local (dev)
#   bash scripts/start-with-admin.sh
#
#   # Produccion (requiere .env.production)
#   bash scripts/start-with-admin.sh --production
#
# Las credenciales del administrador se pueden pasar por variables de entorno
# (EET_SUPER_ADMIN_*) o se solicitan de forma interactiva.
set -euo pipefail

PRODUCTION=0
ADMIN_USER="${EET_SUPER_ADMIN_USERNAME:-}"
ADMIN_NOMBRE="${EET_SUPER_ADMIN_NOMBRE:-}"
ADMIN_EMAIL="${EET_SUPER_ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${EET_SUPER_ADMIN_PASSWORD:-}"
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --production) PRODUCTION=1; shift ;;
        --admin-user) ADMIN_USER="$2"; shift 2 ;;
        --admin-nombre) ADMIN_NOMBRE="$2"; shift 2 ;;
        --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
        --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=1; shift ;;
        *) echo "Opcion desconocida: $1" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$PRODUCTION" == "1" ]]; then
    ENV_FILE="$ROOT/.env.production"
    COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
    if [[ ! -f "$ENV_FILE" ]]; then
        echo ".env.production no existe; se va a generar."
        read -r -p "Dominio publico (sin https://): " DOMINIO
        read -r -p "Servidor SMTP: " SMTP_HOST
        read -r -p "Usuario SMTP: " SMTP_USER
        read -r -p "Correo del administrador inicial: " ADMIN_EMAIL
        read -r -p "CIDR VPN/LAN administrativa (opcional): " VPN_CIDR
        PREPARE_ARGS=(--dominio "$DOMINIO" --smtp-host "$SMTP_HOST" --smtp-user "$SMTP_USER" --admin-email "$ADMIN_EMAIL")
        [[ -n "$VPN_CIDR" ]] && PREPARE_ARGS+=(--vpn-cidr "$VPN_CIDR")
        bash "$ROOT/scripts/prepare-production.sh" "${PREPARE_ARGS[@]}"
    fi

    # Garantizar POSTGRES_PASSWORD (algunos .env manuales no lo incluyen o lo dejan vacio).
    if ! grep -qE '^POSTGRES_PASSWORD=[^[:space:]]+' "$ENV_FILE"; then
        # Eliminar lineas vacias/invalidas previas para no duplicar.
        sed -i '/^POSTGRES_PASSWORD=/d' "$ENV_FILE"
        DB_PASSWORD="$(python3 -c "
import secrets,string
lower=string.ascii_lowercase; upper=string.ascii_uppercase; digits=string.digits; special='!#$%&*()-_=+'
chars=[secrets.choice(lower),secrets.choice(upper),secrets.choice(digits),secrets.choice(special)]
pool=lower+upper+digits+special
chars += [secrets.choice(pool) for _ in range(28)]
import random; random.SystemRandom().shuffle(chars)
print(''.join(chars[:32]))
")"
        printf 'POSTGRES_PASSWORD=%s\n' "$DB_PASSWORD" >> "$ENV_FILE"
        # Sincronizar EET_DATABASE_URL si esta vacia o usa placeholder.
        if ! grep -qE '^EET_DATABASE_URL=postgresql' "$ENV_FILE"; then
            sed -i '/^EET_DATABASE_URL=/d' "$ENV_FILE"
            DB_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
            DB_NAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
            [[ -n "$DB_USER" ]] || DB_USER="eet"
            [[ -n "$DB_NAME" ]] || DB_NAME="eet"
            printf 'EET_DATABASE_URL=postgresql+psycopg://%s:%s@db:5432/%s\n'                 "$DB_USER" "$DB_PASSWORD" "$DB_NAME" >> "$ENV_FILE"
        fi
        echo "POSTGRES_PASSWORD generado y agregado a $ENV_FILE"
    fi
    echo "Verificacion: $(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | sed 's/=.*/=<set>/' )"
else
    ENV_FILE="$ROOT/.env.local"
    COMPOSE_FILE="$ROOT/docker-compose.yml"
fi

[[ -f "$ENV_FILE" ]] || { echo "No existe $ENV_FILE" >&2; exit 1; }
[[ -f "$COMPOSE_FILE" ]] || { echo "No existe $COMPOSE_FILE" >&2; exit 1; }

BUILD_ARG=()
[[ "$SKIP_BUILD" != "1" ]] && BUILD_ARG=(--build)

echo "Levantando stack ($ENV_FILE)..."
# Limpiar redes/contenedores previos con subredes incompatibles (evita
# "no configured subnet contains IP address").
docker rm -f eet-app-prod eet-db-prod eet-proxy-prod >/dev/null 2>&1 || true
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
docker network rm eet-prod_internal eet-prod_edge >/dev/null 2>&1 || true

# Reintento: en servidores con DNS inestable la descarga de imagenes puede fallar.
INTENTO=0
until docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d "${BUILD_ARG[@]}" --wait --wait-timeout 240; do
    INTENTO=$((INTENTO + 1))
    if [[ "$INTENTO" -ge 3 ]]; then
        echo "ERROR: no se pudo levantar el stack tras $INTENTO intentos." >&2
        echo "Verifique DNS (cat /etc/resolv.conf) y conectividad a registry-1.docker.io." >&2
        exit 1
    fi
    echo "Intento $INTENTO fallido; reintentando en 10s..." >&2
    sleep 10
done

# Solicitar credenciales si faltan.
if [[ -z "$ADMIN_USER" ]]; then read -r -p "Usuario administrador: " ADMIN_USER; fi
if [[ -z "$ADMIN_NOMBRE" ]]; then read -r -p "Nombre completo: " ADMIN_NOMBRE; fi
if [[ -z "$ADMIN_EMAIL" ]]; then read -r -p "Correo institucional: " ADMIN_EMAIL; fi
if [[ -z "$ADMIN_PASSWORD" ]]; then
    read -r -s -p "Contrasena inicial: " ADMIN_PASSWORD
    echo
fi

[[ -n "$ADMIN_USER" && -n "$ADMIN_NOMBRE" && -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]] \
    || { echo "Faltan datos del administrador." >&2; exit 1; }

if [[ "$PRODUCTION" == "1" ]]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T \
        -e "EET_SUPER_ADMIN_USERNAME=$ADMIN_USER" \
        -e "EET_SUPER_ADMIN_NOMBRE=$ADMIN_NOMBRE" \
        -e "EET_SUPER_ADMIN_EMAIL=$ADMIN_EMAIL" \
        -e "EET_SUPER_ADMIN_PASSWORD=$ADMIN_PASSWORD" \
        app .venv/bin/python scripts/seed_super_admin.py
else
    EET_SUPER_ADMIN_USERNAME="$ADMIN_USER" \
    EET_SUPER_ADMIN_NOMBRE="$ADMIN_NOMBRE" \
    EET_SUPER_ADMIN_EMAIL="$ADMIN_EMAIL" \
    EET_SUPER_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    EET_DATABASE_URL="postgresql+psycopg://eet:eet@localhost:5432/eet" \
    EET_ENVIRONMENT="dev" \
        python scripts/seed_super_admin.py
fi

if [[ "$PRODUCTION" == "1" ]]; then
    SCHEME="https"
    HOST="$(grep -E '^EET_PUBLIC_BASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed -E 's#^https?://##; s#/$##')"
    PORT=""
else
    SCHEME="http"
    HOST="localhost"
    PORT=":8000"
fi

echo
echo "Sistema levantado: ${SCHEME}://${HOST}${PORT}"
echo "Usuario administrador: $ADMIN_USER"
echo "El primer login exigira cambiar la contrasena y enrolar TOTP."
