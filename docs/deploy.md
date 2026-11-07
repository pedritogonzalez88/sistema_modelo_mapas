# Staging con TLS y autenticación activa (A6.12)

Entorno reproducible para ensayar el despliegue real **sin tocar el entorno de
desarrollo**: `EET_AUTH_ENABLED=true`, cookies `__Host-`, HSTS y proxy de
confianza, todo sobre HTTPS.

| Pieza | Qué hace |
|---|---|
| `db` (`postgis/postgis:16-3.4`) | Base del ensayo. Puerto **5434** en el host y volumen propio (`eet-staging_eet-staging-pgdata`). |
| `app` (build del `Dockerfile` real) | Sin puertos publicados: solo la alcanza el proxy por `app:8000`. Aplica `alembic upgrade head` al arrancar. |
| `proxy` (`nginx:1.27-alpine`) | Termina TLS, redirige 80→443 y reenvía `X-Forwarded-Proto/For/Host`. IP fija **172.28.0.10** (la de `EET_TRUSTED_PROXY_IPS`). Publica **8080** (HTTP) y **8443** (HTTPS). |

Red propia `172.28.0.0/24`. El proxy tiene IP estática para que la lista de
proxies de confianza sea un CIDR estable: sin ella la aplicación no sabría que
la petición llegó por HTTPS (no habría HSTS) y todos los clientes compartirían
IP en auditoría.

---

## Requisitos

- Docker con Compose v2.
- `uv` para los scripts (certificado, preflight y smoke).
- Puertos 8080, 8443 y 5434 libres (el desarrollo usa 8000 y 5432).

---

## Puesta en marcha

Todos los comandos se ejecutan **desde la raíz del repositorio**.

### 1. Certificados locales

```powershell
uv run python scripts/gen-dev-cert.py
```

Genera en `certs/` una CA local (`ca.crt`), el certificado del servidor
con SAN `localhost` + `127.0.0.1` (`server.crt`, con la cadena) y la clave
(`server.key`). Es idempotente: si son válidos no los reescribe (`--force` para
regenerar). **No se versionan** (`.gitignore` y `.dockerignore`).

Para que el navegador los acepte sin avisos, importe `certs/ca.crt` como
autoridad raíz en el equipo de ensayo. `httpx` y `curl -k` no lo necesitan.

### 2. Variables de entorno

```powershell
Copy-Item .env.staging.example .env.staging
```

Complete los dos secretos (nunca los commitee):

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # EET_SECRET_KEY
uv run python -c "import secrets; print(secrets.token_urlsafe(24))"   # EET_AUTH_PEPPER
```

### 3. Levantar

```powershell
docker compose -f docker-compose.staging.yml --env-file .env.staging up -d --build
docker compose -f docker-compose.staging.yml ps
```

Espere a que `eet-db-staging` y `eet-app-staging` estén `healthy` (el arranque
aplica las migraciones antes de Uvicorn).

### 4. Primer administrador

```powershell
$env:EET_ADMIN_PASSWORD = '<clave inicial fuerte>'
docker compose -f docker-compose.staging.yml exec -e EET_ADMIN_PASSWORD=$env:EET_ADMIN_PASSWORD app `
  uv run --no-sync --frozen python -m app.cli crear-admin `
  --email coordinacion@aep.gov.py --username staging-admin --nombre "Admin staging"
Remove-Item Env:EET_ADMIN_PASSWORD
```

El administrador nace con política `totp` y sin enrolar: el primer login crea la
sesión y redirige a `/auth/seguridad` para enrolar el autenticador. Eso es lo que
usa el smoke para comprobar la cookie `__Host-eet_session`.

### 5. Verificar

```powershell
# Configuración + base + servicio (desde el host)
uv run python scripts/preflight-auth.py `
  --env-file .env.staging `
  --database-url postgresql+psycopg://eet:eet@localhost:5434/eet `
  --url https://localhost:8443 --insecure

# Camino completo del operador (HTTP→HTTPS, cabeceras, cookie, logout)
$env:EET_SMOKE_USER = 'staging-admin'
$env:EET_SMOKE_PASSWORD = '<la misma clave>'
uv run python scripts/smoke-staging.py --base https://localhost:8443 --insecure
```

Ambos salen con 0 si todo está en verde y 1 si algo falla. El preflight acepta
`--json` para automatización.

### 6. Bajar y limpiar

```powershell
# Detiene y elimina contenedores, red y volumen del staging (no toca dev)
docker compose -f docker-compose.staging.yml down -v
```

El volumen de desarrollo (`eet-pgdata`) y el contenedor `eet-app` no se tocan:
son otro proyecto de Compose (`eet-staging` ≠ directorio raíz).

---

## Comandos de operación dentro del contenedor

```powershell
docker compose -f docker-compose.staging.yml exec app `
  uv run --no-sync --frozen python -m app.cli estado-auth

docker compose -f docker-compose.staging.yml exec app `
  uv run --no-sync --frozen python -m app.cli reenviar-correo --limit 20

docker compose -f docker-compose.staging.yml exec app `
  uv run --no-sync --frozen python -m app.cli re-cifrar
```

Detalle de cada uno en `docs/runbook-autenticacion.md` §9 y §15.

---

## Qué **no** valida este entorno

- **SMTP real**: el backend es `console`; el preflight lo marca como aviso. Los
  envíos, SPF/DKIM/DMARC y la entrega a Gmail/Outlook requieren el relay
  institucional (pendiente de TI).
- **Certificado público y DNS**: el certificado es local; el dominio real y su
  certificado los emite TI.
- **CIDR definitivos**: aquí se usa `EET_ADMIN_INTERNAL_ONLY=false` para poder
  administrar desde el host. En producción va en `true` con los rangos internos
  reales.
- **WAF de borde** y balanceo: fuera del alcance de un ensayo de aplicación.

---

## Problemas frecuentes

| Síntoma | Causa |
|---|---|
| El navegador avisa del certificado | Falta importar `certs/ca.crt` como CA raíz |
| HSTS ausente en la respuesta | `EET_TRUSTED_PROXY_IPS` no coincide con la IP del proxy (`172.28.0.10/32`) o el proxy no envía `X-Forwarded-Proto` |
| `502 Bad Gateway` en el proxy | `app` todavía arrancando (migraciones) o sin `healthy` |
| Puerto ocupado al levantar | 8080/8443/5434 en uso; ciérrelo o cambie el mapeo |
| `preflight` falla en `base_datos` | Falta `--database-url …@localhost:5434/eet` (desde el host el servicio `db` no se resuelve) |
| El login devuelve 200 con error | Usuario o clave incorrectos, o la cuenta quedó bloqueada por intentos |
