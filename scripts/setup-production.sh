#!/usr/bin/env bash
# Orquestador de producción: configura, valida y levanta el Validador EET.
#
# Uso:
#   bash scripts/setup-production.sh \
#       --dominio "eet.aep.gov.py" --smtp-host "smtp.aep.gov.py" \
#       --smtp-user "validador" --admin-email "admin@aep.gov.py" \
#       [--vpn-cidr "10.20.0.0/16"] [--http-only] [--skip-preflight]
#
# Usa .env.production.example como referencia de variables y genera
# .env.production con secretos aleatorios (gitignored).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PRODUCTION_ARGS=()
PREPARE_ARGS=()
SKIP_PREFLIGHT=0
HTTP_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dominio) PREPARE_ARGS+=(--dominio "$2"); shift 2 ;;
        --smtp-host) PREPARE_ARGS+=(--smtp-host "$2"); shift 2 ;;
        --smtp-port) PREPARE_ARGS+=(--smtp-port "$2"); shift 2 ;;
        --smtp-user) PREPARE_ARGS+=(--smtp-user "$2"); shift 2 ;;
        --smtp-password) PREPARE_ARGS+=(--smtp-password "$2"); shift 2 ;;
        --admin-email) PREPARE_ARGS+=(--admin-email "$2"); shift 2 ;;
        --vpn-cidr) PREPARE_ARGS+=(--vpn-cidr "$2"); shift 2 ;;
        --http-only) HTTP_ONLY=1; PREPARE_ARGS+=(--http-only); shift ;;
        --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
        --help|-h)
            sed -n '1,30p' "$0"; exit 0 ;;
        *) echo "Opcion desconocida: $1" >&2; exit 2 ;;
    esac
done

[[ "$HTTP_ONLY" == "1" ]] && PRODUCTION_ARGS+=(--http-only)

# 1. Verificar prerequisitos
for cmd in docker openssl python3; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "Falta $cmd" >&2; exit 1; }
done
docker compose version >/dev/null 2>&1 || { echo "Falta docker compose v2" >&2; exit 1; }

# 2. Generar .env.production si no existe
if [[ ! -f .env.production ]]; then
    echo "== Generando .env.production =="
    bash scripts/prepare-production.sh "${PREPARE_ARGS[@]}"
else
    echo "== .env.production ya existe; se reutiliza =="
fi

# 3. Directorios con permisos correctos
echo "== Preparando directorios =="
sudo install -d -o 10001 -g 10001 -m 0750 uploads 2>/dev/null || install -d -m 0750 uploads
if [[ "$HTTP_ONLY" != "1" ]]; then
    sudo install -d -o 10001 -g 10001 -m 0750 certs 2>/dev/null || install -d -m 0750 certs
fi

# 4. Preflight
if [[ "$SKIP_PREFLIGHT" != "1" ]]; then
    echo "== Preflight =="
    if [[ "$SKIP_PREFLIGHT" != "1" ]]; then
        if [[ -x .venv/bin/python ]]; then
            .venv/bin/python scripts/check_production_config.py || true
        fi
    fi
fi

# 5. Levantar stack + admin
echo "== Levantando stack =="
bash scripts/start-with-admin.sh --production "${PRODUCTION_ARGS[@]}"

# 6. Resumen
echo
echo "=== Produccion lista ==="
if [[ "$HTTP_ONLY" == "1" ]]; then
    echo "Modo HTTP: app en 127.0.0.1:8000, proxy en puerto 80."
    echo "Configure el frontal externo para hacer proxy a http://127.0.0.1:8000"
    echo "y enviar X-Forwarded-Proto: https."
else
    echo "Modo HTTPS: proxy en 80/443. Copie fullchain.pem y privkey.pem en certs/."
fi
echo "Admin: las credenciales se solicitaron durante el arranque."
echo "Siguientes pasos:"
echo "  1. Verifique: docker compose --env-file .env.production -f docker-compose.prod.yml ps"
echo "  2. Smoke:     .venv/bin/python scripts/smoke_http.py --base-url https://<dominio>"
echo "  3. Backups:   configure scripts/backup.ps1 con un destino externo."
echo "  4. Monitoreo: configure scripts/healthcheck-production.py + systemd."
