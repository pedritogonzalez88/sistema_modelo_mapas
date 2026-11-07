# Producción con HTTP interno y nginx externo (TLS)

Arquitectura:

```text
Internet
  │ 443/TLS
  ▼
nginx externo (host o contenedor)
  │ HTTP 127.0.0.1:8000
  ▼
app FastAPI (contenedor, sin puerto público)
  │ red interna
  ▼
PostgreSQL/PostGIS
```

El navegador **sigue usando HTTPS**. El tramo `nginx -> app` es HTTP local; la
app confía en `X-Forwarded-Proto=https` para generar enlaces, cookies `Secure`
y HSTS. No exponga el puerto 8000 a Internet.

## 1. Generar la configuración

Linux/WSL:

```bash
bash scripts/prepare-production.sh \
  --http-only \
  --dominio "eet.aep.gov.py" \
  --smtp-host "smtp.aep.gov.py" \
  --smtp-user "validador" \
  --admin-email "admin@aep.gov.py" \
  --vpn-cidr "10.20.0.0/16"
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/prepare-production.ps1 `
  -HttpOnly `
  -Dominio "eet.aep.gov.py" `
  -SmtpHost "smtp.aep.gov.py" `
  -SmtpUser "validador" `
  -AdminEmail "admin@aep.gov.py" `
  -VpnCidr "10.20.0.0/16"
```

El script crea `.env.production` con:

```text
EET_PUBLIC_BASE_URL=https://<dominio>
EET_COOKIE_SECURE=true
EET_TRUSTED_PROXY_IPS=127.0.0.1/32
EET_HTTP_BIND=127.0.0.1
EET_HTTP_PORT=8000
```

Si el nginx externo corre en otro contenedor, cambie `EET_HTTP_BIND=0.0.0.0`
y restrinja el puerto 8000 por firewall; ajuste `EET_TRUSTED_PROXY_IPS` a la
IP/CIDR de ese contenedor.

## 2. nginx externo

Use `nginx/nginx.external.conf` como base. En el host:

```bash
sudo cp nginx/nginx.external.conf /etc/nginx/conf.d/eet.conf
sudo editor /etc/nginx/conf.d/eet.conf   # dominio y rutas de certificados
sudo nginx -t
sudo systemctl reload nginx
```

El proxy debe enviar:

```nginx
proxy_set_header Host              $http_host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Sin `X-Forwarded-Proto` la app no reconocerá HTTPS y no emitirá HSTS.
`client_max_body_size` debe coincidir con `EET_MAX_UPLOAD_MB` (20 MB).

## 3. Preflight

```powershell
powershell -ExecutionPolicy Bypass -File scripts/preflight-production.ps1 -HttpOnly
```

No exige certificados locales (los tiene nginx) y valida variables, Compose y
`Settings()`. En Linux puede validar el compose directamente:

```bash
docker compose --env-file .env.production \
  -f docker-compose.http.yml config --quiet
```

## 4. Levantar

El directorio de uploads debe pertenecer al UID del contenedor:

```bash
sudo install -d -o 10001 -g 10001 -m 0750 /opt/sistema_modelo_mapas/uploads
```

```bash
docker compose --env-file .env.production \
  -f docker-compose.http.yml up -d --build
```

La app ejecuta `alembic upgrade head` antes de Uvicorn y el healthcheck usa
`/ready`. PostgreSQL no publica puertos.

## 5. Verificación

- `https://<dominio>/salud` → 200 `{"estado":"ok"}`.
- `https://<dominio>/ready` → 200 `{"estado":"listo","base":"ok"}`.
- Respuesta con `Strict-Transport-Security`.
- Login del administrador + enrolamiento TOTP.
- Subida de una entrega y confirmación.
- `docker compose ... ps` con `app` y `db` en `healthy`.
- `curl -I http://127.0.0.1:8000/salud` solo debe funcionar desde el host.

## 6. Cambiar entre modos

`docker-compose.http.yml` usa el mismo proyecto (`eet-prod`), volumen de
PostgreSQL y contenedores que `docker-compose.prod.yml`. Por eso:

1. Detenga el stack anterior:
   ```bash
   docker compose --env-file .env.production -f docker-compose.prod.yml down
   ```
2. Levante el otro modo. Los datos de PostgreSQL y uploads se conservan.

No ejecute ambos stacks a la vez: comparten nombres de contenedor y volumen.

## 7. Notas de seguridad

- `EET_ADMIN_INTERNAL_ONLY=true` funciona porque la app lee la IP real desde
  `X-Forwarded-For` (el proxy es de confianza). Si el proxy no la envía, todos
  los clientes parecerán `127.0.0.1` y la restricción no será efectiva.
- No publique `8000` en `0.0.0.0` salvo que el proxy esté en otro contenedor y
  el firewall limite el acceso.
- Mantenga `EET_COOKIE_SECURE=true` y `EET_PUBLIC_BASE_URL=https://...`: el
  usuario final navega por HTTPS.
- La app ya emite CSP, `X-Frame-Options`, `Referrer-Policy` y HSTS; no las
  duplique en nginx.
