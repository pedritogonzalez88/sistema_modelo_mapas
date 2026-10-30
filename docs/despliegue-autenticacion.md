# Despliegue de la autenticación (Fases A0–A6a)

> Guía operativa para publicar el **Validador EET** en internet y en la red interna.
> Complementa `docs/plan-implementacion-autenticacion.md` §6 y
> `docs/analisis-autenticacion.md` §11. El detalle de esta fase está en
> `docs/fase-a6a-endurecimiento.md`.

---

## 1. Resumen en una tabla

| Aspecto | Desarrollo (`dev`) | Staging | Producción (`prod`) |
|---|---|---|---|
| `EET_ENVIRONMENT` | `dev` | `staging` | `prod` |
| Transporte | HTTP local | HTTPS recomendado | **HTTPS obligatorio** |
| Cookies | `eet_session` (sin `Secure`) | `__Host-…` | `__Host-…` + `Secure` |
| HSTS | no se emite (no hay HTTPS) | sí, sobre HTTPS | sí, sobre HTTPS |
| Docs (`/docs`) | opcionales | ocultas | **ocultas** (`EET_EXPOSE_DOCS=false`) |
| SMTP | `console` / `file` | `smtp` (relay de pruebas) | `smtp` institucional |
| Validación al arrancar | ninguna | advertencias en el log | **falla cerrada** |

Con `EET_AUTH_ENABLED=false` la aplicación se comporta como antes de la Fase A1:
no hay gate, ni cookies de sesión, ni CSRF efectivo. **No es una configuración
publicable en internet.**

---

## 2. Variables de entorno

Todas llevan el prefijo `EET_` y se pueden definir en el entorno del contenedor,
en un `docker-compose.override.yml` o en un `.env` junto al proceso (ese archivo
nunca se versiona).

### 2.1 Aplicación y base

| Variable | Default | Notas |
|---|---|---|
| `EET_DATABASE_URL` | `postgresql+psycopg://eet:eet@localhost:5433/eet` | En Docker: `…@db:5432/eet` |
| `EET_UPLOAD_DIR` | `./uploads` | Volumen persistente obligatorio |
| `EET_ENVIRONMENT` | `dev` | `dev` \| `staging` \| `prod` |
| `EET_TRUSTED_PROXY_IPS` | vacío | CIDR del reverse proxy, separados por coma |
| `EET_EXPOSE_DOCS` | `false` | `true` solo en `dev` |
| `EET_MAX_UPLOAD_MB` | `20` | Tope por archivo (entregas, malla, mapa) |

### 2.2 Autenticación y sesiones

| Variable | Default | Notas |
|---|---|---|
| `EET_AUTH_ENABLED` | `false` | Activa gate, CSRF, roles y 2FA |
| `EET_SECRET_KEY` | vacío | ≥ 32 caracteres en `prod`; cifra secretos TOTP y deriva el CSRF |
| `EET_AUTH_PEPPER` | vacío | ≥ 16 caracteres en `prod`; HMAC de códigos de un solo uso |
| `EET_FERNET_KEYS` | vacío | `v2:<clave>,v1:<clave>` para rotar sin perder datos |
| `EET_SESSION_COOKIE` | `__Host-eet_session` | Debe empezar con `__Host-` en `prod` |
| `EET_COOKIE_SECURE` | `true` | `false` **solo** en `dev`/`staging` por HTTP |
| `EET_SESSION_ABSOLUTE_MIN` | `720` | Vida absoluta de la sesión (12 h) |
| `EET_SESSION_IDLE_MIN` | `30` | Inactividad tolerada |
| `EET_LOGIN_MAX_INTENTOS` | `5` | Fallos por cuenta antes de bloquearla |
| `EET_LOGIN_BLOQUEO_MIN` | `15` | Duración del bloqueo por cuenta |
| `EET_LOGIN_IP_MAX_15MIN` | `20` | Fallos por IP en 15 min (password spraying) |
| `EET_MFA_ISSUER` | `Validador EET (AEP)` | Nombre visible en la app autenticadora |
| `EET_MFA_WINDOW` | `1` | Pasos TOTP de tolerancia (±30 s) |

### 2.3 Correo institucional

| Variable | Default | Notas |
|---|---|---|
| `EET_EMAIL_BACKEND` | `console` | `console` \| `file` \| `smtp` (`smtp` obligatorio en `prod`) |
| `EET_EMAIL_DIR` | `uploads/mail` | Solo backend `file` |
| `EET_EMAIL_FROM` | `Validador EET <no-reply@localhost>` | Remitente del dominio institucional |
| `EET_SMTP_HOST` | vacío | Obligatorio con `smtp` |
| `EET_SMTP_PORT` | `587` | 587 con STARTTLS, 465 con TLS directo |
| `EET_SMTP_STARTTLS` | `true` | |
| `EET_SMTP_USER` / `EET_SMTP_PASSWORD` | vacío | Cuenta de relay; el password, en secreto |
| `EET_PUBLIC_BASE_URL` | `http://localhost:8000` | **https://** en `prod`; arma los enlaces del correo |

### 2.4 LDAP / Active Directory (apagado por defecto)

| Variable | Default | Notas |
|---|---|---|
| `EET_LDAP_ENABLED` | `false` | Con `false` no se abre ninguna conexión |
| `EET_LDAP_URL` | vacío | `ldap://` (StartTLS) o `ldaps://` (TLS directo) |
| `EET_LDAP_BIND_DN` / `EET_LDAP_BIND_PASSWORD` | vacío | Cuenta de servicio de solo lectura |
| `EET_LDAP_BASE_DN` | vacío | Raíz de búsqueda |
| `EET_LDAP_USER_FILTER` | `(sAMAccountName={login})` | |
| `EET_LDAP_ROLE_MAP` | `{}` | JSON `{"grupo": "rol"}`; roles válidos abajo |
| `EET_LDAP_DEFAULT_ROLE` | vacío | Vacío = denegar el acceso (fail closed) |
| `EET_LDAP_STARTTLS` | `true` | Solo aplica a `ldap://` |
| `EET_LDAP_AUTO_PROVISION` | `false` | Crea la cuenta local en el primer login |
| `EET_LDAP_GROUP_ATTR` | `memberOf` | `memberOf` (atributo) o `member` (búsqueda inversa) |

Roles válidos: `admin`, `supervision`, `cargador`, `consulta`, `asistente`.

### 2.5 Red y administración

| Variable | Default | Notas |
|---|---|---|
| `EET_ADMIN_INTERNAL_ONLY` | `true` | Restringe administración y auditoría por CIDR |
| `EET_INTERNAL_CIDRS` | `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32` | Red interna/VPN |

---

## 3. Validación al arrancar (`EET_ENVIRONMENT`)

`app/core/config.py` valida la configuración **al importar** el módulo: es
imposible arrancar un contenedor inseguro y descubrirlo después.

### 3.1 `prod` (falla cerrada)

Con `EET_ENVIRONMENT=prod` **y** `EET_AUTH_ENABLED=true` la aplicación no arranca si:

- `EET_COOKIE_SECURE` no es `true`;
- `EET_PUBLIC_BASE_URL` no empieza con `https://`;
- `EET_EMAIL_BACKEND` no es `smtp` (o falta `EET_SMTP_HOST`);
- `EET_EXPOSE_DOCS` no es `false`;
- `EET_SECRET_KEY` tiene menos de 32 caracteres;
- `EET_AUTH_PEPPER` tiene menos de 16 caracteres;
- `EET_SESSION_COOKIE` no empieza con `__Host-`.

El mensaje de error nombra **la variable y la condición**, nunca el valor:

```
Configuración insegura para EET_ENVIRONMENT=prod: EET_COOKIE_SECURE debe ser
true (cookies Secure sobre HTTPS). EET_PUBLIC_BASE_URL debe comenzar con https://
(enlaces de correo).
```

### 3.2 `staging` (solo advierte)

Registra `WARNING` por cada requisito incumplido y arranca. Sirve para ensayar el
despliegue completo (`A6.12`) sin bloquear el pipeline.

### 3.3 `dev`

No exige nada. Es el modo por defecto y el que usan las pruebas.

> La validación **solo** actúa con la autenticación activa: mientras
> `EET_AUTH_ENABLED=false` el rollout gradual del plan (Fase A0) sigue siendo
> posible sin exigir secretos.

---

## 4. Reverse proxy con TLS (Nginx)

La aplicación no termina TLS: lo hace el proxy. Dos detalles son críticos:

1. `X-Forwarded-Proto` para que la aplicación sepa que el usuario llegó por
   HTTPS (de eso dependen HSTS, `request.url.scheme` y los enlaces del correo).
2. `EET_TRUSTED_PROXY_IPS` para que **solo** ese proxy pueda reescribir la IP del
   cliente; sin él, `X-Forwarded-For` se ignora y el rate limiting usa la IP de
   la conexión.

```nginx
# /etc/nginx/conf.d/eet.conf
server {
    listen 80;
    server_name eet.aep.gov.py;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name eet.aep.gov.py;

    ssl_certificate     /etc/ssl/certs/eet.aep.gov.py.pem;
    ssl_certificate_key /etc/ssl/private/eet.aep.gov.py.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    # Tope de subida alineado con EET_MAX_UPLOAD_MB (20 MB por defecto).
    # El proxy rechaza antes de que el cuerpo llegue a la aplicación.
    client_max_body_size 20m;

    # Cabeceras de seguridad de la aplicación: no se duplican aquí.
    # HSTS lo emite la app cuando request.url.scheme == "https".

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_read_timeout 60s;
    }

    location /static/ {
        proxy_pass http://127.0.0.1:8000;
        expires 7d;
        add_header Cache-Control "public, max-age=604800, immutable";
    }
}
```

Variables correspondientes:

```ini
EET_ENVIRONMENT=prod
EET_TRUSTED_PROXY_IPS=127.0.0.1/32        # IP/CIDR del Nginx delante de la app
```

Con esa variable, `create_app()` agrega `ProxyHeadersMiddleware` (uvicorn) como
el middleware **más externo** y acepta `X-Forwarded-Proto`/`X-Forwarded-For`
solo de las redes indicadas. Si `EET_TRUSTED_PROXY_IPS` queda vacío, el
middleware no se monta y la IP del cliente es siempre `request.client.host`.

> Si el proxy y la aplicación corren en contenedores separados, use la red
> interna de Docker: `EET_TRUSTED_PROXY_IPS=172.18.0.0/16` (o el CIDR real).

---

## 5. Activación

```powershell
# 1. Secretos (una sola vez; guárdelos en el gestor de secretos)
python -c "import secrets; print('EET_SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('EET_AUTH_PEPPER=' + secrets.token_urlsafe(24))"

# 2. Migraciones
uv run alembic upgrade head        # 0003 (head)

# 3. Primer administrador (pide email real; el TOTP es obligatorio)
uv run python -m app.cli crear-admin

# 4. Activación
#    EET_AUTH_ENABLED=true
#    EET_ENVIRONMENT=prod
#    EET_COOKIE_SECURE=true
#    EET_PUBLIC_BASE_URL=https://eet.aep.gov.py
#    EET_EMAIL_BACKEND=smtp (+ SMTP_*)
#    EET_TRUSTED_PROXY_IPS=<CIDR del proxy>

# 5. Verificación
curl -sI https://eet.aep.gov.py/auth/login | findstr /i "content-security-policy strict-transport cache-control"
```

Comportamiento esperado de `curl -I https://…/auth/login`: `303` (o `200` en el
formulario), `content-security-policy` con `nonce-…`, `strict-transport-security`
presente, `cache-control: no-store`, `x-frame-options: DENY`, y **HTTP** sin HSTS.

---

## 6. Checklist de producción

### Antes de publicar

- [ ] `alembic upgrade head` aplicado y reversible probado (`downgrade`).
- [ ] `EET_ENVIRONMENT=prod` arranca sin errores (la validación es el primer filtro).
- [ ] `EET_AUTH_ENABLED=true`, `EET_COOKIE_SECURE=true`, `__Host-eet_session`.
- [ ] `EET_SECRET_KEY` ≥ 32 y `EET_AUTH_PEPPER` ≥ 16, ambos en el gestor de secretos.
- [ ] `EET_EXPOSE_DOCS=false` (`/docs` y `/openapi.json` responden 404).
- [ ] `EET_PUBLIC_BASE_URL` con **https** y el mismo host del certificado.
- [ ] SMTP probado con un envío real a Gmail y Outlook (no solo internos).
- [ ] SPF, DKIM y DMARC del dominio publicados (`dig txt …`).
- [ ] `EET_ADMIN_INTERNAL_ONLY=true` y `EET_INTERNAL_CIDRS` correctos.
- [ ] Firewall: solo 80/443 al proxy; la app y la base sin exposición directa.
- [ ] Backup de la base **y** de las claves (`EET_SECRET_KEY`/`EET_FERNET_KEYS`).
- [ ] Restauración probada en un entorno limpio.
- [ ] Dos administradores activos como mínimo (evita el bloqueo total).
- [ ] Alertas sobre picos de `login_fail`, `mfa_fail`, `login_ip_locked`,
      `password_reset_requested`.
- [ ] Revisión de dependencias (`uv lock --check`, avisos de seguridad).

### Después de publicar

- [ ] `/` redirige a `/auth/login?next=%2F` sin sesión.
- [ ] Login con el administrador, TOTP y códigos de respaldo guardados.
- [ ] `curl -I` muestra CSP con nonce y HSTS; ninguna cabecera duplicada.
- [ ] Un archivo de más de `EET_MAX_UPLOAD_MB` devuelve **413 HTML**
      («Archivo demasiado grande»).
- [ ] 11 fallos de login desde una IP terminan en «Demasiados intentos fallidos».
- [ ] La recuperación responde el mismo texto para cuenta existente e inexistente.
- [ ] La auditoría registra actor, IP y agente; ningún log contiene secretos.

---

## 7. CSP y por qué `'unsafe-inline'` en estilos

Cabecera emitida (un nonce distinto por petición):

```
default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none';
object-src 'none'; img-src 'self' data:; font-src 'self';
style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-<nonce>'; connect-src 'self'
```

- **`script-src` no admite `'unsafe-inline'`.** Los siete bloques `<script>`
  inline (`base.html`, `auth/base_auth.html`, `entregas.html`,
  `entrega_preview.html`, `exportar.html`, `qaqc_detalle.html`, `auth/mfa.html`)
  llevan `nonce="{{ request.state.csp_nonce }}"`; los `<script src="…">` externos
  se resuelven con `'self'`. No hay atributos `onclick=`/`onload=`.
- **`style-src` sí admite `'unsafe-inline'`** por una razón acotada: el panel y
  las barras de progreso usan atributos `style="width:NN%"` con un valor
  calculado por el servidor (dona de distribución y avance de cargas).
  Convertirlos exigiría generar CSS dinámico por respuesta (o cientos de clases
  `w-[NN%]`) sin ganancia real de seguridad: **no hay entrada de usuario en esos
  valores**, son porcentajes derivados de los datos del proyecto.
  Si en el futuro se migran a variables CSS (`style="--pct:42"`), `'unsafe-inline'`
  puede retirarse sin más cambios que la clase del componente.
- `frame-ancestors 'none'` + `X-Frame-Options: DENY`: doble barrera contra
  clickjacking (la cabecera clásica la respetan los navegadores antiguos).
- `form-action 'self'`: un formulario inyectado no puede exfiltrar a otro origen.
- `connect-src 'self'`: `fetch`/XHR solo al mismo origen.

### Otras cabeceras

| Cabecera | Valor | Motivo |
|---|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Solo si `request.url.scheme == "https"` |
| `X-Content-Type-Options` | `nosniff` | Evita el sniffing de tipos |
| `Referrer-Policy` | `no-referrer` | Los enlaces con token no se filtran por `Referer` |
| `X-Frame-Options` | `DENY` | Clickjacking |
| `Permissions-Policy` | `geolocation=(), camera=(), microphone=()` | La aplicación no usa esas APIs |
| `Cross-Origin-Opener-Policy` | `same-origin` | Aísla el contexto de navegación |
| `Cross-Origin-Resource-Policy` | `same-origin` | Bloquea la inclusión desde otros sitios |
| `Cache-Control` | `no-store` | En `/auth/*`: códigos, tokens y datos de sesión nunca se cachean |

`no-store` se aplica con `setdefault`: si un handler ya fijó uno más específico,
se respeta.

---

## 8. Rotación de claves

### 8.1 `EET_SECRET_KEY` (Fernet + CSRF)

La clave cifra los secretos TOTP y el cuerpo sensible de `email_outbox`.

```ini
# Estado inicial
EET_FERNET_KEYS=v1:<clave-vieja>

# 1) Agregar la nueva DELANTE (cifra lo nuevo, descifra lo viejo)
EET_FERNET_KEYS=v2:<clave-nueva>,v1:<clave-vieja>

# 2) Reiniciar y verificar que el login con TOTP sigue funcionando

# 3) Re-cifrar los datos existentes (script de mantenimiento) y retirar la vieja
EET_FERNET_KEYS=v2:<clave-nueva>
```

**Nunca** elimine una clave en uso: los secretos TOTP cifrados con ella quedan
ilegibles y esos usuarios deben re-enrolarse (el sistema lo detecta y los manda a
códigos de respaldo / reset asistido, no rompe la petición).

Rotar `EET_SECRET_KEY` invalida además los tokens CSRF derivados: los formularios
abiertos fallan con 403 y basta recargar. Las sesiones **no** se invalidan (el
token de sesión se guarda hasheado con SHA-256, no con la clave).

### 8.2 `EET_AUTH_PEPPER`

Cambiarlo invalida **todos** los códigos de correo y de respaldo emitidos (son
HMAC con pepper): los usuarios con códigos de respaldo impresos deben regenerarlos
o pedir un reset asistido. Planifique la rotación con la coordinación y avise
antes.

---

## 9. Backups y restauración

| Qué | Cómo | Frecuencia |
|---|---|---|
| Base PostgreSQL | `pg_dump -Fc eet` + copia fuera del host | diaria, retención ≥ 30 días |
| Claves (`EET_SECRET_KEY`, `EET_FERNET_KEYS`, `EET_AUTH_PEPPER`) | gestor de secretos + sobre sellado | en cada rotación |
| `uploads/` | snapshot del volumen | diaria |
| Configuración (`.env`, Nginx) | repositorio privado | en cada cambio |

Sin las claves, un backup de la base **no permite** reconstruir los secretos TOTP
ni los cuerpos cifrados de `email_outbox`: guarde ambos por separado y con
controles distintos.

Prueba de restauración (trimestral, en un entorno aislado):

```bash
createdb eet_restore
pg_restore -d eet_restore eet.dump
EET_DATABASE_URL=postgresql+psycopg://eet:eet@localhost:5432/eet_restore uv run alembic current
# Debe informar 0003 (head); luego probar login con un usuario de prueba.
```

---

## 10. Modo HTTP en `dev` y `staging`

En local y en un staging interno se puede trabajar sin TLS:

```ini
EET_ENVIRONMENT=staging
EET_COOKIE_SECURE=false      # permite eet_session sobre http
EET_PUBLIC_BASE_URL=http://eet-staging.interno:8000
```

Consecuencias, todas deliberadas:

- la cookie se llama `eet_session` (sin prefijo `__Host-`, que exige `Secure`) y
  el navegador la enviará por HTTP;
- **no** se emite HSTS (no habría HTTPS donde aplicarlo);
- `staging` solo advierte en el log por cada requisito de producción incumplido.

`EET_ENVIRONMENT=prod` **rechaza** `EET_COOKIE_SECURE=false`: es la única forma de
que un error de configuración no deje las cookies viajando en claro en internet.

---

## 11. Diagnóstico rápido

| Síntoma | Causa probable |
|---|---|
| El contenedor no arranca y el log nombra `EET_*` | Validación de `prod`: falta un requisito de §3.1 |
| Las cookies no persisten | `EET_COOKIE_SECURE=true` sobre HTTP, o proxy sin `X-Forwarded-Proto` |
| HSTS ausente en producción | El proxy no envía `X-Forwarded-Proto: https` (o falta `EET_TRUSTED_PROXY_IPS`) |
| Todos los clientes comparten la misma IP en auditoría | Falta `EET_TRUSTED_PROXY_IPS` con el CIDR del proxy |
| El navegador bloquea un script y la consola nombra `script-src` | Un `<script>` inline nuevo sin `nonce="{{ request.state.csp_nonce }}"` |
| 413 en cargas válidas | `EET_MAX_UPLOAD_MB` menor que el archivo, o `client_max_body_size` menor que la app |
| La recuperación «no envía nada» | `EET_EMAIL_BACKEND=console` en producción (prohibido por la validación) |
| «Demasiados intentos fallidos» con la clave correcta | Bloqueo por cuenta (`5`) o por IP (`EET_LOGIN_IP_MAX_15MIN`); revisar `login_attempts` |

---

## 12. Staging reproducible con TLS (A6.12)

Ensayo completo del despliegue —`EET_AUTH_ENABLED=true`, HTTPS, cookies
`__Host-` y proxy de confianza— sin tocar el entorno de desarrollo. La
infraestructura está en [`deploy/`](../deploy/README.md) y el informe del
ensayo en [`docs/fase-a6c-staging.md`](fase-a6c-staging.md).

### 12.1 Piezas

| Servicio | Imagen | Publica | Notas |
|---|---|---|---|
| `db` | `postgis/postgis:16-3.4` | `5434` | Volumen propio `eet-staging_*` |
| `app` | build del `Dockerfile` real | — | Sin puerto host: solo la ve el proxy; migra al arrancar |
| `proxy` | `nginx:1.27-alpine` | `8080`, `8443` | IP fija `172.28.0.10`, red `172.28.0.0/24` |

### 12.2 Comandos

```powershell
# 1) Certificado local (CA + servidor para localhost/127.0.0.1, 365 días)
uv run python scripts/gen-dev-cert.py

# 2) Variables (copiar y completar los dos secretos)
Copy-Item deploy/.env.staging.example deploy/.env.staging

# 3) Levantar y esperar healthy
docker compose -f deploy/docker-compose.staging.yml --env-file deploy/.env.staging up -d --build

# 4) Administrador (la clave va por entorno, nunca por parámetro)
docker compose -f deploy/docker-compose.staging.yml exec -e EET_ADMIN_PASSWORD=$pw app `
  uv run --no-sync --frozen python -m app.cli crear-admin `
  --email coordinacion@aep.gov.py --username staging-admin --nombre "Admin staging"

# 5) Preflight (configuración + base + servicio)
uv run python scripts/preflight-auth.py `
  --env-file deploy/.env.staging `
  --database-url postgresql+psycopg://eet:eet@localhost:5434/eet `
  --url https://localhost:8443 --insecure

# 6) Smoke del camino completo
$env:EET_SMOKE_USER='staging-admin'; $env:EET_SMOKE_PASSWORD=$pw
uv run python scripts/smoke-staging.py --base https://localhost:8443 --insecure
```

Salidas reales del ensayo (verificado el 30/10/2026): preflight **20 PASS ·
2 WARN · 0 FAIL**, smoke **15 PASS · 0 FAIL** y cookie
`__Host-eet_session=***; HttpOnly; Max-Age=43200; Path=/; SameSite=lax; Secure`.

### 12.3 Por qué el preflight necesita `--env-file` y `--database-url`

El preflight lee los `Settings` **reales** del proceso. Dentro del contenedor ya
están en el entorno:

```bash
docker compose -f deploy/docker-compose.staging.yml exec app \
  uv run --no-sync --frozen python scripts/preflight-auth.py
```

Desde el host, en cambio, hay que darle el archivo y el puerto publicado de la
base (`db` no se resuelve fuera de la red de Docker). `--database-url` solo
cambia la URL de conexión del chequeo; no altera el entorno del despliegue.

### 12.4 Diferencias con producción

| Punto | Staging | Producción |
|---|---|---|
| Certificado | CA local (`deploy/certs/`) | CA institucional |
| `EET_PUBLIC_BASE_URL` | `https://localhost:8443` | `https://<dominio>` |
| `EET_EMAIL_BACKEND` | `console` (aviso del preflight) | `smtp` + relay real |
| `EET_ADMIN_INTERNAL_ONLY` | `false` (para operar desde el host) | `true` con CIDR reales |
| Puertos del proxy | `8080`/`8443` | `80`/`443` |
| Validación de `Settings` | advierte | **falla cerrada** |

### 12.5 Apagado

```powershell
# Detiene y elimina contenedores, red y volumen del staging
docker compose -f deploy/docker-compose.staging.yml down -v
```

No afecta al entorno de desarrollo: es otro proyecto de Compose (`eet-staging`) y
otro volumen.
