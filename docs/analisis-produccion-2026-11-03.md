# Análisis para puesta en producción — Validador EET

**Fecha:** 2026-11-03
**Alcance:** un proyecto, un servidor, una instancia (decisiones confirmadas).
**Estado del código:** 319 pruebas unitarias + 286 de integración = **605 passed**; cobertura ~82%.
**Migración actual:** `0005 (head)`, `alembic check` sin diferencias.

---

## 1. Veredicto

**NO-GO todavía.** La aplicación funciona y la base técnica es sólida, pero el
despliegue actual es de **desarrollo/local**, no de producción:

- La configuración `EET_ENVIRONMENT=prod` **falla** hoy por cookies inseguras,
  URL pública HTTP y backend de correo de archivo.
- La base contiene datos demo/prueba (8 usuarios, 60 cruces, 56 fichas, 336
  correos de prueba, 943 eventos de auditoría).
- PostgreSQL usa credenciales fijas `eet/eet` y está publicado en el host.
- No hay TLS, backups programados, monitoreo, alertas ni CI/CD ejecutado.
- Faltan controles de auditoría real y de política MFA en el alta de usuarios.

**Estimación para producción:** 1–2 semanas para cerrar bloqueadores; 3–4
semanas para un go-live con hardening, staging y UAT.

---

## 2. Evidencia del estado actual

### 2.1 Configuración

Ejecutando `EET_ENVIRONMENT=prod` con el `.env.local` actual, la aplicación no
arranca y reporta:

1. `EET_COOKIE_SECURE` debe ser `true`.
2. `EET_PUBLIC_BASE_URL` debe comenzar con `https://`.
3. `EET_EMAIL_BACKEND` debe ser `smtp`.

Además, no se validan en producción: `EET_ADMIN_INTERNAL_ONLY`,
`EET_TRUSTED_PROXY_IPS`, credenciales SMTP ni fortaleza del password de la base.

### 2.2 Datos

| Dato | Valor actual | Acción para producción |
|---|---:|---|
| Usuarios | 8 (6 semilla + `demo.admin` + `demo.consulta`) | Base nueva; usuarios reales; eliminar demo |
| `demo.admin` | admin, `mfa_policy=disabled` | Eliminar o desactivar; nunca en producción |
| `demo.consulta` | consulta, `mfa_policy=disabled` | Eliminar o desactivar |
| Cruces | 60 | Base limpia o datos reales validados |
| Fichas QA/QC | 56 | Ídem |
| Correos en outbox | 336 (pruebas) | No migrar; base limpia |
| Auditoría | 943 eventos de prueba | Base limpia; conservar solo si es real |

`app/seed_data.py` ya usa un correo institucional para el administrador y
`persist.seed` **no se ejecuta** con `EET_ENVIRONMENT=prod`. La CLI
`seed-usuarios` sigue existiendo para desarrollo; no usarla en producción.

### 2.3 Despliegue actual

- `docker-compose.yml` publica `8000` y `5432` en todas las interfaces.
- PostgreSQL usa `eet/eet`.
- `deploy/docker-compose.staging.yml` publica `5434`, usa `eet/eet` y comparte
  `uploads/` con desarrollo.
- `deploy/nginx.conf` usa `Host $host` (pierde el puerto; puede romper CSRF en
  puertos no estándar).
- Dockerfile corre como root, no copia `scripts/`, usa tags flotantes y no
  declara `USER`/healthcheck de imagen.
- No hay compose de producción ni TLS.

### 2.4 Operación

- Backup probado manualmente y con restauración, pero **no programado** y sin
  destino externo definido.
- Sin monitoreo, alertas, métricas, trazas ni rotación de logs.
- CI `.github/workflows/ci.yml` creado, pero **no ejecutado** en GitHub ni
  conectado a un despliegue.
- Watchdog/keepalive funcionan para el entorno local, no sustituyen systemd en
  un servidor.

---

## 3. Matriz de readiness

| Área | Estado | Bloqueador |
|---|---|---|
| Funcionalidad EET | Parcial | Importador con casos límite; workflow de fichas/reemplazos sin unificar |
| Integridad de datos | Parcial | Restricciones 0004, transacción atómica e historial 0005; falta cobertura/plausibilidad |
| Autenticación | Parcial | Login/TOTP/correo/CSRF OK; alta de admin puede quedar sin TOTP; LDAP diferido |
| Auditoría | Parcial | Rutas de negocio registran actor fijo (`cargador`/`supervision`), no el usuario real |
| Seguridad de despliegue | No | HTTP, cookies inseguras, DB publicada, credenciales fijas, root |
| Base de datos | Parcial | PostGIS y migraciones OK; sin backup programado ni monitoreo |
| Backups | Parcial | Script probado; falta schedule, destino externo, cifrado y restore drill |
| Observabilidad | No | Sin logs centralizados, métricas, alertas ni readiness real |
| CI/CD | Parcial | Workflow creado; falta ejecutarlo, registry y deploy |
| Rendimiento | Desconocido | Sin prueba de carga con volúmenes reales |
| Documentación/runbook | Parcial | Muy buena documentación técnica; falta runbook de producción |
| Cumplimiento | Desconocido | Retención de datos, acceso, privacidad y auditoría a definir |

---

## 4. Bloqueadores P0 (antes de producción)

### B1. Configuración de producción

Definir un `deploy/.env.production` (600, fuera de git) o secret manager con:

```env
EET_ENVIRONMENT=prod
EET_AUTH_ENABLED=true
EET_COOKIE_SECURE=true
EET_SESSION_COOKIE=__Host-eet_session
EET_PUBLIC_BASE_URL=https://eet.<dominio-institucional>
EET_SECRET_KEY=<32+ caracteres aleatorios>
EET_AUTH_PEPPER=<16+ caracteres aleatorios>
EET_FERNET_KEYS=v2:<nueva>,v1:<anterior>
EET_DATABASE_URL=postgresql+psycopg://eet:<password-fuerte>@db:5432/eet
EET_EMAIL_BACKEND=smtp
EET_SMTP_HOST=<smtp institucional>
EET_SMTP_PORT=587
EET_SMTP_STARTTLS=true
EET_SMTP_USER=<usuario>
EET_SMTP_PASSWORD=<secreto>
EET_EMAIL_FROM=Validador EET <no-reply@<dominio>>
EET_ADMIN_INTERNAL_ONLY=true
EET_INTERNAL_CIDRS=<VPN/LAN administrativa>
EET_TRUSTED_PROXY_IPS=<IP/CIDR de nginx>
EET_EXPOSE_DOCS=false
EET_MAX_UPLOAD_MB=20
EET_LDAP_ENABLED=false
```

Eliminar `EET_DEMO_*`. Validar con `Settings()` en un contenedor antes de
desplegar. Rotar los secretos actuales si el `.env.local` se compartió.

### B2. TLS, nginx y red

- Dominio y certificado (Let's Encrypt o institucional), renovación automática.
- nginx solo 80/443; app y PostgreSQL en red interna, **sin publicar 5432**.
- `proxy_set_header Host $http_host;` y `X-Forwarded-Proto/For`.
- HSTS, TLS 1.2/1.3, ciphers, `server_tokens off`.
- Firewall: solo 80/443 y SSH restringido.
- `EET_TRUSTED_PROXY_IPS` con la IP de nginx para que rate limiting y auditoría
  vean la IP real.

### B3. Base de datos limpia y usuarios reales

- Crear base nueva; ejecutar `alembic upgrade head`; `alembic check`.
- No migrar datos demo ni correos de prueba.
- Crear administrador real con TOTP; entregar códigos de respaldo por canal
  seguro.
- Crear las cuentas reales; asignar roles y letras de terna.
- Eliminar/desactivar `demo.admin` y `demo.consulta`.
- La semilla ya usa correo institucional y no corre en producción; el
  administrador se crea con `bootstrap_admin.py`.
- Enforcar TOTP para `admin`/`supervision` también en el alta (hoy se puede
  elegir `disabled` explícitamente).

### B4. Backups programados y probados — implementado

- `pg_dump`/WAL + `uploads` a **NAS o disco externo**, cifrado.
- Cron/systemd timer diario; retención definida (ej. 30/90 días).
- Verificación de restauración mensual.
- Definir RPO/RTO (ej. RPO ≤ 1 h, RTO ≤ 4 h).
- Probar el restore antes del go-live.

### B5. Auditoría real y política MFA — implementado

- Pasar `actor=f"usuario:{request.state.usuario.id}"` en entregas, QA/QC,
  reemplazos, malla y mapa.
- Rechazar `mfa_policy=disabled` para roles críticos en alta y CLI, salvo
  break-glass auditado.
- Definir retención y protección de `auditoria`; evitar actor fijo.

### B6. Robustez del importador (mínimo productivo) — implementado

- IDs de punto decimales (`101.0 → 1010`) y `ID_EV` desconocidos.
- Fórmulas sin caché y hojas no reconocidas.
- Confirmar que un archivo con errores no se acepta (ya resuelto).
- Validar con un libro real de tamaño producción (puntos × 35 años).

### B7. Observabilidad y operación — implementado

- Logs con rotación (`max-size`/`max-file` o journald) y sin secretos.
- Monitoreo de `/salud`, latencia, 5xx, DB, disco, backup y certificado.
- Alertas a correo/Telegram.
- `/ready` que compruebe la base (hoy `/salud` no verifica DB).
- Runbook de incidentes, rollback y contactos.

### B8. CI/CD y staging — workflow publicado; staging pendiente

- Ejecutar el workflow en GitHub y corregir lo que falle.
- Construir imagen con tag inmutable y publicarla en un registry.
- Desplegar a staging con configuración tipo producción; UAT.
- Deploy a producción con aprobación manual, migraciones y smoke.
- Rollback: imagen anterior + backup/restore.

### B9. Hardening del host y del contenedor — contenedor aplicado; host pendiente

- Usuario no-root en la imagen, filesystem read-only salvo `uploads`/tmp,
  límites de CPU/memoria.
- OS actualizado, SSH con claves, fail2ban, NTP, Docker socket protegido.
- Imagen base y `uv` con versiones fijas; escaneo de vulnerabilidades.
- Antivirus/validación de archivos subidos si la política lo exige.

---

## 5. P1 (primeros 30 días)

- Unificar workflow `FichaQAQC` + `Reemplazo` y sincronizar el consolidado.
- Cobertura de entrega (`entrega_alcance`) y plausibilidad persistida.
- Evitar doble asignación de reservas y reservas ad-hoc.
- Exportaciones XLSX sin inyección de fórmulas.
- Corregir `return_to` con barra invertida y páginas fuera de rango.
- Pruebas de carga con datos reales; índices y N+1.
- Job en background para importaciones grandes.
- Métricas/alertas más completas y tablero operativo.
- LDAP/AD cuando TI lo habilite.
- Revisión de seguridad externa (SAST/DAST/ZAP) y dependencias.

---

## 6. Arquitectura objetivo de producción

```text
Internet
  │ 443/TLS
  ▼
nginx (host o contenedor)
  │ red interna
  ├── app FastAPI (8000, no publicado)
  └── PostgreSQL/PostGIS (5432, no publicado)
        └── volumen persistente
  └── uploads (volumen)
        └── backup diario a NAS/disco externo

Host único:
  - Docker Compose + systemd
  - firewall 80/443 + SSH restringido
  - monitorización y logs con rotación
  - CI/CD: build → registry → deploy con aprobación
```

No se propone alta disponibilidad en esta etapa (decisión: un servidor). Se
documenta el RTO/RPO y el procedimiento de restauración.

---

## 7. Checklist de go-live

### Antes

- [ ] `EET_ENVIRONMENT=prod` valida sin errores.
- [ ] TLS activo, HSTS, renovación automática.
- [ ] DB nueva migrada a `0005`; `alembic check` limpio.
- [ ] Usuarios reales; demo eliminados; admin con TOTP.
- [ ] SMTP real probado (reset y código MFA).
- [ ] Backups programados y restore probado.
- [ ] Monitoreo y alertas activos.
- [ ] CI ejecutado; imagen tagueada; staging con UAT.
- [ ] Auditoría con actor real.
- [ ] Importación de un libro real validada.
- [ ] Runbook y rollback documentados.

### Durante

- [ ] Ventana de mantenimiento y backup previo.
- [ ] Migraciones y arranque; `/salud`, `/health`, login.
- [ ] Smoke de roles, entrega, QA/QC, exportación y correo.
- [ ] Verificación de logs y métricas.

### Después

- [ ] Backup post-go-live.
- [ ] Monitoreo 24–72 h.
- [ ] Revisión de auditoría y errores.
- [ ] Retrospectiva y ajustes.

---

## 8. Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Pérdida de datos por backup no probado | Backup diario externo + restore mensual + RPO/RTO |
| Exposición de DB o app sin TLS | Firewall, red interna, nginx TLS, no publicar 5432 |
| Admin sin TOTP | Enforcar política en alta/CLI + auditoría |
| Actor de auditoría incorrecto | Pasar usuario real en todos los servicios |
| Datos demo en producción | Base nueva + revisión de usuarios/proyecto |
| Importación que pierde celdas | Validar IDs/fórmulas/hojas + UAT con libro real |
| Falla única del servidor | Backups + RTO documentado; HA en fase posterior |
| Correos no entregados | SMTP institucional + SPF/DKIM + prueba real |
| Certificado vencido | Renovación automática + alerta |
| Cambios sin trazabilidad | CI/CD + tags + auditoría + runbook |

---

## 9. Estimación

| Frente | Esfuerzo |
|---|---|
| Configuración prod + TLS/nginx + compose prod | 2–3 días |
| Base nueva, usuarios, TOTP, limpieza demo | 1–2 días |
| Backups + restore + monitoreo | 2–3 días |
| Auditoría real + MFA en alta + importador mínimo | 2–3 días |
| CI/CD + staging + UAT + runbook | 3–5 días |
| **Total** | **10–16 días hábiles** |

Con un backend + DevOps/QA parcial: **2–3 semanas**; con UAT y correcciones:
**3–4 semanas**.

---

## 10. B1–B3 implementados (2026-11-03)

Activos de producción creados:

- `deploy/docker-compose.prod.yml`: app y PostgreSQL **sin puertos
  publicados**, proxy nginx en 80/443, healthchecks, `restart`, logs con
  rotación y red interna `172.30.0.0/24`.
- `deploy/nginx.prod.conf`: TLS 1.2/1.3, HSTS, `Host $http_host`,
  `X-Forwarded-Proto/For`, `client_max_body_size 20m`.
- `deploy/.env.production.example` y `scripts/prepare-production.ps1`:
  generación de secretos, password de base URL-safe, SMTP y ACL del archivo.
- `scripts/preflight-production.ps1` + `scripts/check_production_config.py`:
  validan variables, ausencia de demo/placeholders, certificados, Compose y
  `Settings()` de producción sin imprimir secretos.
- `scripts/bootstrap_admin.py` + `scripts/bootstrap-admin.ps1`: crean el
  administrador inicial con TOTP obligatorio y cambio de contraseña.
- `Dockerfile` copia `scripts/` para preflight y bootstrap dentro de la imagen.
- `.gitignore` excluye `deploy/.env.production`.
- `persist.seed` no se ejecuta en `prod`; la semilla usa correo institucional y
  el administrador se crea explícitamente con TOTP.

Validación realizada con una configuración temporal:

```text
docker compose config      -> OK
Settings() producción      -> CONFIG_OK
nginx -t                   -> syntax is ok / test is successful
```

Los archivos temporales (env de prueba y certificados autofirmados) se
eliminaron. **Sigue siendo NO-GO** hasta cargar dominio/certificado real,
SMTP real, base limpia, administrador real, backups programados y monitoreo.

## 11. B4–B5 implementados (2026-11-03)

### Backups cifrados y programados

- `scripts/backup-local.sh`: dump `pg_dump -Fc` + `uploads`, verificación del
  dump, restauración en base temporal (`--verify`) y cifrado AES-256-CBC con
  PBKDF2 (`openssl enc`, 200 000 iteraciones) usando `--key-file`.
- Detección automática del contenedor (`eet-db-prod` en producción,
  `eet-mapa-aep` en desarrollo) y retención configurable (30 días por defecto).
- `scripts/backup.ps1` acepta `-KeyFile` y `-Container`.
- `deploy/systemd/eet-backup.service` + `.timer`: ejecución diaria 02:30 con
  `Persistent=true`, `UMask=0077` y destino externo `/mnt/backup/eet`.
- `scripts/register-backup.ps1`: alternativa en el Programador de tareas de
  Windows/WSL.
- Prueba real: 8 usuarios y 60 cruces restaurados; solo se generaron archivos
  `.enc`; clave y destino temporales eliminados.

### Auditoría y MFA

- `app/auth/audit.py`: `actor_de_request(request)` devuelve
  `usuario:<id>` o `sistema`.
- Entregas, QA/QC, reemplazos, malla y mapa pasan el actor real y la IP; la
  carga de una entrega registra `entrega_recibida` y `cargada_por_id`.
- `crear_usuario_admin` rechaza cualquier política distinta de `totp` para
  `admin` y `supervision`, aunque el formulario envíe `disabled`.
- Pruebas nuevas: actor/cargador en `crear_entrega_pendiente`, rechazo de MFA
  no-TOTP en el alta y una prueba HTTP de extremo a extremo que sube una
  entrega como `cargador` y verifica `cargada_por_id` y
  `auditoria.actor=usuario:<id>`.
- Suite completa: **291 unitarias + 275 integración = 566 pruebas**.

## 12. B6–B9 implementados (2026-11-04)

### B6 — Importador

- IDs decimales: `101.0`, `"101.0"` y `"101,0"` se leen como `101`; una
  fracción distinta de cero (`101.5`) se rechaza en lugar de concatenar
  dígitos.
- `ID_EV` numérico (`1.0`, `"3.0"`) se resuelve a A/B/C. Un `ID_EV`
  desconocido ya **no** se sustituye por el nombre de la hoja: se reporta como
  `EVALUADORA_DESCONOCIDA`.
- Fórmulas sin valor calculado (`data_only=True` devuelve `None`) se detectan
  abriendo una segunda vista del libro y se reportan como `FORMULA_SIN_CACHE`
  con la celda exacta; la fila no se importa como si estuviera vacía.
- Hojas con datos pero sin columna de punto/año o sin evaluadora se reportan
  como `HOJA_NO_RECONOCIDA`. Las hojas de portada/ayuda conocidas se ignoran.
- Un libro sin filas de cruce también se reporta (no se acepta en silencio).
- Pendiente de UAT: importar un libro real de tamaño producción.

### B7 — Observabilidad

- `/ready` comprueba `SELECT 1` contra la base y responde 503 sin filtrar
  detalles; `/salud` y `/health` siguen siendo liveness.
- `AccessLogMiddleware` registra método, ruta, estado, duración y
  `request_id` (sin cuerpos ni query strings); los 5xx quedan en WARNING.
- El healthcheck de producción usa `/ready`.
- `scripts/healthcheck-production.py` valida HTTP, base, espacio, antigüedad
  del backup y vencimiento del certificado; alerta opcional por Telegram.
- `deploy/systemd/eet-healthcheck.service` + `.timer` cada 5 minutos.

### B8 — CI/CD

- El workflow ahora publica la imagen en GHCR
  (`ghcr.io/<repo>:sha-<commit>` y `:latest`) en push a `master`, con caché de
  Buildx y `GITHUB_TOKEN`; en PR solo construye.
- El commit `0601890` se publicó en `master`; la ejecución debe verificarse en
  GitHub Actions (este entorno no tiene `gh` ni acceso a la API del repo).
- Falta configurar staging/despliegue con secretos del servidor y aprobación
  manual.

### B9 — Hardening del contenedor

- Imagen non-root `10001:10001`, filesystem de solo lectura, `tmpfs` para
  temporales, `cap_drop: ALL`, `no-new-privileges`.
- Límites de CPU/memoria/PIDs y `stop_grace_period` en app, base y proxy.
- Verificado que el contenedor arranca como UID 10001 con `/app` de solo
  lectura e importa la aplicación.
- El directorio `uploads` del host debe pertenecer a `10001:10001`
  (documentado en `deploy/README-production.md`).
- Pendiente en el host: SSH con claves, fail2ban, NTP, firewall, escaneo de
  imagen/dependencias y revisión externa.

## 13. Decisiones pendientes

1. Dominio y certificado TLS; IP del servidor.
2. Cuenta SMTP institucional y política SPF/DKIM.
3. Destino de backups (NAS, disco externo, otro servidor) y retención.
4. Administradores reales y entrega de TOTP/códigos de respaldo.
5. ¿Se migra alguna base existente o se arranca con base nueva?
6. Acceso remoto a administración: VPN/LAN o exposición controlada.
7. Momento de LDAP/AD.
8. Herramienta de monitoreo/alertas preferida.
9. Política de retención de datos, auditoría y archivos originales.
