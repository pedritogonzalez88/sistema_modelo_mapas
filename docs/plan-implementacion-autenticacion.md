# Plan de implementación — Autenticación, MFA y recuperación

> Complemento de `docs/analisis-autenticacion.md` (decisiones confirmadas en §11).
> Este documento es ejecutable: tareas atómicas, archivos, contratos, migraciones, pruebas y orden.

---

## 1. Objetivo

Proteger todas las rutas de negocio con identidad verificada y permisos por rol, incorporando:

- Login local usuario/contraseña (Argon2id).
- Proveedor LDAP/AD **parametrizado y deshabilitado** hasta que exista dominio.
- 2FA con TOTP (app autenticadora) y códigos por correo.
- Recuperación de contraseña por correo institucional.
- Sesiones revocables, CSRF, rate limiting y auditoría con actor real.
- Endurecimiento para exposición simultánea **internet + red interna**.

### Restricciones

1. No se publica la app sin autenticación: la bandera `EET_AUTH_ENABLED` arranca en `false`,
   pero el despliegue a internet exige ponerla en `true`.
2. No se almacenan contraseñas ni hashes de AD.
3. Sin IdP externo: todo se implementa en la app.
4. No se rompe el flujo actual mientras la bandera esté apagada (rollout gradual).
5. Toda pantalla nueva cumple el estándar ya logrado: WCAG 2.2 AA, teclado y axe en cero.

---

## 2. Decisiones aplicadas y su impacto

| Decisión | Impacto en la implementación |
|---|---|
| AD aún no existe | `LdapProvider` completo pero no registrado si `EET_LDAP_ENABLED=false`; pruebas con `ldap3` en modo mock; comando `ldap-check`; UI de admin muestra "Directorio: no configurado" |
| Correo institucional | SMTP autenticado, remitente del dominio, outbox con reintentos, plantillas sin enlaces rastreables; SPF/DKIM/DMARC como checklist de TI |
| Internet + interno | HTTPS/HSTS, cookies `Secure`, CSP con nonce, rate limit por IP y cuenta, admin restringido a CIDR internos, límite de subida, sin enumeración |
| 2FA por rol | `admin`/`supervision`: TOTP obligatorio; resto: TOTP o correo; enrolamiento forzado antes de operar |
| Alembic | Baseline del esquema actual + migraciones de auth; `init_db()` solo dev |
| Sin IdP | No se agrega OIDC; la interfaz `AuthProvider` queda preparada |

---

## 3. Arquitectura objetivo

```
Request
  │
  ├─ ProxyHeaders (solo IPs de confianza)         ← X-Forwarded-Proto/For
  ├─ SecurityHeaders (CSP nonce, HSTS, nosniff)
  ├─ AuthGate (si EET_AUTH_ENABLED)
  │    ├─ ruta pública → continúa
  │    ├─ cookie __Host-eet_session → hash → tabla sesiones
  │    │    ├─ válida y no expirada → request.state.usuario
  │    │    └─ inválida → 303 /auth/login?next=...
  │    └─ JSON/API → 401 JSON
  ├─ CSRF (POST/PUT/PATCH/DELETE)
  └─ Router → require_roles(...) → servicio → plantilla (usuario, csrf_token)
```

Componentes y responsabilidades:

| Componente | Responsabilidad |
|---|---|
| `core/security.py` | Argon2id, tokens, hashes, Fernet multi-clave, TOTP, comparación constante |
| `auth/providers.py` | `LocalProvider`, `LdapProvider` (interfaz común) |
| `auth/service.py` | Login, 2FA, sesiones, recuperación, rate limit, enrolamiento |
| `auth/dependencies.py` | `get_current_user`, `require_roles`, `require_mfa_reciente` |
| `auth/csrf.py` | Token por sesión + validación de origen |
| `auth/mailer.py` | SMTP / consola / archivo + outbox |
| `auth/router.py` | Endpoints `/auth/*` |
| `auth/middleware.py` | AuthGate, headers, contexto de usuario |
| `app/cli.py` | `crear-admin`, `ldap-check`, `limpiar-sesiones`, `rehash` |

---

## 4. Fases y tareas

### Fase A0 — Preparación (2 días)

| ID | Tarea | Archivos | Verificación |
|---|---|---|---|
| A0.1 | Agregar dependencias | `pyproject.toml` | `uv sync` |
| A0.2 | Extender settings con auth/email/LDAP, validadores y defaults seguros | `app/core/config.py` | Import con `auth_enabled=false` no exige secretos; con `true` falla si falta `secret_key` |
| A0.3 | Crear `app/core/security.py` con Argon2id, tokens, Fernet, TOTP | nuevo | Pruebas unitarias |
| A0.4 | Introducir Alembic | `alembic.ini`, `migrations/env.py` | `alembic upgrade head` sobre BD existente |
| A0.5 | Baseline del esquema actual | `migrations/versions/0001_baseline.py` | `alembic stamp head` en la BD actual; `upgrade` no cambia nada |
| A0.6 | Migración de auth | `0002_auth.py` | Tablas/columnas creadas; reversible |
| A0.7 | Comando de arranque con migraciones | `Dockerfile`, `scripts/entrypoint.sh` | Contenedor aplica `alembic upgrade head` antes de uvicorn |

Dependencias:

```toml
argon2-cffi>=23.1
pyotp>=2.9
segno>=1.6
cryptography>=42
ldap3>=2.9
aiosmtplib>=3.0
email-validator>=2.1
alembic>=1.13
```

Configuración nueva (borrador; ver anexo C del análisis):

```python
class Settings(BaseSettings):
    auth_enabled: bool = False
    secret_key: str = ""              # Fernet, base64
    auth_pepper: str = ""
    session_cookie: str = "__Host-eet_session"
    session_absolute_min: int = 720
    session_idle_min: int = 30
    cookie_secure: bool = True
    login_max_intentos: int = 5
    login_bloqueo_min: int = 15
    login_ip_max_15min: int = 20
    mfa_issuer: str = "Validador EET (AEP)"
    mfa_window: int = 1
    email_backend: str = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str = "Validador EET <no-reply@localhost>"
    public_base_url: str = "http://localhost:8000"
    ldap_enabled: bool = False
    ldap_url: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_base_dn: str | None = None
    ldap_user_filter: str = "(sAMAccountName={login})"
    ldap_role_map: str = "{}"          # JSON grupo→rol
    ldap_timeout: int = 5
    admin_internal_only: bool = True
    internal_cidrs: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"
    max_upload_mb: int = 20
    expose_docs: bool = False
```

### Fase A1 — Sesión, login local y CSRF (5 días)

| ID | Tarea | Archivos |
|---|---|---|
| A1.1 | Extender `Usuario` y crear modelos de auth | `app/models/__init__.py` |
| A1.2 | `SessionService`: crear, cargar, tocar, revocar, revocar todas | `app/auth/service.py` |
| A1.3 | `LocalProvider` con Argon2id y verificación de usuario dummy para igualar tiempos | `app/auth/providers.py` |
| A1.4 | `AuthGateMiddleware` + `get_current_user` | `app/auth/middleware.py`, `dependencies.py` |
| A1.5 | Login/logout con rotación de sesión y `return_to` validado | `app/auth/router.py`, `templates/auth/login.html` |
| A1.6 | CSRF: token por sesión, global Jinja, middleware y barrido de formularios | `app/auth/csrf.py`, todas las plantillas con POST |
| A1.7 | Menú de usuario en el shell (nombre, rol, seguridad, salir) | `components/shell.html` |
| A1.8 | Auditoría de login (ok/fail/lock) con IP y agente | `app/services/persist.py` o `auth/service.py` |
| A1.9 | `create_app()` para pruebas y montaje condicional de middleware | `app/main.py` |
| A1.10 | CLI `crear-admin` y `limpiar-sesiones` | `app/cli.py` |

Detalles:

- Cookie `__Host-eet_session`: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, sin `Domain`.
- Expiración absoluta 12 h; inactividad 30 min; `last_seen_at` se actualiza como máximo 1/min.
- Al autenticar se **rota** el token y se elimina la sesión previa (evita fijación).
- Usuario inexistente: se verifica contra un hash Argon2id fijo para que el tiempo sea similar.
- Todo POST sin CSRF válido → 403 HTML, nunca JSON.
- Mientras `auth_enabled=false`, el middleware no interviene (rollout seguro).

CSRF en plantillas:

```jinja
{# components/ui.html #}
{% macro csrf_input() -%}
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
{%- endmacro %}
```

```html
<form method="post" action="/qaqc/{{ ficha.id }}">
  {{ ui.csrf_input() }}
  ...
</form>
```

### Fase A2 — Autorización por roles (3 días)

> Entregada: ver `docs/fase-a2-permisos-por-rol.md` (matriz aplicada, alcance del
> asistente, navegación condicionada y `tests/test_auth_roles.py`).

| ID | Tarea |
|---|---|
| A2.1 | Definir matriz de permisos en `app/auth/permissions.py` (única fuente de verdad) |
| A2.2 | `require_roles(*roles)` y `require_mfa_reciente(min)` |
| A2.3 | Aplicar guardas a los 8 routers y a cada acción sensible |
| A2.4 | Alcance de datos: `asistente` solo ve sus entregas (`letra_terna`), 403 en el resto |
| A2.5 | Helper `puede(...)` para ocultar navegación y botones |
| A2.6 | Página 403 HTML con explicación y enlace de vuelta |
| A2.7 | Admin: CRUD de usuarios, cambio de rol, baja lógica, reset asistido |
| A2.8 | Restricción de admin/auditoría a CIDR internos si `admin_internal_only` |
| A2.9 | Pruebas de autorización por rol y por recurso |

Matriz (resumen; la completa está en el análisis §6.4):

```python
PERMISOS = {
    "dashboard.ver":     {"admin", "supervision", "cargador", "asistente", "consulta"},
    "entregas.ver":      {"admin", "supervision", "cargador", "consulta"},
    "entregas.propias":  {"asistente"},
    "entregas.gestionar": {"supervision", "cargador"},
    "cruces.ver":        {"supervision", "cargador", "consulta"},
    "qaqc.ver":          {"supervision", "cargador", "consulta"},
    "qaqc.decidir":      {"supervision"},
    "reemplazos.proponer": {"supervision", "cargador"},
    "reemplazos.autorizar": {"supervision"},
    "malla.cargar":      {"supervision", "cargador"},
    "mapa.cargar":       {"supervision", "cargador"},
    "exportar":          {"supervision", "consulta"},
    "usuarios.gestionar": {"admin"},
    "auditoria.ver":     {"admin", "supervision"},
}
```

> `admin` no dictamina fichas ni autoriza reemplazos: separación de deberes explícita.

### Fase A3 — TOTP y códigos de respaldo (3 días)

> Entregada: ver `docs/fase-a3-totp.md` (esquema 0003, enrolamiento, challenge,
> política por rol, decisiones y pruebas). No incluye código por correo (A4).

| ID | Tarea |
|---|---|
| A3.1 | Modelos `mfa_totp` y `recovery_codes` (ya en migración) |
| A3.2 | Enrolamiento: secreto 160 bits, cifrado, QR SVG (`segno`), clave manual, `Cache-Control: no-store` |
| A3.3 | Confirmación con ventana ±1 y `last_used_step` (antirreplay) |
| A3.4 | 10 códigos de respaldo, hasheados, mostrados una sola vez, descargables |
| A3.5 | Challenge de login: cookie firmada 5 min o fila `auth_tokens` purpose=`mfa` |
| A3.6 | Pantalla `/auth/seguridad`: estado, cambiar autenticador, regenerar códigos, sesiones activas |
| A3.7 | Política: si el rol exige TOTP y no está enrolado, solo se permite `/auth/seguridad` y logout |
| A3.8 | Pruebas: código válido, expirado, reutilizado, ±1, respaldo de un solo uso |

Verificación TOTP:

```python
def verificar_totp(secret: str, code: str, last_step: int | None, window: int = 1) -> int | None:
    totp = pyotp.TOTP(secret)
    now = int(time.time()) // 30
    for step in range(now - window, now + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.at(step * 30), code):
            return step
    return None
```

### Fase A4 — Correo, OTP y recuperación (4 días)

> Entregada: ver `docs/fase-a4-correo-recuperacion.md` (mailer, outbox cifrado,
> código por correo, recuperación neutra, notificaciones y pruebas).

| ID | Tarea |
|---|---|
| A4.1 | `Mailer` con backends `console`, `file`, `smtp` (`aiosmtplib`, STARTTLS, timeout 10 s) |
| A4.2 | `email_outbox` con reintentos y estado; job de reenvío |
| A4.3 | Código por correo: 6 dígitos, HMAC con pepper, 10 min, 5 intentos, un solo uso |
| A4.4 | `/auth/recuperar` con respuesta neutra y rate limit |
| A4.5 | `/auth/restablecer`: token 32 bytes hasheado, 30 min, un solo uso, revoca sesiones, notifica |
| A4.6 | Plantillas de correo (texto + HTML mínimo) sin imágenes ni enlaces de seguimiento |
| A4.7 | Aviso de cambios de contraseña y de enrolamiento 2FA |
| A4.8 | Pruebas: expiración, reutilización, enumeración, envío fallido y reintento |

Reglas del correo:

- Remitente fijo por configuración; nunca se interpola entrada del usuario en cabeceras.
- Asunto y cuerpo en español, con hora y origen; "no compartas este código".
- El enlace usa `EET_PUBLIC_BASE_URL` (https en producción).
- Si el envío falla, el flujo de login por correo muestra error controlado y sugiere TOTP/respaldo.

### Fase A5 — LDAP/AD parametrizado (3 días)

| ID | Tarea |
|---|---|
| A5.1 | `LdapProvider` con `ldap3`, LDAPS/StartTLS, validación de certificado, timeout |
| A5.2 | Búsqueda con cuenta de servicio + segundo bind con el usuario |
| A5.3 | Mapeo de grupos→rol y prioridad; `role_source` manual/ldap |
| A5.4 | Sincronización de nombre/email en cada login; nunca de contraseña |
| A5.5 | Registro condicional: si `ldap_enabled=false`, no se instancia ni se toca la red |
| A5.6 | `python -m app.cli ldap-check` con diagnóstico legible (conexión, bind, búsqueda, grupos) |
| A5.7 | Pruebas con `ldap3.MOCK_SYNC`: válido, inválido, grupo, DC caído, timeout |
| A5.8 | UI admin: estado de configuración del directorio (sin exponer credenciales) |
| A5.9 | Break-glass: admin local excluido de LDAP, TOTP obligatorio, auditado |

> Aunque no haya AD hoy, el código queda listo y probado contra un servidor simulado.
> Activar en el futuro = completar variables + `EET_LDAP_ENABLED=true` + reiniciar.

### Fase A6 — Endurecimiento, E2E y despliegue (4 días)

| ID | Tarea |
|---|---|
| A6.1 | Rate limiting en login, 2FA y recuperación (por IP y por cuenta), con bloqueo temporal |
| A6.2 | Headers: CSP con nonce, HSTS, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `frame-ancestors 'none'` |
| A6.3 | Mover JS inline a `app.js` o nonce por request (hoy hay scripts inline) |
| A6.4 | Límite de subida (`max_upload_mb`) y rechazo temprano por `Content-Length` |
| A6.5 | Ocultar `/docs` y `/openapi.json` en producción (`expose_docs=false`) |
| A6.6 | Proxy de confianza: `X-Forwarded-Proto/For` solo desde el proxy; `--proxy-headers` |
| A6.7 | Gestión de sesiones activas y revocación desde UI |
| A6.8 | Vista de auditoría con filtros y exportación |
| A6.9 | E2E Playwright: login, 2FA, recuperación, bloqueo, roles |
| A6.10 | Axe sobre 6 pantallas nuevas (objetivo: 0 violaciones) |
| A6.11 | Runbook: rotación de claves, reset asistido, caída de SMTP/DC, restauración |
| A6.12 | Ensayo de despliegue con `auth_enabled=true` en staging |

---

## 5. Migraciones (Alembic)

```powershell
uv run alembic init migrations
# editar migrations/env.py:
#   from app.models import Base
#   from app.core.config import settings
#   config.set_main_option("sqlalchemy.url", settings.database_url)
#   target_metadata = Base.metadata
#   include_object: excluir spatial_ref_sys y tablas de PostGIS
uv run alembic revision --autogenerate -m "baseline"
uv run alembic stamp head          # BD existente ya tiene el esquema
uv run alembic revision --autogenerate -m "auth"
uv run alembic upgrade head
```

Notas:
- El baseline debe ignorar `spatial_ref_sys`, índices GIST y tablas internas de PostGIS.
- `init_db()` queda para desarrollo/tests; en producción el arranque ejecuta `alembic upgrade head`.
- Toda migración debe ser reversible (`downgrade` probado).

Tablas/columnas: ver análisis §6.2. Índices obligatorios:
`sesiones(token_hash)`, `sesiones(usuario_id, revoked_at)`, `auth_tokens(token_hash)`,
`login_attempts(identificador, created_at)`, `login_attempts(ip, created_at)`,
`mfa_email_codes(usuario_id, purpose, consumed_at)`.

---

## 6. Seguridad para exposición a internet

Checklist de despliegue (además del código):

- [ ] Reverse proxy con TLS 1.2+ y redirección 80→443 (Nginx/Caddy/IIS).
- [ ] `EET_COOKIE_SECURE=true`, HSTS activo, certificado válido.
- [ ] `EET_ADMIN_INTERNAL_ONLY=true` y CIDR internos correctos.
- [ ] Firewall: solo 80/443 al proxy; base de datos y app sin exposición directa.
- [ ] SMTP con credenciales en secreto; SPF/DKIM/DMARC del dominio.
- [ ] Backups de la BD y del archivo de claves; restauración probada.
- [ ] Alertas de picos de `login_fail`, `mfa_fail`, `password_reset_requested`.
- [ ] Revisión de dependencias y de logs sin secretos.
- [ ] WAF/rate limit de borde (opcional pero recomendado) contra credential stuffing.

Nginx (esencial):

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 20m;
}
```

Uvicorn debe arrancar con `--proxy-headers --forwarded-allow-ips=<IP del proxy>`; el middleware
solo confía en esos orígenes, nunca en cabeceras de clientes directos.

Rate limiting (sin Redis):

```python
def limitar_login(db, identificador: str, ip: str) -> None:
    desde = datetime.utcnow() - timedelta(minutes=15)
    fallos_cuenta = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.identificador == identificador.lower(),
        LoginAttempt.exito.is_(False), LoginAttempt.created_at >= desde)) or 0
    fallos_ip = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.ip == ip, LoginAttempt.exito.is_(False),
        LoginAttempt.created_at >= desde)) or 0
    if fallos_cuenta >= settings.login_max_intentos or fallos_ip >= settings.login_ip_max_15min:
        raise RateLimitExcedido()
```

Mensaje al usuario: "Demasiados intentos. Esperá 15 minutos o recuperá tu acceso." Sin revelar
si la cuenta existe.

---

## 7. UX de las pantallas (resumen de implementación)

| Ruta | Plantilla | Puntos clave |
|---|---|---|
| `/auth/login` | `auth/login.html` | usuario, contraseña con mostrar/ocultar, error genérico, `autocomplete`, foco inicial, enlace de recuperación |
| `/auth/mfa` | `auth/mfa.html` | 6 dígitos, `inputmode=numeric`, `autocomplete=one-time-code`, pegar, reenviar con cuenta atrás, "usar otro método" |
| `/auth/recuperar` | `auth/recuperar.html` | mensaje neutro siempre, instrucciones del siguiente paso |
| `/auth/restablecer` | `auth/restablecer.html` | requisitos visibles, confirmación, token inválido con CTA |
| `/auth/seguridad` | `auth/seguridad.html` | estado 2FA, QR, clave manual, códigos de respaldo, sesiones activas |
| `/auth/usuarios` | `auth/usuarios.html` | tabla, alta, rol, estado, último acceso, reset asistido |
| `/auth/auditoria` | `auth/auditoria.html` | filtros por evento/usuario/fecha, exportación |

Reutilizar macros (`field`, `banner`, `btn`, `badge`, `table_wrap`, `pagination`). El layout de
autenticación es una variante sin sidebar (`auth/base_auth.html`), centrada y accesible.

---

## 8. Pruebas

| Archivo | Cobertura |
|---|---|
| `tests/test_security.py` | Argon2id, rehash, tokens, Fernet multi-clave, HMAC de códigos, TOTP ±1/replay |
| `tests/test_auth_login.py` | login ok/fail, tiempos, bloqueo, rotación, logout, `return_to` |
| `tests/test_auth_csrf.py` | token válido/ausente/ajeno, `Origin` distinto, 403 HTML |
| `tests/test_auth_mfa.py` | enrolamiento, TOTP, respaldo, expiración, intentos, política por rol |
| `tests/test_auth_recovery.py` | solicitud neutra, token único, expiración, revocación de sesiones, email console |
| `tests/test_auth_roles.py` | matriz completa: cada rol × cada ruta sensible (403/200) |
| `tests/test_auth_ldap.py` | `ldap3.MOCK_SYNC`: válido, inválido, grupos, DC caído |
| `tests/test_auth_audit.py` | eventos, actor, IP, ausencia de secretos en logs |
| `tests/e2e/test_auth_flow.py` | Playwright: login + 2FA + recuperación + teclado + axe |

Regla: `auth_enabled=true` en las pruebas de integración de auth; el resto de la suite sigue con
la bandera apagada para no reescribir los 237 tests existentes (129 unit + 108 integración).

---

## 9. Rollout

1. **Desarrollo** (`auth_enabled=false`): se construyen y prueban las piezas.
2. **Staging** (`auth_enabled=true`, SMTP console): se valida el flujo completo y el runbook.
3. **Bootstrap**: `python -m app.cli crear-admin` con email real y TOTP obligatorio.
4. **Piloto**: supervisión + cargador (roles críticos), una semana.
5. **Producción**: activar para todos; comunicación y soporte.
6. **Post-despliegue**: revisar auditoría, fallos de login, entregas de correo y latencia.

Backfill: completar username/email de los 5 usuarios semilla; el resto se crea desde admin.
Los usuarios existentes reciben contraseña temporal y `must_change_password=true`.

---

## 10. Cronograma y commits

| Fase | Días | Hito |
|---|---|---|
| A0 | 2 | Alembic + settings + crypto |
| A1 | 5 | Login local, sesiones, CSRF |
| A2 | 3 | Permisos efectivos |
| A3 | 3 | TOTP + respaldo |
| A4 | 4 | Correo + recuperación |
| A5 | 3 | LDAP listo y apagado |
| A6 | 4 | Hardening + E2E + runbook |
| **Total** | **24** | |

Commits sugeridos:

```
build(auth): dependencias, settings y modulo de seguridad
feat(db): alembic baseline y migracion de autenticacion
feat(auth): sesiones, login local y logout
feat(auth): csrf en formularios y headers de seguridad
feat(auth): autorizacion por roles y alcance de datos
feat(auth): totp, codigos de respaldo y politica por rol
feat(auth): correo, otp y recuperacion de contrasena
feat(auth): proveedor ldap parametrizado y ldap-check
feat(auth): rate limiting, csp con nonce y limite de subida
test(auth): unitarias, integracion, ldap mock y e2e
docs(auth): runbook, politicas y guia de despliegue
```

Cada commit deja la suite en verde y la app funcionando con la bandera apagada.

---

## 11. Riesgos específicos

| Riesgo | Mitigación |
|---|---|
| Bloqueo del único admin | Dos admins mínimo; break-glass documentado |
| Correo institucional cae en spam | SPF/DKIM/DMARC, relay autenticado, probar con Outlook/Gmail |
| TLS mal terminado | Cookies `Secure` solo con HTTPS; verificación con `curl -I` y `openssl s_client` |
| CSP rompe la UI | Mover inline a `app.js`; probar en staging con `report-only` primero |
| Migración sobre datos vivos | Baseline + `stamp` + migración reversible; backup previo |
| Costo de Argon2id en el contenedor | Ajustar memoria (64 MB) al límite; medir login |
| Usuarios externos sin correo | Alta obligatoria con email verificado; alternativa de reset asistido |
| AD futuro con grupos distintos | Mapa de roles en configuración, no en código |
| Fuga de secretos en logs | Filtro de logging y pruebas que inspeccionan salida |

---

## 12. Criterios de aceptación

- [ ] Con `auth_enabled=true`, toda ruta de negocio exige sesión; `/salud` y `/static` siguen públicos.
- [ ] Login correcto/incorrecto no revela existencia de cuenta ni difiere en tiempo de forma medible.
- [ ] 5 fallos bloquean temporalmente la cuenta/IP y quedan auditados.
- [ ] CSRF ausente o inválido devuelve 403 HTML en todos los POST.
- [ ] TOTP rechaza reutilización y acepta ±1 paso; los códigos de respaldo son de un solo uso.
- [x] Código por correo expira en 10 min y admite 5 intentos.
- [x] Reset por correo funciona una vez, expira en 30 min y revoca todas las sesiones.
- [x] Un `asistente` no puede ver entregas de otro ni acceder a Cruces/QA/QC/Exportar (403).
- [x] `admin` no puede dictaminar fichas ni autorizar reemplazos.
- [ ] LDAP deshabilitado no abre conexiones; con mock funciona el mapeo de grupos y el DC caído falla cerrado.
- [ ] Cookies `HttpOnly`+`Secure`+`SameSite=Lax`; HSTS y CSP activos en producción.
- [ ] Auditoría registra actor real, IP, resultado y motivo en acciones sensibles.
- [ ] Ningún log/respuesta/tabla expone contraseñas, códigos, tokens ni secretos TOTP.
- [x] Suite completa en verde (existentes + nuevas) y axe en cero en las pantallas de auth.

---

## 13. Estado de ejecución (30/10/2026)

Ejecución del cierre de las fases A6.9 (E2E) y A6.10 (axe) sobre el código del
repositorio, con la app en `uvicorn :8033` y la base de datos del contenedor.
Informe técnico completo: [`docs/qa-visual/resumen-auth.md`](qa-visual/resumen-auth.md).

### 13.1 Fases A0–A6

| Fase | Estado | Evidencia |
|---|---|---|
| A0 — Preparación (dependencias, settings, seguridad, Alembic) | Completada | `app/core/config.py`, `app/core/security.py`, `migrations/`, arranque con `alembic upgrade head` |
| A1 — Sesión, login local y CSRF | Completada | `app/auth/middleware.py`, `app/auth/csrf.py`, `docs/fase-a1-sesiones-login-csrf.md` |
| A2 — Autorización por roles | Completada | `app/auth/permissions.py`, `app/auth/dependencies.py`, `docs/fase-a2-permisos-por-rol.md` |
| A3 — TOTP y códigos de respaldo | Completada | `app/auth/service.py`, `docs/fase-a3-totp.md`; E2E pasos 3–5 |
| A4 — Correo, OTP y recuperación | Completada | `app/auth/mailer.py`, `docs/fase-a4-correo-recuperacion.md`; E2E pasos 7–8 |
| A5 — LDAP/AD parametrizado (apagado) | Completada, sin directorio real | `app/auth/providers.py`, `uv run python -m app.cli ldap-check`, `docs/fase-a5-ldap.md` |
| A6 — Endurecimiento, administración, E2E y despliegue | Completada (A6.12 cerrada en A6c) | `docs/fase-a6a-endurecimiento.md`, `docs/fase-a6b-administracion.md`, `docs/runbook-autenticacion.md`, `docs/fase-a6c-staging.md` |

### 13.2 A6.9 — E2E de Playwright

`scripts/e2e-auth.mjs` (Chrome 156 headless, sin dependencias nuevas; TOTP
calculado con `node:crypto` en `scripts/totp.mjs`). **10/10 pasos en verde**:

login con error genérico y sin sesión · enrolamiento obligatorio ·
clave manual del QR + TOTP + 10 códigos de respaldo · segundo factor por TOTP
hasta `/` · código de respaldo de un solo uso (y rechazo al repetirlo) · alta,
cambio de rol, baja/alta, reset de contraseña y de 2FA · auditoría con CSV
`text/csv; charset=utf-8` y BOM · recuperación completa por correo (enlace del
`.eml` y código por correo) · `403` del admin en `/cruces` con el menú sin esa
sección · cierre de sesión.

Evidencia: 18 capturas, `report.json`, `resumen.md` y el CSV descargado en
`docs/qa-visual/e2e-auth/`.

### 13.3 A6.10 — axe en las pantallas de auth

`scripts/a11y-auth.mjs` sobre `/auth/login`, `/auth/recuperar`,
`/auth/restablecer?token=invalido`, `/auth/seguridad` (tres estados),
`/auth/mfa` (challenge pendiente), `/auth/usuarios` y `/auth/auditoria`, a
**1440 y 390 px**: **16 auditorías, 0 violaciones** `critical`/`serious`/
`moderate`/`minor`.

Antes de las correcciones había **5 reglas / 6 nodos**: `color-contrast`
`serious` en el pie de las tres pantallas anónimas (4.41:1) y `landmark-unique`
`moderate` en `/auth/usuarios` y `/auth/auditoria`. Se corrigieron en CSS y
plantillas (§8 de `docs/qa-accesibilidad.md`). Teclado en `/auth/login`,
`/auth/mfa` y `/auth/usuarios`: 6 recorridos sin trampas, sin elementos ocultos
enfocados y con `Enter` disparando el envío.

### 13.4 Desviaciones

1. **Defecto bloqueante corregido fuera del alcance previsto.** El E2E descubrió
   que `Referrer-Policy: no-referrer` hace que el navegador envíe
   `Origin: null` en todos los POST, y la validación CSRF lo rechazaba: **ningún
   formulario funcionaba en un navegador real** (la suite no lo veía porque
   `TestClient` no envía la cabecera). Se ajustó `_origen_valido` en
   `app/auth/csrf.py` para aceptar el origen opaco solo con
   `Sec-Fetch-Site: same-origin` (o sin la cabecera) y se añadieron dos pruebas
   de integración. No cambia el control principal (token CSRF).
2. `EET_PUBLIC_BASE_URL` y `EET_LOGIN_MAX_INTENTOS` se ajustaron **solo en el
   entorno de la corrida** (enlaces del correo a `:8033`; umbral 10 para que el
   propio E2E no bloqueara la cuenta al provocar fallos).
3. La variante `?metodo=email` de `/auth/mfa` se ejercita en el E2E pero la
   captura axe corresponde a la variante TOTP.
4. `/auth/cambiar-password` no formaba parte del alcance de A6.10.

### 13.5 Pendientes reales

- ~~**A6.12**: ensayo de despliegue con `auth_enabled=true` en staging, con TLS
  real y proxy (`EET_TRUSTED_PROXY_IPS`), y verificación de cookies `__Host-`.~~
  **Completada** (`docs/fase-a6c-staging.md`): preflight 20 PASS / 2 WARN /
  0 FAIL y smoke 15/15 con cookie `__Host-eet_session` (`Secure`, `HttpOnly`,
  `SameSite=lax`, sin `Domain`). Quedan como requisitos de TI el SMTP real, el
  DNS/certificado público, los CIDR definitivos y el firewall de borde.
- **Lector de pantalla** (NVDA/Firefox, VoiceOver/Safari) sobre las pantallas de
  auth: anuncios de `role="status"`/`role="alert"`, clave manual del
  autenticador y códigos de respaldo.
- **Revisión visual humana** de los estados (que el color no sea el único
  portador del significado).
- LDAP con un directorio real: hoy solo está probado con `ldap3.MOCK_SYNC`.
- Backfill documentado de `username`/`email` de las cuentas semilla (ids 1–5)
  antes de activar la bandera en producción.
