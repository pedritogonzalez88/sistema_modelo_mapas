# Fase A1 — Sesiones, login local y CSRF

> Implementación de la Fase A1 de `docs/plan-implementacion-autenticacion.md`.
> No incluye TOTP (A3), correo (A4), LDAP (A5) ni la matriz de permisos por rol
> (A2): con `EET_AUTH_ENABLED=true` el gate solo exige **sesión válida**.

---

## 1. Alcance entregado

| Pieza | Archivo |
|---|---|
| Sesiones, login, bloqueo y cambio de contraseña | `app/auth/service.py` |
| `AuthProvider` / `LocalProvider` (Argon2id) | `app/auth/providers.py` |
| Token CSRF y validación de origen | `app/auth/csrf.py` |
| `get_current_user` / `require_roles` | `app/auth/dependencies.py` |
| `AuthGateMiddleware` / `SecurityHeadersMiddleware` | `app/auth/middleware.py` |
| Auditoría con actor real | `app/auth/audit.py` |
| Rutas `/auth/*` | `app/auth/router.py` |
| CLI de administración | `app/cli.py` |
| Pantallas de acceso | `app/templates/auth/*.html` |
| Aplicación factorizada | `app/main.py` (`create_app()`) |

## 2. Orden de la pila

```
Request
  └─ SecurityHeadersMiddleware   X-Content-Type-Options, Referrer-Policy, X-Frame-Options
       └─ AuthGateMiddleware     (solo si EET_AUTH_ENABLED=true)
            └─ Router          → dependencia csrf_protect en cada POST
```

`SecurityHeaders` se agrega después de `AuthGate`, por lo que queda por fuera:
las redirecciones al login también llevan cabeceras. La CSP con nonce y HSTS
son parte de la Fase A6 y **no** se agregaron aquí.

Con la bandera apagada, `AuthGate` retorna en la primera línea: no abre sesión
de base, no crea cookies y no altera la respuesta.

## 3. Sesiones

- Token de 32 bytes en la cookie; en `sesiones` solo se guarda `sha256(token)`.
- Vida absoluta `EET_SESSION_ABSOLUTE_MIN` (720 min) fijada al crear.
- Inactividad `EET_SESSION_IDLE_MIN` (30 min) sobre `last_seen_at`; el toque se
  escribe como máximo una vez por minuto.
- `cargar_sesion` rechaza revocadas, expiradas e inactivas.
- `revocar_sesion`, `revocar_por_token`, `revocar_todas(excepto_id)` y
  `limpiar_expiradas(dias=30)`.
- Cookie: `EET_SESSION_COOKIE` (`__Host-eet_session`) con `cookie_secure=true`;
  con `cookie_secure=false` se usa `eet_session` (los prefijos `__Host-` exigen
  `Secure` y el navegador descartaría la cookie en local). Atributos:
  `HttpOnly`, `SameSite=Lax`, `Path=/`, `max_age=session_absolute_min*60`.
- En cada login se revoca la sesión previa de esa cookie: rotación que evita la
  fijación de sesión.

## 4. Diseño CSRF (cookie frente a sesión)

| Situación | Token válido | Transporte |
|---|---|---|
| Sin sesión (formulario de login) | token aleatorio de 32 bytes | cookie `eet_csrf` (`HttpOnly`, `SameSite=Lax`) + campo oculto (double submit) |
| Con sesión | `sesion.csrf_token` | campo oculto `csrf_token` |

**Decisión:** `csrf_token` no se guarda en una columna. El esquema de `sesiones`
quedó congelado en la Fase A0 y `tests/test_models_auth.py` fija su conjunto
exacto de columnas, de modo que agregar `csrf_token` habría roto ese contrato.
En su lugar el token se **deriva** con HMAC-SHA256 de `sha256(token)` y
`EET_SECRET_KEY`, y se expone en memoria como `sesion.csrf_token`:

- es distinto por sesión (un token ajeno no sirve);
- rota al rotar la sesión;
- no requiere escribir ni leer nada extra en la base;
- quien no tenga la clave del servidor no puede calcularlo a partir del hash.

`validar_csrf` compara en tiempo constante y, si vienen, exige que `Origin` y
`Referer` coincidan en esquema y host con `request.base_url`.

`csrf_protect` es una **dependencia** FastAPI (no un middleware de cuerpo): lee
`request.form()` (Starlette lo cachea, así que los routers multipart siguen
funcionando) y responde `403` HTML con «Token CSRF inválido o ausente.».

Con la bandera apagada `token_actual()` devuelve cadena vacía y `csrf_protect`
no valida: los formularios renderizan el campo oculto vacío y todo funciona
igual que antes.

### Barrido de formularios

`csrf_input()` en `components/ui.html` se insertó como primer campo en los 11
formularios POST existentes (entregas, vista previa, malla, mapa, ficha QA/QC,
evento y los cuatro de reemplazos) y en el logout del shell. Los formularios
`GET` de filtros **no** llevan token a propósito: el token viajaría en la URL
(historial, logs, `Referer`) sin proteger nada, porque un GET no cambia estado.

## 5. Flujos

**Login** — `GET/POST /auth/login`

1. `next` solo se acepta si empieza con `/` y no con `//`, `\` o saltos de
   línea; cualquier otro valor cae a `/`.
2. Usuario inexistente, inactivo o de otro proveedor: se verifica contra un
   hash de relleno y se responde el mismo mensaje («Usuario o contraseña
   incorrectos.») con el mismo coste de tiempo.
3. Bloqueo: 5 fallos (`EET_LOGIN_MAX_INTENTOS`) fijan `locked_until` durante
   `EET_LOGIN_BLOQUEO_MIN`; el mensaje dice cuántos minutos faltan.
4. Éxito: resetea `failed_attempts`, actualiza `last_login_at`, revoca la sesión
   previa, crea la nueva y redirige con `303`.

**Logout** — `POST /auth/logout` revoca la fila, borra la cookie y redirige a
`/auth/login?ok=sesion_cerrada`. Es una ruta pública (idempotente) y valida el
CSRF con el token de la sesión que se cierra, aunque ya haya caducado.

**Cambio de contraseña** — `GET/POST /auth/cambiar-password` exige sesión y
`must_change_password` no permite navegar a otra cosa. Valida contraseña actual,
coincidencia, política (`validar_password`) y que la nueva no sea la actual;
al guardar revoca las demás sesiones y audita `password_changed`.

## 6. Auditoría

`audit.registrar(...)` llena `auditoria` con `actor`, `accion`, `entidad`,
`entidad_id`, `detalle`, `ip` y `user_agent`. El actor es `usuario:<id>`,
`cli` o `sistema`. `_detalle_seguro` elimina cualquier clave que suene a
secreto (`password`, `hash`, `token`, `secret`, `cookie`, `csrf`, `pepper`).

Eventos de esta fase: `login_ok`, `login_fail`, `login_locked`,
`password_changed` y `user_created`.

## 7. CLI

```powershell
# Contraseña por EET_ADMIN_PASSWORD (o se pide dos veces por terminal)
uv run python -m app.cli crear-admin --email admin@aep.gov.py --username admin `
    --nombre "Administración EET" [--must-change]

# Contraseña por EET_USER_PASSWORD; must_change_password=true siempre
uv run python -m app.cli crear-usuario --email juan@aep.gov.py --username jcarga `
    --nombre "Juan Carga" --rol cargador

# Rol asistente exige la letra de terna
uv run python -m app.cli crear-usuario ... --rol asistente --letra A

# Mantenimiento
uv run python -m app.cli limpiar-sesiones --dias 30
```

`crear-admin` usa `rol=admin`, `auth_provider=local` y `mfa_policy=totp` (el
enrolamiento llega en A3). No hay contraseña por defecto: si no hay variable de
entorno ni terminal interactiva, el comando falla.

## 8. Activación

```env
EET_AUTH_ENABLED=true
EET_SECRET_KEY=<cadena larga aleatoria>
EET_AUTH_PEPPER=<otra cadena larga aleatoria>
EET_COOKIE_SECURE=true          # requiere HTTPS
# EET_SESSION_COOKIE=__Host-eet_session
# EET_SESSION_ABSOLUTE_MIN=720
# EET_SESSION_IDLE_MIN=30
# EET_LOGIN_MAX_INTENTOS=5
# EET_LOGIN_BLOQUEO_MIN=15
```

Con `EET_AUTH_ENABLED=true` y `EET_EXPOSE_DOCS=false` (valor por defecto) se
desactivan `/docs`, `/redoc` y `/openapi.json`. `/salud` y `/static` siguen
públicos. Activar la bandera sin `EET_SECRET_KEY` o sin `EET_AUTH_PEPPER`
impide el arranque (validación de `Settings`).

## 9. Pruebas

```powershell
uv run pytest -q                     # unitarias
uv run pytest -m integration -q      # requiere PostgreSQL
```

- `tests/test_auth_login.py`: redirección al login, cookie CSRF, credenciales
  inválidas, login correcto, `next` interno, logout, bloqueo por 5 fallos,
  usuario inactivo, `must_change_password`, `Accept: application/json` → 401 y
  cabeceras de seguridad.
- `tests/test_auth_csrf.py`: POST sin token, token de otra sesión, token de la
  cookie anónima con sesión, `Origin`/`Referer` distintos y camino válido.
- `tests/test_auth_templates.py`: todo `<form method="post">` lleva el campo
  CSRF; hay al menos 17 formularios.
- `tests/test_auth_audit.py`: `login_ok`, `login_fail`, bloqueo y cambio de
  contraseña con actor, IP y sin secretos; la sesión guarda solo el hash.

Las pruebas crean cuentas con nombre único y las borran en el teardown
(sesiones, auditoría e intentos incluidos).

## 10. Cómo quitar una cuenta creada a mano (por ejemplo, la de humo)

```powershell
uv run python -c "from sqlalchemy import text; from app.core.database import engine; \
  uid = 113; \
  c = engine.begin(); conn = c.__enter__(); \
  conn.execute(text('DELETE FROM sesiones WHERE usuario_id = :i'), {'i': uid}); \
  conn.execute(text('DELETE FROM auditoria WHERE actor = :a'), {'a': f'usuario:{uid}'}); \
  conn.execute(text('DELETE FROM usuarios WHERE id = :i'), {'i': uid}); \
  c.__exit__(None, None, None)"
```

O, más simple, con `psql` dentro del contenedor de base:

```powershell
docker compose exec db psql -U eet -d eet -c "DELETE FROM usuarios WHERE username = 'admin-prueba-a1';"
```

`sesiones` tiene `ON DELETE CASCADE`; las filas de `auditoria` no, por eso se
borran aparte si se quiere dejar la tabla limpia.

## 11. Desviaciones respecto del plan

1. **`sesion.csrf_token` derivado, no almacenado.** Ver §4: el esquema de A0
   está congelado por `tests/test_models_auth.py` y no se modificó `app/models/`.
2. **`AuthProvider.authenticate` devuelve `(usuario, needs_rehash)`.** El
   pseudocódigo del plan declaraba `Usuario | None` pero exigía el flag de
   rehash; se tipó la tupla en el `Protocol`.
3. **Formularios GET sin token CSRF.** Solo los POST lo llevan (§4).
4. **`require_roles` implementado pero sin usar.** La matriz de permisos y las
   guardas por ruta son la Fase A2.
5. **Sin rate limit por IP.** A1 aplica bloqueo por cuenta; el límite por IP y
   el `login_attempts` como ventana deslizante son la Fase A6 (la tabla ya se
   llena con cada intento).
6. **Middlewares de Starlette con `BaseHTTPMiddleware`.** Suficiente para el
   volumen actual; si el costo aparece, se migra a middleware ASGI puro.
