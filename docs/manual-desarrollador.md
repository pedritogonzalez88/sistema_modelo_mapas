# Manual de desarrollador — Validador EET

## 1. Visión general

Monolito por capas en FastAPI + PostgreSQL/PostGIS. Renderizado server-side
(Jinja2) con HTMX; no hay API JSON formal. Autenticación con sesiones,
CSRF, MFA (TOTP/correo), recuperación y LDAP opcional.

## 2. Arquitectura

Capas (dependencias verificadas por `import-linter`):

```
app/domain      -> puro, sin infraestructura
app/core        -> config, db, security, middleware
app/services    -> persistencia y lógica de negocio
app/auth        -> autenticación/autorización
app/routers     -> controladores HTTP
app/templates   -> vistas Jinja2
```

- `app/domain/qaqc.py`: reglas puras de consenso, matriz de confusión, Cohen.
- `app/services/persist.py`: fachada que delega en módulos (`bootstrap`,
  `common`, `entregas`, `cruces`, `malla`, `reemplazos`, `qaqc`, `dashboard`).
- `app/auth/service.py` y `app/auth/providers.py`: fachadas de compatibilidad.
- `app/seed_data.py`: tabla pura de las seis cuentas nominales (sin BD).

## 3. Estructura del repositorio

```
app/            código de la aplicación
migrations/     Alembic (0001..0005)
tests/          unitarias (sin marca) + integración (marcadas)
scripts/        utilidades de operación, seeds, smoke
         compose prod/staging/http, nginx, systemd
docs/           documentación
```

## 4. Entorno local

Requisitos: Windows + WSL2 (Ubuntu) + Docker + uv.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1
```

Levanta PostGIS + app, siembra datos demo, ejecuta 319 unit + 286 integración
y smoke HTTP. App en `http://localhost:8000`.

Credenciales demo: `demo.admin` / `demo.consulta` (passwords en
`.env.local`, gitignored).

Arranque sin tests:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1 -NoBrowser
```

## 5. Base de datos y migraciones

- Desarrollo: `init_db()` (create_all) en lifespan.
- Producción/staging: Alembic (`alembic upgrade head` en el arranque).
- Nueva migración: `uv run alembic revision --autogenerate -m "..."`.
- Verificar: `uv run alembic check`.

## 6. Pruebas y calidad

```bash
uv run pytest -q -p no:warnings                      # unitarias
uv run pytest -m integration -o addopts= -q -p no:warnings  # integración
uv run ruff check app scripts tests migrations        # lint completo
uv run mypy                                          # domain/core/services
uv run lint-imports                                  # reglas de arquitectura
```

Cobertura mínima 80% (CI). Ruff ignora `B008`, `BLE001`, `DTZ003`, `ISC004`
(justificados en `pyproject.toml`).

## 7. Autenticación y autorización

- Matriz única en `app/auth/permissions.py` (rol → permisos).
- Routers usan `require_permiso(...)`.
- Plantillas usan el global Jinja `puede(...)`.
- Roles críticos (`admin`, `supervision`) exigen TOTP.
- Con `EET_AUTH_ENABLED=false` todo se permite (rollout gradual).

Añadir un permiso: declararlo en `PERMISOS`, aplicarlo en el router y (si
aplica) en la plantilla.

## 8. Desarrollo de funcionalidades

1. Dominio: añadir reglas puras en `app/domain/`.
2. Servicio: añadir módulo en `app/services/` y exponerlo en `persist.py`.
3. Router: crear `app/routers/<modulo>.py` con `require_permiso`.
4. Registrar el router en `app/main.py`.
5. Plantilla en `app/templates/`.
6. Migración si cambia el modelo.
7. Pruebas unitarias + integración.

## 9. Despliegue

Ver `deploy-production.md`. Resumen:

```bash
# producción
docker compose --env-file .env.production \
  -f docker-compose.prod.yml up -d --build
# admin inicial
EET_SUPER_ADMIN_USERNAME=admin ... .venv/bin/python scripts/seed_super_admin.py
```

Modos: TLS propio (nginx del compose), TLS externo (`--http-only`) y Easypanel
(App/Dockerfile).

## 10. Convenciones

- Python 3.12, type hints modernos (`X | None`).
- Ruff + mypy + import-linter en CI.
- No secretos en el repositorio (`.env.*` gitignored).
- Migraciones con Alembic; no `create_all` en producción.
- Auditoría con actor real (`usuario:<id>`).

## 11. Troubleshooting

- **App no arranca**: revisar `docker compose ... logs app` y migraciones.
- **403 inesperado**: revisar matriz de permisos y rol del usuario.
- **MFA**: códigos TOTP requieren reloj sincronizado (NTP).
- **Uploads**: directorio debe pertenecer a UID 10001.
- **Tests en prod**: no ejecutar la suite con `EET_ENVIRONMENT=prod` sin
  configurar cookies Secure, HTTPS y SMTP.
