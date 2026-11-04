#!/usr/bin/env bash
# Backup de PostgreSQL/PostGIS + uploads, con cifrado opcional y verificación.
#
# Uso dentro de WSL/Linux:
#   bash scripts/backup-local.sh --dest /mnt/backup/eet \
#       --key-file /etc/eet/backup.key --verify
#
# --key-file cifra los archivos con AES-256 (PBKDF2) usando esa clave.
# El destino debe estar fuera del volumen de datos (NAS, disco externo, etc.).
set -euo pipefail

DEST=""
RETENTION_DAYS=30
VERIFY=0
REPO="/opt/sistema_modelo_mapas"
KEY_FILE=""
CONTAINER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest) DEST="${2:-}"; shift 2 ;;
        --retention-days) RETENTION_DAYS="${2:-30}"; shift 2 ;;
        --repo) REPO="${2:-$REPO}"; shift 2 ;;
        --key-file) KEY_FILE="${2:-}"; shift 2 ;;
        --container) CONTAINER="${2:-}"; shift 2 ;;
        --verify) VERIFY=1; shift ;;
        *) echo "Argumento desconocido: $1" >&2; exit 2 ;;
    esac
done

[[ -n "$DEST" ]] || { echo "Falta --dest (NAS o disco externo)." >&2; exit 2; }
[[ -d "$REPO" ]] || { echo "No existe el repositorio: $REPO" >&2; exit 2; }
if [[ -n "$KEY_FILE" && ! -f "$KEY_FILE" ]]; then
    echo "No existe la clave de cifrado: $KEY_FILE" >&2
    exit 2
fi

# Detectar el contenedor de base: producción usa eet-db-prod; desarrollo
# eet-mapa-aep. Se puede forzar con --container.
if [[ -z "$CONTAINER" ]]; then
    for candidato in eet-db-prod eet-mapa-aep; do
        if docker inspect -f '{{.State.Running}}' "$candidato" 2>/dev/null | grep -q true; then
            CONTAINER="$candidato"
            break
        fi
    done
fi
[[ -n "$CONTAINER" ]] || { echo "No hay contenedor de PostgreSQL corriendo." >&2; exit 1; }

mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
DUMP_PLAIN="$TMP/eet.dump"
UPLOADS_PLAIN="$TMP/uploads.tgz"

echo "== Dump de PostgreSQL ($CONTAINER) =="
docker exec "$CONTAINER" pg_dump -U eet -d eet -Fc -f /tmp/eet_backup.dump
docker cp "$CONTAINER:/tmp/eet_backup.dump" "$DUMP_PLAIN"
docker exec "$CONTAINER" rm -f /tmp/eet_backup.dump

echo "== Archivos de uploads =="
mkdir -p "$REPO/uploads"
tar -czf "$UPLOADS_PLAIN" -C "$REPO" uploads

echo "== Verificación del dump =="
docker cp "$DUMP_PLAIN" "$CONTAINER:/tmp/eet_verify.dump"
docker exec "$CONTAINER" pg_restore --list /tmp/eet_verify.dump >/dev/null

if [[ "$VERIFY" == "1" ]]; then
    echo "== Restauración en base temporal =="
    docker exec "$CONTAINER" psql -U eet -d postgres -c \
        "DROP DATABASE IF EXISTS eet_restore_test;" >/dev/null
    docker exec "$CONTAINER" psql -U eet -d postgres -c \
        "CREATE DATABASE eet_restore_test;" >/dev/null
    docker exec "$CONTAINER" psql -U eet -d eet_restore_test -c \
        "CREATE EXTENSION IF NOT EXISTS postgis;" >/dev/null
    docker exec "$CONTAINER" pg_restore -U eet -d eet_restore_test \
        --no-owner --no-privileges --exit-on-error /tmp/eet_verify.dump >/dev/null
    USUARIOS=$(docker exec "$CONTAINER" psql -U eet -d eet_restore_test \
        -Atc "select count(*) from usuarios;")
    CRUCES=$(docker exec "$CONTAINER" psql -U eet -d eet_restore_test \
        -Atc "select count(*) from resultados_cruce;")
    docker exec "$CONTAINER" psql -U eet -d postgres -c \
        "DROP DATABASE eet_restore_test;" >/dev/null
    echo "Restauración OK: usuarios=$USUARIOS, cruces=$CRUCES"
fi
docker exec "$CONTAINER" rm -f /tmp/eet_verify.dump

if [[ -n "$KEY_FILE" ]]; then
    echo "== Cifrado AES-256 =="
    FINAL_DUMP="$DEST/eet_$STAMP.dump.enc"
    FINAL_UPLOADS="$DEST/uploads_$STAMP.tgz.enc"
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
        -pass "file:$KEY_FILE" -in "$DUMP_PLAIN" -out "$FINAL_DUMP"
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
        -pass "file:$KEY_FILE" -in "$UPLOADS_PLAIN" -out "$FINAL_UPLOADS"

    # Verifica que ambos archivos se descifran y que el dump coincide.
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -pass "file:$KEY_FILE" -in "$FINAL_DUMP" -out "$TMP/check.dump"
    cmp "$DUMP_PLAIN" "$TMP/check.dump"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -pass "file:$KEY_FILE" -in "$FINAL_UPLOADS" -out "$TMP/check.tgz"
    tar -tzf "$TMP/check.tgz" >/dev/null
else
    echo "ADVERTENCIA: backup sin cifrado (use --key-file en producción)."
    FINAL_DUMP="$DEST/eet_$STAMP.dump"
    FINAL_UPLOADS="$DEST/uploads_$STAMP.tgz"
    mv "$DUMP_PLAIN" "$FINAL_DUMP"
    mv "$UPLOADS_PLAIN" "$FINAL_UPLOADS"
fi

echo "== Retención (${RETENTION_DAYS} días) =="
find "$DEST" -maxdepth 1 -type f \
    \( -name 'eet_*.dump' -o -name 'eet_*.dump.enc' \) \
    -mtime +"$RETENTION_DAYS" -delete
find "$DEST" -maxdepth 1 -type f \
    \( -name 'uploads_*.tgz' -o -name 'uploads_*.tgz.enc' \) \
    -mtime +"$RETENTION_DAYS" -delete

echo "Backup OK:"
echo "  $FINAL_DUMP ($(du -h "$FINAL_DUMP" | cut -f1))"
echo "  $FINAL_UPLOADS ($(du -h "$FINAL_UPLOADS" | cut -f1))"
