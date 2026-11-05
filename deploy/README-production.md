# Despliegue de producción — Validador EET

Arquitectura: **un host**, Docker Compose, nginx con TLS, PostgreSQL/PostGIS
interno (sin puerto publicado), app interna (sin puerto publicado) y backups a
un disco/NAS externo.

> **Easypanel:** no use este compose dentro de Easypanel. Easypanel debe
> desplegar la app en modo **App (Dockerfile = `Dockerfile`)** y administrar
> TLS/proxy. Guía específica: `deploy/README-easypanel.md`.

## 1. Requisitos

- Dominio y certificado TLS (`fullchain.pem`, `privkey.pem`).
- DNS apuntando al servidor.
- Docker Engine + Compose v2.
- Firewall: solo 80/443 y SSH restringido.
- VPN/LAN administrativa si `EET_ADMIN_INTERNAL_ONLY=true`.
- SMTP institucional con credenciales y SPF/DKIM.

## 2. Configuración

Opción recomendada (genera secretos aleatorios y ACL):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/prepare-production.ps1 `
  -Dominio "eet.aep.gov.py" `
  -SmtpHost "smtp.aep.gov.py" `
  -SmtpUser "validador" `
  -AdminEmail "admin@aep.gov.py" `
  -VpnCidr "10.20.0.0/16"
```

En Linux/WSL:

```bash
bash scripts/prepare-production.sh \
  --dominio "eet.aep.gov.py" \
  --smtp-host "smtp.aep.gov.py" \
  --smtp-user "validador" \
  --admin-email "admin@aep.gov.py" \
  --vpn-cidr "10.20.0.0/16"
```

Ambos crean `deploy/.env.production` (gitignored) con secretos aleatorios y
password de base URL-safe. No lo suba al repositorio ni lo comparta. También
puede copiar `deploy/.env.production.example` y completarlo a mano.

> En WSL, si el repositorio está en `/mnt/c`, `chmod 600` puede no aplicarse.
> Ejecute el script sobre una ruta ext4 o habilite metadata en `/etc/wsl.conf`.

Revise especialmente:

- `EET_PUBLIC_BASE_URL=https://<dominio>`
- `EET_COOKIE_SECURE=true`
- `EET_EMAIL_BACKEND=smtp`
- `EET_ADMIN_INTERNAL_ONLY=true`
- `EET_TRUSTED_PROXY_IPS=172.30.0.10`
- `EET_INTERNAL_CIDRS` con la VPN/LAN administrativa
- `EET_SECRET_KEY` (32+), `EET_AUTH_PEPPER` (16+)
- `POSTGRES_PASSWORD` y `EET_DATABASE_URL` con el mismo password URL-safe

## 3. Certificados

Copie los certificados a:

```text
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
```

Con Let's Encrypt puede emitirlos en el host y copiarlos; programe la
renovación y un `docker compose ... exec proxy nginx -s reload`.

## 4. Preflight

```powershell
powershell -ExecutionPolicy Bypass -File scripts/preflight-production.ps1
```

Valida variables, ausencia de demo/placeholders, certificados, `docker compose
config` y `Settings()` en la imagen. Debe terminar en **PREFLIGHT OK**.

## 5. Levantar el stack

La imagen corre como **UID/GID 10001** y el filesystem es de solo lectura. El
directorio de uploads es la única escritura persistente, así que debe
pertenecer a ese usuario en el host:

```bash
sudo install -d -o 10001 -g 10001 -m 0750 /opt/sistema_modelo_mapas/uploads
```

```bash
docker compose --env-file deploy/.env.production \
  -f deploy/docker-compose.prod.yml up -d --build
```

El contenedor de la app ejecuta `alembic upgrade head` antes de Uvicorn. La
base queda en la red `internal` y **no** se publica el puerto 5432. El
healthcheck usa `/ready`, que comprueba la base de datos.

## 6. Crear el administrador inicial

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap-admin.ps1
```

Solicita usuario, nombre, correo y contraseña. El primer login exigirá cambiar
la contraseña y enrolar TOTP. No hay usuarios demo en producción.

## 7. Smoke de producción

- `https://<dominio>/salud` → 200 `{"estado":"ok"}`.
- `https://<dominio>/ready` → 200 `{"estado":"listo","base":"ok"}`.
- Login del administrador + enrolamiento TOTP.
- Recuperación de contraseña y código por correo (SMTP real).
- Alta de un usuario de prueba, entrega, confirmación, QA/QC y exportación.
- Revisar `docker compose ... ps` (todos `healthy`).
- Revisar logs sin secretos (`request_id` permite correlacionar una petición).

## 8. Backups

Genere la clave una sola vez y protéjala fuera del servidor:

```bash
sudo install -d -m 700 /etc/eet
sudo sh -c 'openssl rand -hex 32 > /etc/eet/backup.key'
sudo chmod 600 /etc/eet/backup.key
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup.ps1 `
  -Destino "D:\EET-Backups" -KeyFile "/etc/eet/backup.key" -Verify
```

En Linux/WSL, instale el timer diario:

```bash
sudo cp deploy/systemd/eet-backup.service deploy/systemd/eet-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eet-backup.timer
systemctl list-timers eet-backup.timer
```

Ajuste `/mnt/backup/eet` en la unidad al NAS o disco **externo** real. Programe
una prueba de restauración mensual. No guarde el backup en el mismo volumen de
PostgreSQL.

## 9. Monitoreo y alertas

El chequeo operativo valida HTTP, base (`/ready`), espacio, antigüedad del
backup y vencimiento del certificado:

```bash
.venv/bin/python scripts/healthcheck-production.py \
  --base-url https://<dominio> \
  --backup-dir /mnt/backup/eet \
  --cert deploy/certs/fullchain.pem
```

Para alertas por Telegram, cree `/etc/eet/healthcheck.env` (modo 600):

```text
EET_TELEGRAM_TOKEN=...
EET_TELEGRAM_CHAT_ID=...
```

Instale el timer cada 5 minutos:

```bash
sudo cp deploy/systemd/eet-healthcheck.service deploy/systemd/eet-healthcheck.timer /etc/systemd/system/
# Edite el dominio y las rutas de la unidad antes de habilitarla.
sudo systemctl daemon-reload
sudo systemctl enable --now eet-healthcheck.timer
journalctl -u eet-healthcheck.service -n 50
```

Además, configure un monitor externo (Uptime Kuma, Zabbix, etc.) contra
`/salud` y `/ready`. Los contenedores limitan `json-file` a 10 MB × 5.

## 10. Operación y actualizaciones

```bash
# Estado
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml ps

# Logs
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml logs -f app

# Actualización (con backup previo)
docker compose ... pull
docker compose ... up -d --build
docker compose ... ps
```

El arranque aplica migraciones. Antes de actualizar: backup y ventana de
mantenimiento. Rollback: volver a la imagen anterior y, si la migración no es
compatible, restaurar el backup.

> `docker compose down -v` **borra** el volumen de PostgreSQL. No usarlo en
> producción salvo que se quiera destruir la base.

## 11. Incidentes y rollback

1. **Caída de la app:** revisar `docker compose ... ps`, `/ready` y
   `journalctl`; si la base no responde, revisar disco y logs de `db`.
2. **Error 5xx masivo:** identificar `request_id` en los logs, evaluar rollback
   a la imagen anterior y conservar la base.
3. **Migración fallida:** no borrar el volumen; restaurar el backup previo en
   una base nueva y cambiar `EET_DATABASE_URL` tras validar.
4. **Backup fallido o antiguo:** ejecutar `backup-local.sh --verify` manualmente
   y alertar al responsable; no esperar al siguiente ciclo.
5. **Certificado vencido:** renovar, copiar a `deploy/certs/` y ejecutar
   `docker compose ... exec proxy nginx -s reload`.
6. **Compromiso de credenciales:** rotar `EET_SECRET_KEY`, `EET_AUTH_PEPPER`,
   contraseña de PostgreSQL, SMTP y clave de backup; invalidar sesiones.

Contactos: completar con responsable de aplicación, TI/operaciones y
proveedor de red/SMTP antes del go-live.

## 12. Endurecimiento aplicado y pendiente

Aplicado en el compose/imagen:

- App non-root (10001), filesystem read-only, `cap_drop: ALL`,
  `no-new-privileges`, `tmpfs` para temporales.
- Límites de CPU/memoria/PIDs y `stop_grace_period` para app, base y proxy.
- Logs rotados; healthcheck contra `/ready`.

Pendiente en el host:

- OS actualizado, SSH con claves y sin root directo, fail2ban, NTP.
- Firewall: solo 80/443 y SSH restringido; Docker socket solo para root.
- Escaneo de imagen/dependencias (`trivy`, `pip-audit`) y revisión externa.
- Renovación automática del certificado y prueba de restore mensual.

## 13. CI/CD

`.github/workflows/ci.yml` ejecuta lint, unitarias, integración con PostGIS y
build en cada push/PR. En push a `master` publica:

```text
ghcr.io/pedritogonzalez88/sistema_modelo_mapas:sha-<commit>
ghcr.io/pedritogonzalez88/sistema_modelo_mapas:latest
```

Para desplegar una versión concreta, cambie la imagen del servicio `app` en el
compose de producción por el tag `sha-<commit>` (o use `docker pull` y
`docker compose up -d`). El despliegue a staging/producción con aprobación
manual queda pendiente de configurar los secretos del servidor.
