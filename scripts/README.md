# Pruebas locales (WSL 2 + Docker)

## Requisitos

- Windows 11 con WSL 2 y una distro **Ubuntu** registrada.
- Docker Engine y el plugin Compose instalados dentro de Ubuntu.
- `uv` disponible en WSL (`/root/.local/bin/uv` o en `PATH`).
- Puerto 8000 libre en Windows y 5432 libre en WSL.

## Uso

Desde la raíz del repositorio en Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1
```

Opciones:

```powershell
# No reconstruir la imagen
powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1 -SkipBuild

# Solo pruebas rápidas (sin PostgreSQL)
powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1 -SkipIntegration

# Usar el código que ya está en /opt/sistema_modelo_mapas
powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1 -SkipSync
```

Dentro de WSL también se puede ejecutar directamente:

```bash
bash scripts/test-local.sh /mnt/c/Users/<usuario>/PycharmProjects/sistema_modelo_mapas
```

## Qué hace

1. Sincroniza el repositorio a `/opt/sistema_modelo_mapas` (sin `.git`,
   `.venv`, `node_modules` ni `uploads`; conserva `.venv`, `uploads` y
   `deploy/.env.local` del destino).
2. Genera `deploy/.env.local` si no existe, con secretos aleatorios y
   credenciales demo.
3. Levanta `db` (PostGIS) y `app` con `docker compose up -d --wait`.
4. Ejecuta `uv sync --frozen --dev`.
5. Siembra datos demo idempotentes con `scripts/seed_demo_data.py`:
   60 cruces, 56 fichas, la entrega 1 con un `.xlsx` válido y las cuentas
   `demo.admin` / `demo.consulta`.
6. Ejecuta `pytest -q` (319 pruebas) y `pytest -m integration` (286 pruebas).
7. Hace smoke HTTP de `/salud`, `/ready` y, si la autenticación está activa,
   login real de administrador y consulta, recorriendo las páginas permitidas
   y denegadas para cada rol, más el XLSX consolidado.

## Arranque y watchdog

```powershell
# Levanta WSL + Docker + compose y abre el login
scripts\start-local.cmd

# Solo levanta el stack, sin abrir el navegador
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1 -NoBrowser
```

Se registran dos tareas:

- **EET WSL Keepalive**: mantiene una sesión WSL abierta (`sleep infinity`) y
  la reinicia si la VM se apaga, para que Docker y los contenedores vuelvan
  solos.
- **EET Local Watchdog**: revisa `/salud` cada 5 minutos y levanta el stack si
  WSL o los contenedores se detuvieron.

Ambas tareas se lanzan mediante `scripts/run-hidden.vbs` (`wscript.exe`) para
que PowerShell arranque oculto desde el inicio y Windows Terminal no muestre un
flash de consola cada 5 minutos.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register-autostart.ps1
```

Para quitarlas:

```powershell
Unregister-ScheduledTask -TaskName "EET Local Watchdog","EET WSL Keepalive" -Confirm:$false
```

El log del watchdog está en `%LOCALAPPDATA%\EET\keep-local.log`.

## Producción

Guía completa: `deploy/README-production.md`.

```powershell
# 1. Generar deploy/.env.production con secretos y datos reales (Windows)
powershell -ExecutionPolicy Bypass -File scripts/prepare-production.ps1 `
  -Dominio "eet.aep.gov.py" -SmtpHost "smtp.aep.gov.py" `
  -SmtpUser "validador" -AdminEmail "admin@aep.gov.py"

# 1b. Equivalente en Linux/WSL
bash scripts/prepare-production.sh `
  --dominio "eet.aep.gov.py" --smtp-host "smtp.aep.gov.py" `
  --smtp-user "validador" --admin-email "admin@aep.gov.py"

# 1c. TLS en nginx externo (app escucha HTTP en 127.0.0.1:8000)
bash scripts/prepare-production.sh --http-only `
  --dominio "eet.aep.gov.py" --smtp-host "smtp.aep.gov.py" `
  --smtp-user "validador" --admin-email "admin@aep.gov.py"
# Luego: deploy/docker-compose.http.yml + deploy/nginx.external.conf
# Guía: deploy/README-http.md

# 2. Copiar fullchain.pem/privkey.pem a deploy/certs y validar
powershell -ExecutionPolicy Bypass -File scripts/preflight-production.ps1

# 3. Levantar el stack de producción
docker compose --env-file deploy/.env.production `
  -f deploy/docker-compose.prod.yml up -d --build

# 4. Crear el administrador inicial (TOTP obligatorio)
powershell -ExecutionPolicy Bypass -File scripts/bootstrap-admin.ps1
```

## Auditoría, backup y CI

```bash
# Duplicados y valores fuera de rango (antes de migrar)
EET_DATABASE_URL=postgresql+psycopg://eet:eet@localhost:5432/eet \
  .venv/bin/python scripts/audit_duplicates.py

# Clave de cifrado (una sola vez, en WSL)
wsl -d Ubuntu -u root --exec bash -c "install -d -m 700 /etc/eet && openssl rand -hex 32 > /etc/eet/backup.key && chmod 600 /etc/eet/backup.key"

# Backup cifrado + restauración de prueba a un disco/NAS externo
powershell -ExecutionPolicy Bypass -File scripts/backup.ps1 `
  -Destino "D:\EET-Backups" -KeyFile "/etc/eet/backup.key" -Verify

# Programar el backup diario a las 02:30 (alternativa a systemd)
powershell -ExecutionPolicy Bypass -File scripts/register-backup.ps1 `
  -Destino "D:\EET-Backups"
```

En Linux/WSL también puede instalar
`deploy/systemd/eet-backup.service` y `deploy/systemd/eet-backup.timer`.
El backup incluye PostgreSQL/PostGIS y `uploads`, se cifra con AES-256-CBC
(PBKDF2) y verifica la restauración en una base temporal antes de publicar los
archivos `.enc`.

## Easypanel

No use el `docker-compose.yml` de desarrollo en Easypanel: tiene
`container_name` y `ports`, que Easypanel administra. Use el modo
**App/Dockerfile** o, si prefiere Compose, `docker-compose.easypanel.yml`.
Guía completa: `deploy/README-easypanel.md`.

Para generar las variables que se pegan en **Environment**:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/prepare-easypanel-env.ps1 `
  -Dominio "eet.aep.gov.py" -SmtpHost "smtp.aep.gov.py" `
  -SmtpUser "validador" -AdminEmail "admin@aep.gov.py"
```

El script crea `deploy/.env.easypanel` (gitignored) con password de base
URL-safe y secretos aleatorios. Easypanel no lee ese archivo: hay que copiar su
contenido al editor de Environment.

## Monitoreo

```powershell
.venv\Scripts\python.exe scripts\healthcheck-production.py `
  --base-url https://eet.aep.gov.py `
  --backup-dir /mnt/backup/eet `
  --cert deploy/certs/fullchain.pem
```

Comprueba `/salud`, `/ready` (base), espacio libre, antigüedad del backup y
vencimiento del certificado. Devuelve código 1 ante cualquier fallo. Con
`EET_TELEGRAM_TOKEN` y `EET_TELEGRAM_CHAT_ID` envía la alerta por Telegram.
El timer `deploy/systemd/eet-healthcheck.timer` lo ejecuta cada 5 minutos.

El workflow `.github/workflows/ci.yml` ejecuta lint, unitarias, integración con
PostGIS y build de la imagen en cada push/PR. En push a `master` publica
`ghcr.io/<repo>:sha-<commit>` y `:latest`.

El modal de acciones de usuarios se puede validar en un navegador real:

```powershell
npm ci
node scripts/modal-smoke.mjs
```

Requiere Edge instalado; usa `demo.admin` por defecto y permite
`EET_SMOKE_USER` / `EET_SMOKE_PASSWORD` para otras cuentas.

## Credenciales demo

El script crea `demo.admin` (rol admin) y `demo.consulta` (rol consulta) con
MFA deshabilitado **solo para la demo**. Las contraseñas se generan en el primer
arranque y se guardan en `deploy/.env.local` (gitignored) como
`EET_DEMO_ADMIN_PASSWORD` y `EET_DEMO_CONSULTA_PASSWORD`.

Para usar contraseñas propias, expórtelas antes de ejecutar el script o
escríbelas en `deploy/.env.local`.

## Notas

- Las pruebas de rutas y accesibilidad ya no dependen de una base poblada: el
  fixture `datos_web` de `tests/conftest.py` crea y limpia el cruce 81/2008, la
  ficha 1, la entrega 1 y las filas de paginación. El seed queda para la demo
  visual y el smoke de login.
- El servicio `app` tiene healthcheck contra `/salud`; `docker compose ps`
  muestra `(healthy)` y `--wait` espera a que la app responda de verdad.
- `/health` es un alias público de `/salud` para monitores externos.
- La sincronización no borra archivos eliminados del destino. Si necesita una
  copia limpia, elimine `/opt/sistema_modelo_mapas` y vuelva a ejecutar.
- `EET_COOKIE_SECURE=false` es solo para HTTP local. En producción use HTTPS y
  cookies `Secure`, y exija TOTP a `admin`/`supervision`.
- Los secretos viven en `deploy/.env.local` (gitignored). Si se rotan, use
  `EET_FERNET_KEYS="v2:<nueva>,v1:<anterior>"` para conservar la lectura de
  correos/TOTP antiguos.
