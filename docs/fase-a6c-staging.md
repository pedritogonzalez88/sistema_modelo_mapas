# Fase A6c — Ensayo de despliegue en staging (A6.12)

> Cierra el último pendiente del plan: **A6.12 — ensayo de despliegue con
> `auth_enabled=true` en staging**, con TLS real, reverse proxy, cookies
> `__Host-` y proxy de confianza.
>
> Infraestructura: [`deploy/`](../deploy/README.md) ·
> Guía operativa: [`docs/despliegue-autenticacion.md`](despliegue-autenticacion.md) §12 ·
> Runbook: [`docs/runbook-autenticacion.md`](runbook-autenticacion.md) §15 y §16.

Estado previo: fases A0–A6b completas, 212 unitarias + 266 integración en verde,
Alembic `0003 (head)`, `EET_AUTH_ENABLED=false` por defecto y la app de
desarrollo en `:8000`.

---

## 1. Alcance entregado

| Entregable | Archivo |
|---|---|
| Compose de staging (db + app + proxy, red `172.28.0.0/24`, proxy en `172.28.0.10`) | `deploy/docker-compose.staging.yml` |
| Nginx: 80→301, TLS 1.2/1.3, `client_max_body_size 20m`, `X-Forwarded-*`, `server_tokens off` | `deploy/nginx.conf` |
| Variables del ensayo con placeholders y comentarios | `deploy/.env.staging.example` |
| CA + certificado local con SAN `localhost`/`127.0.0.1` (idempotente) | `scripts/gen-dev-cert.py` |
| Preflight de configuración + base + servicio (legible y `--json`) | `scripts/preflight-auth.py` |
| Smoke HTTPS del camino completo, incluida la cookie `__Host-` | `scripts/smoke-staging.py` |
| Comandos de operación | `app/cli.py` (`reenviar-correo`, `re-cifrar`, `estado-auth`) |
| Fechas del mailer sin `utcnow` | `app/auth/mailer.py` |
| Pruebas | `tests/test_cli_operacion.py` (12), `tests/test_mailer_fechas.py` (3) |
| Exclusiones de secretos | `.gitignore`, `.dockerignore` (`deploy/`) |

---

## 2. Arquitectura del staging

```
host :8080 ──┐
             ├─► proxy (nginx:1.27-alpine, 172.28.0.10) ──► app:8000 (sin puerto host)
host :8443 ──┘        TLS 1.2/1.3, 80→301                      │
                                                               ├─ EET_TRUSTED_PROXY_IPS=172.28.0.10/32
                                                               ├─ EET_AUTH_ENABLED=true
                                                               ├─ EET_COOKIE_SECURE=true
                                                               └─ __Host-eet_session
host :5434 ──────► db (postgis/postgis:16-3.4) ◄──────────────┘
                   volumen eet-staging_eet-staging-pgdata
```

Decisiones:

1. **La aplicación no publica puertos.** El único camino de entrada es el
   proxy, igual que en producción; el ensayo no puede “aprobar” mientras la app
   siga expuesta directamente.
2. **Proxy con IP fija (`172.28.0.10`).** `EET_TRUSTED_PROXY_IPS=172.28.0.10/32`
   deja de ser un CIDR “de suerte”: sin esto, `X-Forwarded-Proto` se ignora, no
   se emite HSTS y la auditoría registraría la IP del proxy para todos.
3. **Misma imagen que producción** (`build: context: ..`): el ensayo valida el
   `Dockerfile` real, incluido `alembic upgrade head` en el arranque.
4. **Puertos y volumen propios** (5434, `eet-staging_*`): el ensayo no roza el
   volumen de desarrollo. Los labels de Compose confirman el aislamiento:
   `com.docker.compose.project = eet-staging` frente al proyecto del repo.
5. **HSTS y CSP no se duplican en Nginx.** Los emite la aplicación; duplicarlos
   produce dos valores en la misma cabecera y algunos navegadores descartan la
   política. Nginx solo aporta TLS, redirección, límite de cuerpo y cabeceras de
   proxy.
6. **Certificado local de CA propia** (no autofirmado suelto): el navegador
   puede confiar en `ca.crt` y el ensayo se acerca a un certificado real.
   `server.crt` incluye la cadena.

---

## 3. Comandos ejecutados y resultados

Todos desde la raíz del repositorio. El secreto del administrador se generó en
la misma sesión de shell y no se guardó en ningún archivo.

### 3.1 Certificado y entorno

```powershell
uv run python scripts/gen-dev-cert.py
# Certificados generados en …\deploy\certs (válidos 365 días):
#   ca.crt 1277 B · server.crt 2648 B · server.key 1704 B
#   SAN: DNS:localhost, IP:127.0.0.1

# deploy/.env.staging a partir del example + secretos aleatorios
#   EET_SECRET_KEY = 64 caracteres · EET_AUTH_PEPPER = 32 caracteres
```

### 3.2 Levantar

```powershell
docker compose -f deploy/docker-compose.staging.yml --env-file deploy/.env.staging up -d --build
```

```text
eet-db-staging     postgis/postgis:16-3.4   Up (healthy)   127.0.0.1:5434->5432
eet-app-staging    eet-staging-app          Up (healthy)   8000/tcp
eet-proxy-staging  nginx:1.27-alpine        Up             8080->80, 8443->443
```

El log del arranque muestra las tres migraciones y **solo** dos advertencias de
`staging` (esperadas): `EET_EMAIL_BACKEND` en `console` y
`EET_ADMIN_INTERNAL_ONLY=false`.

### 3.3 Administradores

```powershell
docker compose -f deploy/docker-compose.staging.yml exec -e EET_ADMIN_PASSWORD=$pw app `
  uv run --no-sync --frozen python -m app.cli crear-admin `
  --email coordinacion@aep.gov.py --username staging-admin --nombre "Admin staging"
# Administrador creado: id=9 usuario=staging-admin
```

Se creó además `staging-totp` y se completó su enrolamiento por HTTPS (login →
`/auth/seguridad` → clave manual → TOTP → confirmar), para que el preflight
tenga **un administrador con TOTP confirmado**:

```text
username       | mfa_policy | enrolado
staging-admin  | totp       | f
staging-totp   | totp       | t
```

`staging-admin` queda sin enrolar a propósito: su login crea la sesión y
redirige a `/auth/seguridad`, que es justo lo que necesita el smoke para
comprobar la cookie de sesión.

### 3.4 Preflight

```powershell
uv run python scripts/preflight-auth.py `
  --env-file deploy/.env.staging `
  --database-url postgresql+psycopg://eet:eet@localhost:5434/eet `
  --url https://localhost:8443 --insecure
```

```text
Preflight de autenticación — 2026-10-30 14:02 UTC
Entorno: staging · auth_enabled=true · base de datos: postgresql+psycopg://eet:***@localhost:5434/eet

[PASS] auth_enabled            EET_AUTH_ENABLED=true
[PASS] environment             EET_ENVIRONMENT=staging
[PASS] cookie_secure           EET_COOKIE_SECURE=true
[PASS] session_cookie          EET_SESSION_COOKIE=__Host-eet_session
[PASS] public_base_url         EET_PUBLIC_BASE_URL=https://localhost:8443
[PASS] expose_docs             EET_EXPOSE_DOCS=false
[PASS] secret_key              EET_SECRET_KEY: 64 caracteres (mínimo 32)
[PASS] auth_pepper             EET_AUTH_PEPPER: 32 caracteres (mínimo 16)
[WARN] email_backend           EET_EMAIL_BACKEND=console: no entrega correo real (aceptable solo fuera de prod)
[PASS] trusted_proxy_ips       CIDR de confianza: 172.28.0.10/32
[PASS] internal_cidrs          CIDR internos: 172.28.0.0/24, 127.0.0.1/32
[WARN] admin_internal_only     EET_ADMIN_INTERNAL_ONLY=false: administración abierta fuera de la red interna
[PASS] fernet_keys             claves disponibles: v1
[PASS] base_datos              conexión y consulta simple OK
[PASS] alembic                 revisión 0003 == head
[PASS] admin_activo            2 administrador(es) activo(s)
[PASS] admin_totp              1 administrador(es) con TOTP confirmado
[PASS] usuarios_ldap           0 cuenta(s) LDAP · directorio apagado
[PASS] http_salud              GET /salud -> 200
[PASS] http_hsts               Strict-Transport-Security: max-age=31536000; includeSubDomains
[PASS] http_csp                CSP con nonce por petición
[PASS] http_redireccion_login  GET / -> 303 /auth/login?next=%2F

Resumen: 20 PASS · 2 WARN · 0 FAIL        (exit 0)
```

Los dos `WARN` son **deliberados en staging** y se documentan en §5: el backend
de correo es `console` y la administración se abre desde el host para poder
operar el ensayo. En `prod` ambos son `FAIL` (el backend, por la validación de
`Settings`; la red, porque el runbook exige `true`).

### 3.5 Smoke

```powershell
$env:EET_SMOKE_USER='staging-admin'; $env:EET_SMOKE_PASSWORD=$pw
uv run python scripts/smoke-staging.py --base https://localhost:8443 --insecure
```

```text
Smoke del staging — 2026-10-30 14:02 UTC
Proxy: https://localhost:8443 · TLS: sin validar (ensayo)

[PASS] http_redirect_301        GET http://localhost:8080/ -> 301 https://localhost:8443/
[PASS] salud_200                GET /salud -> 200
[PASS] raiz_redirige_login      GET / -> 303 /auth/login?next=%2F
[PASS] login_200                GET /auth/login -> 200
[PASS] hsts                     Strict-Transport-Security: max-age=31536000; includeSubDomains
[PASS] csp_con_nonce            CSP con nonce y mismo valor en el HTML
[PASS] nosniff                  X-Content-Type-Options: nosniff
[PASS] cookie_csrf              cookie eet_csrf presente · campo csrf_token presente
[PASS] docs_no_expuesto         GET /docs -> 303 (no debe ser 200)
[PASS] login_post               POST /auth/login -> 303 /auth/seguridad
[PASS] cookie_sesion            __Host-eet_session=***; HttpOnly; Max-Age=43200; Path=/; SameSite=lax; Secure
[PASS] cookie_sin_prefijo_host  no se emite eet_session sin prefijo `__Host-`
[PASS] sesion_autenticada       GET /auth/seguridad -> 200
[PASS] logout                   POST /auth/logout -> 303 /auth/login?ok=sesion_cerrada
[PASS] sesion_cerrada           GET / tras logout -> 303 /auth/login?next=%2F

Resumen: 15 PASS · 0 SKIP · 0 FAIL        (exit 0)
```

**Cookie verificada**: `__Host-eet_session` con `Secure`, `HttpOnly`,
`SameSite=lax`, `Path=/`, `Max-Age=43200` (12 h) y **sin `Domain`**, que es
exactamente lo que exige el prefijo `__Host-`. No se emite `eet_session` sin
prefijo. Al terminar, `select count(*) from sesiones where revoked_at is null
and expires_at > now()` devuelve **0**: el smoke no deja sesiones activas.

### 3.6 `curl`

```powershell
curl.exe -k -s -I http://localhost:8080/
# HTTP/1.1 301 Moved Permanently
# Location: https://localhost:8443/

curl.exe -k -s -D - -o NUL https://localhost:8443/auth/login
# HTTP/1.1 200 OK
# set-cookie: eet_csrf=…; HttpOnly; Max-Age=43200; Path=/; SameSite=lax; Secure
# x-content-type-options: nosniff · x-frame-options: DENY
# content-security-policy: … script-src 'self' 'nonce-<16B>' …
# strict-transport-security: max-age=31536000; includeSubDomains
# cache-control: no-store
```

> `curl -I` sobre `/auth/login` devuelve **405**: la ruta solo declara `GET` y
> `HEAD` no está montado. Las cabeceras salen igual porque las añade el
> middleware. Para inspeccionar cabeceras use `-D -` con GET.

### 3.7 Comandos de operación en el staging

```text
$ docker compose … exec app uv run --no-sync --frozen python -m app.cli estado-auth
Estado de autenticación (UTC · 2026-10-30 14:00)
  Usuarios: 5 (activos 5 · inactivos 0)
    por rol:       supervision 1 · cargador 1 · asistente 3
    por proveedor: local 5
  Sesiones activas: 0
  Desafíos 2FA pendientes: 0
  Códigos de respaldo sin usar: 0
  Correo: 0 pendiente(s) · 0 agotado(s)
  Último acceso: (sin accesos)
```

`reenviar-correo` y `re-cifrar` se validan en las pruebas (ver §4) y su
procedimiento está en el runbook.

---

## 4. Pruebas

| Suite | Antes | Después |
|---|---|---|
| `pytest -q` (unitarias) | 212 | **224** (12 nuevas) |
| `pytest -m integration -q` | 266 | **269** (3 nuevas) |

`tests/test_cli_operacion.py` — 12 casos:

| Bloque | Casos |
|---|---|
| Re-cifrado (criptografía pura) | `v1`→`v2` con round-trip y `key_id` nuevo; idempotencia (`actual`); clave ausente → `fallo`; cuerpo de la cola `enc:v1:` → `enc:v2:`; cuerpos no cifrados intactos y token corrupto → `fallo` |
| Contrato de la CLI | `--limit` por defecto 20 y validación de `0`; subcomandos registrados |
| Exit codes con sesión simulada | todo migra → 0 y no imprime secretos; clave desconocida → 1; sin cofre → 2 |
| Integración real | TOTP + outbox `enc:v1` migran a `v2` en PostgreSQL, descifran igual y la segunda pasada no re-cifra; `estado-auth` sale 0 sin datos; `reenviar-correo` sin pendientes sale 0 |

`tests/test_mailer_fechas.py` — 3 casos sin base de datos: el nombre del `.eml`,
el cierre de la fila de la cola y el reenvío no emiten el
`DeprecationWarning` de `utcnow` (captura con `warnings.catch_warnings`), y
`enviado_en` sigue siendo `datetime` naive en UTC.

Nota: el caso de integración de `re-cifrar` contrasta el código de salida con el
resumen impreso (`0` si `fallos: 0`), porque una base de desarrollo puede
contener cuerpos históricos cifrados con una clave ya retirada —el comando los
cuenta como fallo y **no los altera**—.

---

## 5. Requisitos que siguen dependiendo de TI

| Punto | Estado | Qué falta |
|---|---|---|
| **SMTP real** | `console` en staging | Host, puerto, usuario y clave del relay; SPF, DKIM y DMARC del dominio; prueba de entrega a Gmail y Outlook |
| **DNS y certificado público** | Certificado local de CA propia | Registro del dominio, certificado emitido por la CA institucional y su renovación |
| **CIDR definitivos** | `172.28.0.0/24` (red Docker de ensayo) | Rangos de la red interna/VPN reales y `EET_ADMIN_INTERNAL_ONLY=true` |
| **Firewall de borde** | No aplica en el host de ensayo | Solo 80/443 al proxy; app y base sin exposición |
| **WAF / rate limit de borde** | No | Recomendado contra credential stuffing |
| **Backups** | Fuera de alcance | Política de respaldo de base y claves, y restauración probada |

---

## 6. Incidencias del entorno (ajenas al código)

Durante la sesión, y **sin relación con el compose de staging** (sus labels son
`com.docker.compose.project=eet-staging`), el entorno de desarrollo cambió por
otro proceso:

- los contenedores `eet-app` y `eet-mapa-aep` desaparecieron;
- una aplicación Reflex de otro proyecto tomó `0.0.0.0:8000`;
- un PostgreSQL nativo de Laragon tomó `127.0.0.1:5432` (sin rol `eet`).

Consecuencias y cómo se resolvió:

- El **volumen de desarrollo** `sistema_modelo_mapas_eet-pgdata` quedó intacto
  (5 usuarios, 221 fichas, Alembic `0003`).
- Para ejecutar la suite de integración se montó ese mismo volumen en un
  contenedor temporal en el puerto **5435**
  (`eet-db-devcheck`), sin tocar `docker-compose.yml` ni el volumen. **269
  pruebas en verde**. El contenedor temporal se retiró al terminar.
- No se intentó recrear el stack de desarrollo: los puertos 8000 y 5432 están
  ocupados por procesos ajenos. El volumen permanece disponible para cuando se
  liberen.

---

## 7. A6.12 — cierre

El smoke pasó **15/15** y el preflight **20 PASS / 2 WARN / 0 FAIL**, con lo que
se dan por verificados:

- HTTP→HTTPS 301 emitido por el proxy;
- TLS 1.2/1.3 con certificado de CA local;
- HSTS y CSP con nonce por petición a través del proxy;
- cookies `__Host-eet_session` (`Secure`, `HttpOnly`, `SameSite=lax`, sin
  `Domain`) y ausencia de la variante sin prefijo;
- `/docs` no accesible;
- proxy de confianza operativo (`X-Forwarded-Proto` aceptado solo desde
  `172.28.0.10`);
- logout sin dejar sesiones activas.

**A6.12: completada.** Los pendientes de la §5 son de infraestructura (TI) y no
bloquean el cierre del ensayo.
