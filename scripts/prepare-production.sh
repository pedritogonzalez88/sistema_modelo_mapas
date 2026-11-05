#!/usr/bin/env bash
# Genera deploy/.env.production con secretos aleatorios y datos de producción.
# No imprime secretos. El archivo queda gitignored y con permisos 600.
#
# Uso:
#   bash scripts/prepare-production.sh \
#       --dominio "eet.aep.gov.py" \
#       --smtp-host "smtp.aep.gov.py" \
#       --smtp-user "validador" \
#       --admin-email "admin@aep.gov.py" \
#       --vpn-cidr "10.20.0.0/16"
#
# El password SMTP se solicita de forma interactiva si no se pasa
# --smtp-password. Evite pasarlo por argumento en servidores compartidos:
# quedaría visible en el historial/procesos.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO="$REPO/deploy/.env.production"
CERTS="$REPO/deploy/certs"
UPLOADS="$REPO/uploads"

DOMINIO=""
SMTP_HOST=""
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASSWORD=""
ADMIN_EMAIL=""
EMAIL_FROM=""
VPN_CIDR=""
DB_HOST="db"
DB_USER="eet"
DB_NAME="eet"
TRUSTED_PROXY_IPS="172.30.0.10"
ADMIN_INTERNAL_ONLY="true"
FORCE=0

uso() {
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Opciones:
  --dominio DOMINIO          Dominio público, sin https:// (obligatorio)
  --smtp-host HOST           Servidor SMTP (obligatorio)
  --smtp-port PUERTO         Puerto SMTP (por defecto 587)
  --smtp-user USUARIO        Usuario SMTP (obligatorio)
  --smtp-password PASSWORD   Password SMTP; si falta se solicita
  --admin-email EMAIL        Correo del administrador inicial (obligatorio)
  --email-from TEXTO         Remitente; por defecto Validador EET <no-reply@dominio>
  --vpn-cidr CIDR            CIDR adicional para EET_INTERNAL_CIDRS
  --db-host HOST             Host de PostgreSQL (por defecto db)
  --db-user USUARIO          Usuario de PostgreSQL (por defecto eet)
  --db-name NOMBRE           Base de datos (por defecto eet)
  --trusted-proxy-ips IPS    Proxy de confianza (por defecto 172.30.0.10)
  --public-admin             EET_ADMIN_INTERNAL_ONLY=false (Easypanel/Internet)
  --force                    Reemplaza deploy/.env.production si existe
  --help                     Muestra esta ayuda
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dominio) DOMINIO="${2:-}"; shift 2 ;;
        --smtp-host) SMTP_HOST="${2:-}"; shift 2 ;;
        --smtp-port) SMTP_PORT="${2:-}"; shift 2 ;;
        --smtp-user) SMTP_USER="${2:-}"; shift 2 ;;
        --smtp-password) SMTP_PASSWORD="${2:-}"; shift 2 ;;
        --admin-email) ADMIN_EMAIL="${2:-}"; shift 2 ;;
        --email-from) EMAIL_FROM="${2:-}"; shift 2 ;;
        --vpn-cidr) VPN_CIDR="${2:-}"; shift 2 ;;
        --db-host) DB_HOST="${2:-}"; shift 2 ;;
        --db-user) DB_USER="${2:-}"; shift 2 ;;
        --db-name) DB_NAME="${2:-}"; shift 2 ;;
        --trusted-proxy-ips) TRUSTED_PROXY_IPS="${2:-}"; shift 2 ;;
        --public-admin) ADMIN_INTERNAL_ONLY="false"; shift ;;
        --force) FORCE=1; shift ;;
        --help|-h) uso; exit 0 ;;
        *) die "Argumento desconocido: $1 (use --help)" ;;
    esac
done

[[ -n "$DOMINIO" ]] || die "Falta --dominio."
[[ -n "$SMTP_HOST" ]] || die "Falta --smtp-host."
[[ -n "$SMTP_USER" ]] || die "Falta --smtp-user."
[[ -n "$ADMIN_EMAIL" ]] || die "Falta --admin-email."
command -v openssl >/dev/null 2>&1 || die "Se requiere openssl."
[[ "$SMTP_PORT" =~ ^[0-9]+$ ]] || die "SMTP_PORT inválido."
[[ "$DB_USER" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "DB_USER inválido."
[[ "$DB_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "DB_NAME inválido."

if [[ -e "$DESTINO" && "$FORCE" != "1" ]]; then
    die "Ya existe $DESTINO. Use --force para reemplazarlo."
fi

if [[ -z "$SMTP_PASSWORD" ]]; then
    if [[ -t 0 ]]; then
        read -rsp "Password SMTP para $SMTP_USER: " SMTP_PASSWORD
        printf '\n'
    else
        die "Falta --smtp-password y no hay terminal para solicitarlo."
    fi
fi
[[ -n "$SMTP_PASSWORD" ]] || die "El password SMTP es obligatorio."

# Normaliza el dominio: quita esquema, barra final y espacios.
DOMINIO="${DOMINIO#http://}"
DOMINIO="${DOMINIO#https://}"
DOMINIO="${DOMINIO%/}"
DOMINIO="$(printf '%s' "$DOMINIO" | tr -d '[:space:]')"
[[ -n "$DOMINIO" ]] || die "El dominio no puede quedar vacío."

SECRET_KEY="$(openssl rand -hex 48)"
AUTH_PEPPER="$(openssl rand -hex 32)"
DB_PASSWORD="$(openssl rand -hex 24)"

if [[ -z "$EMAIL_FROM" ]]; then
    EMAIL_FROM="Validador EET <no-reply@$DOMINIO>"
fi

CIDRS="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"
if [[ -n "$VPN_CIDR" ]]; then
    CIDRS="$CIDRS,$(printf '%s' "$VPN_CIDR" | tr -d '[:space:]')"
fi

env_quote() {
    local valor="$1"
    valor="${valor//\\/\\\\}"
    valor="${valor//\"/\\\"}"
    printf '"%s"' "$valor"
}

mkdir -p "$CERTS" "$UPLOADS"
umask 077
TMP="$(mktemp "${DESTINO}.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

{
    printf '# Generado por scripts/prepare-production.sh — NO versionar.\n'
    printf 'EET_ENVIRONMENT=prod\n'
    printf 'EET_AUTH_ENABLED=true\n'
    printf 'EET_COOKIE_SECURE=true\n'
    printf 'EET_SESSION_COOKIE=__Host-eet_session\n'
    printf 'EET_PUBLIC_BASE_URL=https://%s\n' "$DOMINIO"
    printf 'EET_SECRET_KEY=%s\n' "$SECRET_KEY"
    printf 'EET_AUTH_PEPPER=%s\n' "$AUTH_PEPPER"
    printf 'EET_FERNET_KEYS=\n'
    printf 'POSTGRES_USER=%s\n' "$DB_USER"
    printf 'POSTGRES_DB=%s\n' "$DB_NAME"
    printf 'POSTGRES_PASSWORD=%s\n' "$DB_PASSWORD"
    printf 'EET_DATABASE_URL=postgresql+psycopg://%s:%s@%s:5432/%s\n' \
        "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_NAME"
    printf 'EET_EMAIL_BACKEND=smtp\n'
    printf 'EET_SMTP_HOST=%s\n' "$SMTP_HOST"
    printf 'EET_SMTP_PORT=%s\n' "$SMTP_PORT"
    printf 'EET_SMTP_STARTTLS=true\n'
    printf 'EET_SMTP_USER=%s\n' "$SMTP_USER"
    printf 'EET_SMTP_PASSWORD=%s\n' "$(env_quote "$SMTP_PASSWORD")"
    printf 'EET_EMAIL_FROM=%s\n' "$(env_quote "$EMAIL_FROM")"
    printf 'EET_ADMIN_INTERNAL_ONLY=%s\n' "$ADMIN_INTERNAL_ONLY"
    printf 'EET_INTERNAL_CIDRS=%s\n' "$CIDRS"
    printf 'EET_TRUSTED_PROXY_IPS=%s\n' "$TRUSTED_PROXY_IPS"
    printf 'EET_EXPOSE_DOCS=false\n'
    printf 'EET_MAX_UPLOAD_MB=20\n'
    printf 'EET_LDAP_ENABLED=false\n'
} > "$TMP"

chmod 600 "$TMP"
mv -f "$TMP" "$DESTINO"
trap - EXIT

PERMISOS="$(stat -c '%a' "$DESTINO" 2>/dev/null || printf '?')"
if [[ "$PERMISOS" != "600" ]]; then
    printf 'ADVERTENCIA: permisos %s en %s (se esperaba 600).\n' \
        "$PERMISOS" "$DESTINO" >&2
    printf 'En WSL con el repo en /mnt/c habilite metadata o use una ruta ext4.\n' >&2
fi

if [[ "$(id -u)" -eq 0 ]]; then
    chown -R 10001:10001 "$UPLOADS" 2>/dev/null || true
    chmod 750 "$UPLOADS"
    PROPIETARIO_UPLOADS="uploads: propietario 10001:10001"
else
    PROPIETARIO_UPLOADS="Ejecute: sudo install -d -o 10001 -g 10001 -m 0750 $UPLOADS"
fi

printf 'Configuración creada: %s\n' "$DESTINO"
printf 'Correo administrador para el bootstrap: %s\n' "$ADMIN_EMAIL"
printf '\nSiguientes pasos:\n'
printf '  0. %s\n' "$PROPIETARIO_UPLOADS"
printf '  1. Copie fullchain.pem y privkey.pem en deploy/certs/.\n'
printf '  2. Valide: docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml config --quiet\n'
printf '  3. Levante: docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build\n'
printf '  4. Cree el admin:\n'
printf '     docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml \\\n'
printf '       run --rm --no-deps app .venv/bin/python scripts/bootstrap_admin.py\n'
