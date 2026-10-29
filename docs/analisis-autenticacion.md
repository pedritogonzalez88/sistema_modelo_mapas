# Análisis de autenticación y autorización — Validador EET

> Documento de decisión. Analiza cómo incorporar autenticación local (usuario/contraseña),
> escalable a LDAP/Active Directory, con segundo factor por aplicación autenticadora y por correo,
> y recuperación de cuenta por correo.
> No implementa: define arquitectura, alcance, riesgos y fases.

---

## 1. Resumen ejecutivo

El sistema hoy **no tiene autenticación ni autorización**. Cualquiera con acceso a la URL puede
subir entregas, confirmar cruces, resolver fichas QA/QC, autorizar reemplazos y exportar el
consolidado. El modelo `Usuario` existe (con roles y letra de terna) pero **no se usa en ninguna
parte**; no hay sesiones, cookies, CSRF ni registro de quién hizo qué.

Recomendación:

1. **Autenticación propia en la aplicación** (Opción A) con una capa de proveedores:
   `local` para cuentas propias y `ldap` para Active Directory. Es la ruta que cumple el pedido
   sin sumar un servicio crítico más, y deja la puerta abierta a OIDC (Entra ID) sin reescribir.
2. **Sesiones en servidor** con cookie `HttpOnly`/`Secure`/`SameSite=Lax`, no JWT.
   Permiten revocación inmediata, auditoría y encajan con Jinja sin fricción.
3. **2FA obligatorio por política de rol**: TOTP para supervisión y administración;
   TOTP o código por correo para el resto. Correo como canal de recuperación.
4. **Autorización por roles en el servidor** (dependencia FastAPI) + ocultamiento en la UI.
   El protocolo exige independencia de la terna: un asistente no puede ver interpretaciones ajenas.
5. **No construir criptografía propia**: Argon2id para contraseñas, `pyotp` para TOTP,
   `cryptography` para cifrar secretos, tokens aleatorios de 256 bits hasheados en base.

Si la institución ya opera o acepta operar un IdP central (Authentik/Keycloak/Entra ID),
la **Opción B (OIDC)** es preferible a mediano plazo; el diseño de proveedores permite migrar
sin tocar la capa de autorización. Ver §5.

---

## 2. Estado actual del código (evidencia)

| Aspecto | Situación |
|---|---|
| Middleware de sesión | No existe (`grep session/cookie` solo encuentra un comentario en `web.py`) |
| Autenticación | Ninguna. Todas las rutas son públicas |
| CSRF | Ninguno. ~20 formularios POST sin token |
| `Usuario` | Definido en `app/models/__init__.py` con `nombre`, `rol`, `letra_terna`; **sin email, username, hash ni estado** |
| Roles | `asistente`, `cargador`, `supervision`, `consulta` (comentados en el modelo) y 5 usuarios semilla en `persist.seed` |
| Autorización | Inexistente; no hay `Depends` de usuario ni chequeos por ruta |
| Auditoría | Tabla `auditoria` con `actor` libre; las acciones se registran como `"supervision"` o `"cargador"` fijos |
| TLS | No configurado; se asume reverse proxy en producción (no hay nada en el repo) |
| Correo | No hay cliente SMTP ni configuración |
| Dependencias | `fastapi`, `sqlalchemy`, `jinja2`; ninguna de hashing, TOTP, LDAP o email |
| Migraciones | No hay Alembic; `app/core/database.py` aplica una lista manual `_MIGRACIONES` con `ADD COLUMN IF NOT EXISTS` |

Consecuencias directas:

- Cualquier persona puede **cerrar una ficha QA/QC** y alterar la clase consolidada.
- El campo `Auditoria.actor` es decorativo: no hay identidad verificada detrás.
- La independencia de la terna (requisito del protocolo) no está protegida técnicamente.
- Los enlaces de exportación y descargas son públicos.

---

## 3. Requisitos y supuestos

### 3.1 Requisitos funcionales

| ID | Requisito |
|---|---|
| RF-01 | Inicio de sesión con usuario/contraseña para cuentas locales |
| RF-02 | Inicio de sesión contra LDAP/Active Directory sin almacenar la contraseña del dominio |
| RF-03 | Segundo factor con app autenticadora (TOTP, RFC 6238) |
| RF-04 | Segundo factor por código de un solo uso enviado por correo |
| RF-05 | Recuperación de contraseña por correo (solo cuentas locales) |
| RF-06 | Recuperación de acceso cuando se pierde el segundo factor (códigos de respaldo / correo / admin) |
| RF-07 | Roles y permisos aplicados en servidor y reflejados en la UI |
| RF-08 | Sesiones revocables, con expiración por inactividad y absoluta |
| RF-09 | Auditoría de eventos de autenticación y de cambios de permisos |
| RF-10 | Gestión de usuarios (alta, baja, rol, reset) por un rol administrador |

### 3.2 Requisitos no funcionales

- **Seguridad**: OWASP ASVS nivel 2 como referencia; NIST 800-63B para contraseñas y AAL2 para 2FA.
- **Operación**: debe funcionar on-premise, sin dependencia obligatoria de internet.
- **Compatibilidad**: mantener Jinja + formularios server-side; sin SPA.
- **Accesibilidad**: las pantallas nuevas cumplen lo ya logrado (WCAG 2.2 AA, teclado, foco).
- **Trazabilidad**: cada acción queda ligada a un `usuario_id` verificado, no a un texto libre.

### 3.3 Supuestos a confirmar

- Existe un **Active Directory** institucional accesible desde el servidor (LDAP/LDAPS).
- Hay un **relay SMTP** interno o un proveedor transaccional disponible.
- El sistema se publica en **HTTPS** detrás de un reverse proxy.
- La mayoría de los usuarios internos tiene cuenta de dominio; los asistentes externos podrían no tenerla.
- El volumen es bajo (decenas de usuarios, no miles).

---

## 4. Modelo de amenazas (resumen)

| Amenaza | Vector | Control propuesto |
|---|---|---|
| Fuerza bruta / credential stuffing | `/auth/login` | Rate limit por IP+cuenta, backoff, bloqueo temporal, auditoría, contraseñas robustas |
| Phishing de credenciales | Correo falso | 2FA, educación, DMARC/SPF en el dominio, aviso de "no compartas códigos" |
| Reutilización de contraseña | Filtraciones externas | Argon2id, política NIST, verificación opcional contra lista de filtradas |
| Robo de sesión | XSS / red | Cookie `HttpOnly`+`Secure`+`SameSite`, CSP, TLS, rotación de ID al login |
| CSRF | Formularios | Token sincronizado por sesión + validación de `Origin` |
| Fijación de sesión | Login | Regenerar sesión tras autenticar y tras 2FA |
| Enumeración de usuarios | Login/recuperación | Respuesta genérica y tiempos constantes |
| Fuerza bruta de códigos OTP | `/auth/mfa` | 5 intentos, expiración 10 min, un solo uso, hash del código |
| Replay de TOTP | Código reutilizado | Guardar `last_used_step`, ventana ±1 |
| Robo de secreto TOTP | Base de datos | Cifrado con Fernet + clave fuera de la BD + `key_id` para rotar |
| Bloqueo de cuentas AD (DoS) | Intentos contra el DC | Rate limit **antes** de bind, sin reintentos automáticos, mensaje genérico |
| Abuso interno | Usuario con más permisos | Mínimo privilegio, separación admin/supervisión, auditoría inmutable |
| Correo de recuperación interceptado | Buzón comprometido | Token de un solo uso, 30 min, revocación de sesiones, notificación |
| Dominio caído | DC no disponible | *Break-glass* local para administración, sin fallback silencioso a local para usuarios AD |

Fuera de alcance de este documento: WAF, DLP, hardening del host, SIEM. Se asumen capas externas.

---

## 5. Opciones de arquitectura

### Opción A — Autenticación en la aplicación (recomendada para el estado actual)

La app gestiona login, 2FA, recuperación y sesiones; los proveedores `local` y `ldap` se
seleccionan por usuario o por política.

- **Ventajas**: no suma servicios; control total del flujo y de la UI; funciona aislada; encaja con
  los roles que ya existen en el modelo; no requiere internet.
- **Desventajas**: hay que construir y mantener flujos sensibles (2FA, recuperación, rate limit);
  la seguridad depende de la calidad de esta implementación.
- **Esfuerzo**: 18–22 días de desarrollo + pruebas.

### Opción B — IdP externo con OIDC (Authentik, Keycloak o Entra ID)

La app delega autenticación y 2FA al IdP y consume claims (grupos → rol).

- **Ventajas**: MFA, recuperación, federación LDAP/AD, políticas de sesión y auditoría ya resueltas;
  menor superficie criptográfica propia; estándar (OIDC).
- **Desventajas**: un servicio crítico más (alta disponibilidad, backups, TLS, actualizaciones);
  dependencia de red para entrar; la UX de login sale de la app; el mapeo de roles sigue siendo nuestro.
- **Esfuerzo**: 6–8 días de integración + 2–4 días de operación del IdP.

### Opción C — Híbrido (A ahora, B después)

Implementar A con la abstracción de proveedores y agregar `OidcProvider` en una fase posterior.
Es la recomendación si hoy no hay IdP operado por TI.

### Matriz de decisión

| Criterio | A | B | C |
|---|---|---|---|
| Time to value | Medio | Alto (infra) | Medio |
| Carga operativa | Baja | Alta | Media |
| Madurez de 2FA/recuperación | Media (a construir) | Alta | Alta al final |
| Control de UX | Alto | Bajo | Alto |
| Riesgo de seguridad propio | Medio | Bajo | Medio→Bajo |
| Escalabilidad a AD | Alta (LDAP) | Alta (federación) | Alta |
| Encaja con el pedido literal | Sí | Sí | Sí |

**Recomendación:** Opción C, empezando por A. Si TI ya tiene Entra ID y acepta publicar una app
empresarial, saltar directamente a B ahorra los flujos más delicados.

---

## 6. Diseño recomendado (Opción A/C)

### 6.1 Componentes

```
app/
├── core/
│   ├── config.py          # + settings de auth, smtp, ldap, cookies
│   └── security.py        # hashing, tokens, cifrado, TOTP helpers
├── auth/
│   ├── providers.py       # LocalProvider | LdapProvider (| OidcProvider futuro)
│   ├── service.py         # login, 2FA, recovery, sesiones, rate limit
│   ├── dependencies.py    # get_current_user, require_roles, require_mfa
│   ├── csrf.py            # token por sesión + validación
│   ├── mailer.py          # SMTP / consola / archivo
│   └── router.py          # /auth/*
├── models/                # + tablas de sesiones, tokens, mfa, outbox
├── templates/auth/        # login, mfa, recuperación, seguridad, usuarios
└── middleware.py          # sesión, headers, CSRF, contexto de usuario
```

Separación clave: **autenticación** (¿quién sos?) en `providers` + `service`;
**autorización** (¿qué podés?) en `dependencies` + matriz de §6.4.

### 6.2 Modelo de datos

Extender `usuarios` (sin romper las filas semilla):

```sql
ALTER TABLE usuarios ADD COLUMN username            CITEXT UNIQUE;
ALTER TABLE usuarios ADD COLUMN email               CITEXT UNIQUE;
ALTER TABLE usuarios ADD COLUMN auth_provider       VARCHAR(20) NOT NULL DEFAULT 'local'; -- local|ldap|oidc
ALTER TABLE usuarios ADD COLUMN password_hash       TEXT;              -- NULL si provider != local
ALTER TABLE usuarios ADD COLUMN password_changed_at TIMESTAMPTZ;
ALTER TABLE usuarios ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN is_active           BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE usuarios ADD COLUMN mfa_policy          VARCHAR(20) NOT NULL DEFAULT 'any'; -- disabled|email|totp|any
ALTER TABLE usuarios ADD COLUMN mfa_enrolled_at     TIMESTAMPTZ;
ALTER TABLE usuarios ADD COLUMN role_source         VARCHAR(10) NOT NULL DEFAULT 'manual'; -- manual|ldap
ALTER TABLE usuarios ADD COLUMN ldap_dn             TEXT;
ALTER TABLE usuarios ADD COLUMN external_id         TEXT;
ALTER TABLE usuarios ADD COLUMN last_login_at       TIMESTAMPTZ;
ALTER TABLE usuarios ADD COLUMN failed_attempts     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE usuarios ADD COLUMN locked_until        TIMESTAMPTZ;
ALTER TABLE usuarios ADD COLUMN created_at          TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE usuarios ADD COLUMN updated_at          TIMESTAMPTZ NOT NULL DEFAULT now();
```

Tablas nuevas:

| Tabla | Columnas principales | Propósito |
|---|---|---|
| `sesiones` | `id uuid`, `token_hash char(64) unique`, `usuario_id`, `created_at`, `last_seen_at`, `expires_at`, `revoked_at`, `mfa_at`, `ip`, `user_agent` | Sesión revocable; el token va en cookie y solo se guarda su hash |
| `mfa_totp` | `usuario_id PK`, `secret_enc bytea`, `key_id`, `confirmed_at`, `last_used_step bigint` | Secreto cifrado y antirreplay |
| `mfa_email_codes` | `id`, `usuario_id`, `code_hash`, `purpose`, `expires_at`, `attempts`, `consumed_at` | Códigos de 6 dígitos hasheados |
| `recovery_codes` | `id`, `usuario_id`, `code_hash`, `used_at`, `created_at` | 10 códigos de un solo uso |
| `auth_tokens` | `id`, `usuario_id`, `purpose`, `token_hash`, `expires_at`, `used_at`, `ip` | Reset, activación, verificación de correo |
| `login_attempts` | `id`, `identificador`, `ip`, `exito`, `created_at` | Rate limit y auditoría |
| `email_outbox` | `id`, `destinatario`, `asunto`, `cuerpo`, `intentos`, `enviado_en`, `ultimo_error` | Reintentos y evidencia de envío |
| `dispositivos_confiables` | `id`, `usuario_id`, `token_hash`, `expires_at`, `user_agent` | "Recordar este equipo" (opcional, apagado por defecto) |

Índices: `sesiones(token_hash)`, `sesiones(usuario_id, revoked_at)`, `auth_tokens(token_hash)`,
`login_attempts(identificador, created_at)`, `login_attempts(ip, created_at)`.

Migración: **introducir Alembic** en esta fase. `create_all` + `_MIGRACIONES` no es suficiente para
un cambio de esta magnitud (índices, constraints, backfill). Plan: baseline del esquema actual,
luego migraciones incrementales. Mantener `init_db()` solo para desarrollo.

### 6.3 Flujos

#### Login con contraseña + 2FA

```
GET  /auth/login                          formulario (usuario, contraseña, CSRF)
POST /auth/login
  ├─ rate limit por IP+identificador
  ├─ provider.authenticate()              local: Argon2id verify | ldap: bind
  ├─ fallo → auditoría + error genérico (mismo texto y tiempo)
  ├─ éxito sin 2FA requerido → crear sesión → 303 /
  └─ éxito con 2FA → challenge pendiente (cookie firmada 5 min o fila en auth_tokens)
        ├─ TOTP → GET /auth/mfa (inputmode=numeric, autocomplete=one-time-code)
        └─ email → generar código 6 dígitos, enviar, GET /auth/mfa
POST /auth/mfa
  ├─ verificar ventana ±1 y last_used_step
  ├─ crear sesión nueva (rotar id), marcar mfa_at, limpiar challenge
  └─ 303 / (o return_to validado)
```

Reglas:
- La sesión **no** se crea hasta superar el segundo factor.
- `return_to` solo rutas relativas (`/`), nunca absolutas.
- Tras 5 fallos de 2FA se invalida el challenge y se vuelve al login.

#### Enrolamiento TOTP

```
GET  /auth/seguridad
POST /auth/seguridad/totp/iniciar   → secreto aleatorio (160 bits), cifrado, estado "pendiente"
                                    → QR SVG (segno) + clave manual + instrucciones
POST /auth/seguridad/totp/confirmar → verifica código; activa; muestra 10 códigos de respaldo
                                      (una sola vez, descargables)
```

El secreto pendiente expira a los 15 minutos. Si ya hay TOTP activo, para re-enrolar se exige
sesión con 2FA reciente (< 10 min) o código de respaldo.

#### 2FA por correo

- Código de 6 dígitos, `secrets.randbelow`, hash HMAC-SHA256 con pepper, 10 min, 5 intentos.
- Se invalida cualquier código anterior del mismo propósito.
- El correo dice: origen, hora, "si no fuiste vos, cambiá la contraseña" y "no compartas este código".
- Sin adjuntos ni enlaces de acción.

#### Recuperación de contraseña (solo `local`)

```
GET/POST /auth/recuperar        → respuesta SIEMPRE genérica (exista o no la cuenta)
  ├─ si existe y es local: token 32 bytes, hash SHA-256, 30 min, un solo uso
  ├─ enviar enlace https://<host>/auth/restablecer?token=...
  └─ auditoría (sin token ni IP en el cuerpo del correo)
GET/POST /auth/restablecer      → token válido → nueva contraseña (política) 
                                  → revocar todas las sesiones + notificar por correo
```

- Token inválido/expirado/usado → mismo mensaje, sin filtrar cuál falló.
- Usuarios LDAP: el formulario responde "su cuenta se administra en el dominio; contacte a TI",
  sin revelar si el identificador existe (se muestra ese texto solo si el correo coincide con una
  cuenta LDAP y se envió un aviso al usuario).
- Límite: 3 solicitudes por hora por cuenta y 10 por IP.

#### Recuperación de 2FA

Orden de opciones:
1. Código de respaldo (si quedan).
2. Código por correo (si el usuario tiene email verificado).
3. Reset asistido por administrador: verificación de identidad fuera de banda, revocación de
   factores y sesiones, auditoría con motivo obligatorio. El administrador **nunca** ve el secreto.

#### LDAP / Active Directory

```
1. rate limit local (antes de tocar el DC)
2. conexión LDAPS (636) o StartTLS; validación de certificado; timeout 5 s
3. bind con cuenta de servicio  → buscar (&(objectClass=user)(sAMAccountName=<user>))
4. bind con el DN del usuario y su contraseña  → valida credencial
5. leer displayName, mail, memberOf → mapear a rol (config) → sincronizar nombre/email
6. si role_source='ldap', actualizar rol; si 'manual', respetar override
```

Detalles que evitan problemas reales:
- **Nunca** almacenar la contraseña del dominio ni hashes de AD.
- Deshabilitar el reset local para usuarios `auth_provider='ldap'`.
- Un fallo de LDAP **no** cae a autenticación local: *fail closed*.
- Cuenta de servicio de solo lectura, contraseña rotada, guardada en secreto.
- Mapeo de grupos explícito y denegado por defecto:
  ```env
  EET_LDAP_ROLE_MAP={"CN=EET-Supervision,OU=Grupos,DC=aep,DC=gov,DC=py":"supervision",
                     "CN=EET-Carga,OU=Grupos,DC=aep,DC=gov,DC=py":"cargador",
                     "CN=EET-Consulta,OU=Grupos,DC=aep,DC=gov,DC=py":"consulta"}
  ```
- Si el usuario pertenece a varios grupos, se aplica prioridad explícita
  (`admin > supervision > cargador > consulta > asistente`) y se audita.
- **Break-glass**: un administrador local (`auth_provider=local`, TOTP obligatorio) creado por CLI,
  fuera del mapeo LDAP, para operar si el DC no responde.

### 6.4 Autorización y matriz de roles

Roles definitivos: `admin`, `supervision`, `cargador`, `asistente`, `consulta`.

| Recurso / acción | admin | supervision | cargador | asistente | consulta |
|---|---|---|---|---|---|
| Dashboard | leer | leer | leer | propio | leer |
| Entregas: listar | todas | todas | todas | **solo su letra** | aceptadas |
| Entregas: subir/confirmar/rechazar | — | sí | sí | — | — |
| Cruces: ver | — | sí | sí (sin decidir) | **no** | sí |
| QA/QC: ver | — | sí | lectura | — | lectura |
| QA/QC: dictaminar/cerrar | — | sí | — | — | — |
| Reemplazos: proponer | — | sí | sí | — | — |
| Reemplazos: autorizar/completar | — | sí | — | — | — |
| Malla / Mapa: cargar | — | sí | sí | — | — |
| Exportar consolidado | — | sí | — | — | sí |
| Usuarios y políticas | sí | — | — | — | — |
| Auditoría | leer | leer | — | — | — |

Notas:
- **Separación de deberes**: `admin` no dictamina fichas ni autoriza reemplazos. Evita que una sola
  persona pueda alterar la clase consolidada y borrar el rastro.
- La independencia de la terna se garantiza con `asistente` + filtro por `letra_terna`; el menú
  oculta Cruces/QA/QC, y el servidor devuelve 403 aunque se escriba la URL.
- El `cargador` ve resultados del cruce porque opera el pipeline, pero no puede modificarlos.
- `consulta` es lectura; si el protocolo exige publicar solo tras cierre, se añade un estado
  `publicado` por proyecto antes de habilitar la vista.

Implementación:

```python
# dependencies.py
def require_roles(*roles: str):
    def _dep(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(403, "No tiene permiso para esta acción.")
        return usuario
    return _dep

@router.post("/qaqc/{ficha_id}/evento")
def evento(..., usuario=Depends(require_roles("supervision"))):
    ...
```

En plantillas, un helper `puede("qaqc.decidir")` para ocultar navegación y botones.
**La UI nunca es el control de seguridad**; el servidor siempre valida.

### 6.5 Sesiones, cookies y CSRF

- Token de sesión: 32 bytes (`secrets.token_urlsafe`), cookie `__Host-eet_session`.
- Base de datos guarda `sha256(token)`; comparación en tiempo constante.
- Atributos: `HttpOnly`, `Secure` (excepto dev), `SameSite=Lax`, `Path=/`, sin `Domain`.
- Expiración: **absoluta 12 h**, **inactividad 30 min** (sliding). Renovación de `expires_at`
  como máximo una vez por minuto para no escribir en cada request.
- Rotación de token al autenticar, al superar 2FA y al cambiar contraseña.
- `GET /auth/sesiones`: lista de sesiones activas con IP, agente, última actividad y botón revocar.
- Logout: revoca la fila, borra cookie, redirige a login.
- Limpieza: job diario (o al arrancar) que borra expiradas y tokens consumidos.

CSRF:
- Token por sesión (`secrets.token_urlsafe(32)`) guardado en la fila y expuesto como global Jinja
  `csrf_token()`; input oculto en **todos** los formularios.
- Middleware valida en `POST/PUT/PATCH/DELETE`: token + `Origin`/`Referer` contra el host.
- Fallo → 403 HTML con mensaje y enlace de vuelta (no JSON).
- Excepción documentada: ninguna ruta POST queda exenta.

Headers (middleware):
```
Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self';
                         script-src 'self' 'nonce-<random>'; frame-ancestors 'none';
                         base-uri 'self'; form-action 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: geolocation=(), camera=(), microphone=()
Strict-Transport-Security: max-age=31536000; includeSubDomains   (solo con HTTPS)
```
> Hoy hay scripts inline en varias plantillas; para que CSP no rompa nada hay que moverlos a
> `app.js` o añadir nonce por request. Es parte de la fase de endurecimiento.

### 6.6 Correo

Configuración:

```env
EET_EMAIL_BACKEND=console          # console | smtp | file
EET_SMTP_HOST=mail.aep.gov.py
EET_SMTP_PORT=587
EET_SMTP_STARTTLS=true
EET_SMTP_USER=...
EET_SMTP_PASSWORD=...
EET_EMAIL_FROM="Validador EET <no-reply@aep.gov.py>"
EET_PUBLIC_BASE_URL=https://eet.aep.gov.py
```

- Desarrollo: backend `console` (escribe el correo en el log) o `file` (`.eml` en `uploads/mail/`);
  nunca envía.
- Producción: SMTP con STARTTLS; `email_outbox` para reintentos y evidencia.
- Envío en segundo plano para recuperación; **síncrono con timeout** para códigos de login
  (el usuario está esperando).
- Plantillas de correo en texto plano + HTML mínimo, sin imágenes remotas.
- SPF/DKIM/DMARC del dominio remitente son responsabilidad de TI; sin ellos la recuperación
  cae en spam.

### 6.7 Contraseñas y secretos

| Elemento | Decisión |
|---|---|
| Hash de contraseña | **Argon2id** (`argon2-cffi`), parámetros OWASP: 64 MB, 3 iteraciones, paralelismo 4 (ajustar al contenedor) |
| Política | NIST 800-63B: mínimo 12 caracteres, máximo 128, sin reglas de composición, bloqueo de las 10k más comunes, comparación con username |
| Cambio | Requiere contraseña actual + 2FA reciente; revoca otras sesiones |
| Fuerza inicial | `must_change_password=true` para cuentas creadas por admin o bootstrap |
| Secreto TOTP | Cifrado Fernet; `key_id` para rotación; nunca se registra ni se muestra tras el enrolamiento |
| Tokens | 32 bytes aleatorios; solo se guarda `sha256`; un solo uso; expiración corta |
| Pepper de códigos | `EET_AUTH_PEPPER` separado de la clave Fernet |
| Gestión | Variables de entorno / `docker secret`; nunca en Git ni en la BD |

Rotación de claves: `EET_FERNET_KEYS="v2:<base64>,v1:<base64>"`; se cifra con la primera y se
descifra con cualquiera. Un job puede re-cifrar los secretos viejos.

### 6.8 Auditoría

Extender `auditoria` con `ip` y `user_agent` (o guardarlos en `detalle`), y registrar:

`login_ok`, `login_fail`, `login_locked`, `mfa_ok`, `mfa_fail`, `mfa_enrolled`, `mfa_reset`,
`password_reset_requested`, `password_reset_ok`, `password_changed`, `session_revoked`,
`user_created`, `user_disabled`, `role_changed`, `ldap_error`, `ldap_sync`.

Reglas:
- Nunca registrar contraseñas, códigos, tokens ni secretos TOTP.
- `actor` pasa a ser `usuario:<id>` (o `sistema`), no un rol fijo.
- Los cambios de rol y los resets asistidos exigen `motivo` en `detalle`.
- Retención mínima: 1 año (definir con la institución).

### 6.9 UX de las pantallas nuevas

| Pantalla | Elementos clave |
|---|---|
| `/auth/login` | Marca institucional, usuario, contraseña con mostrar/ocultar, "¿Olvidaste tu contraseña?", error genérico, `autocomplete="username"` / `current-password`, foco en el primer campo, sin revelar si el usuario existe |
| `/auth/mfa` | Un solo campo de 6 dígitos (`inputmode="numeric"`, `autocomplete="one-time-code"`, pegado permitido), enlace "usar otro método", aviso de expiración, cuenta atrás discreta |
| `/auth/recuperar` | Email/usuario, mensaje neutro siempre, explicación del siguiente paso |
| `/auth/restablecer` | Nueva contraseña + confirmación, medidor simple, requisitos visibles, token inválido con CTA |
| `/auth/seguridad` | Estado de 2FA, método preferido, "cambiar autenticador", códigos de respaldo (generar/descargar), sesiones activas |
| `/auth/usuarios` (admin) | Tabla con rol, proveedor, estado, último acceso; alta, desactivar, reset, forzar cambio de contraseña |
| `/auth/auditoria` | Filtros por evento/usuario/fecha, exportación |

Accesibilidad: reutilizar `field`, `banner`, `btn`, `badge`; un solo `h1`; errores con resumen y
`aria-describedby`; nunca depender solo del color; tiempos de espera anunciados con `aria-live`.

---

## 7. Migración y puesta en marcha

1. **Inventario de usuarios**: nombre, email, rol, letra de terna, ¿tiene AD?
2. **Bootstrap**: comando `python -m app.cli crear-admin --email ...` que crea el primer `admin`
   con contraseña temporal y TOTP obligatorio. Nunca una contraseña por defecto.
3. **Backfill**: las 5 filas semilla se completan con username/email; el resto se crea desde la UI.
4. **Feature flag** `EET_AUTH_ENABLED=false` durante el desarrollo; al activarlo, un middleware
   redirige a login toda ruta no pública. Rutas públicas: `/auth/*`, `/salud`, `/static/*`.
5. **Rollout**: staging → supervisión (piloto) → cargadores → asistentes → consulta.
6. **Comunicación**: instructivo de enrolamiento de 2FA, qué hacer si se pierde el teléfono,
   política de contraseñas y horario de soporte.

Orden de migración de base:
`Alembic baseline` → `usuarios` (columnas) → `sesiones` → `mfa_*` → `auth_tokens` →
`login_attempts` → `email_outbox` → índices. Todo reversible.

---

## 8. Plan por fases y estimación

| Fase | Alcance | Días |
|---|---|---|
| 0. Decisiones y preparación | Confirmar AD/SMTP/TLS; inventario; política de 2FA; Alembic baseline | 2 |
| 1. Núcleo de sesión y login local | Modelo, Argon2id, sesiones, CSRF, middleware, login/logout, auditoría | 5 |
| 2. Autorización | Matriz de roles, dependencias, filtros por letra, UI condicional, pruebas 403 | 3 |
| 3. TOTP | Enrolamiento, QR, verificación con antirreplay, códigos de respaldo, reset | 3 |
| 4. Correo y recuperación | Mailer, outbox, códigos por correo, reset de contraseña, notificaciones | 4 |
| 5. LDAP/AD | `ldap3`, LDAPS, mapeo de grupos, sync, break-glass, pruebas con mock | 4 |
| 6. Endurecimiento y cierre | Rate limit, lockout, headers/CSP, sesiones UI, pruebas E2E, documentación | 3 |
| **Total** | | **24 días** (±20%) |

Camino crítico: 0 → 1 → 2 (habilita proteger el sistema) → 3/4 en paralelo → 5 → 6.

Si se elige la Opción B (IdP), las fases 3, 4 y 5 se sustituyen por: integración OIDC (4 d),
mapeo de grupos (1 d), break-glass local (2 d), operación del IdP (aparte).

---

## 9. Pruebas

**Unitarias**
- Argon2id: hash/verify, rehash al cambiar parámetros.
- Tokens: longitud, hash, expiración, un solo uso.
- TOTP: ventana ±1, rechazo de paso repetido, tolerancia de reloj.
- CSRF: token válido/ausente/ajeno; `Origin` distinto.
- Política de contraseñas y comparación con username.

**Integración (TestClient + Postgres)**
- Login correcto/incorrecto, mensajes idénticos, auditoría.
- Bloqueo tras N intentos y desbloqueo por tiempo.
- Flujo 2FA completo (TOTP y correo), código expirado, reintentos.
- Reset: solicitud, token válido, reutilización rechazada, sesiones revocadas.
- 403 por rol en cada ruta sensible; asistente A no ve entrega de B.
- LDAP con `ldap3.MOCK_SYNC` (usuario válido, inválido, grupo→rol, DC caído).
- Correo con backend `console`: contenido, ausencia de tokens en logs.

**E2E (Playwright, ya instalado)**
- Login + 2FA + navegación; recuperación completa; enrolamiento TOTP con QR.
- Accesibilidad de las 6 pantallas nuevas con axe (mismo estándar actual).
- Teclado: foco, pegado de código, `Escape`, sin trampas.

**Seguridad**
- Prueba de enumeración (tiempos y textos).
- Fuerza bruta simulada contra login y OTP.
- Revisión de headers y cookies (`Secure`, `HttpOnly`, `SameSite`).
- Verificación de que ningún log contiene secretos.

---

## 10. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Bloqueo de cuentas AD por intentos | Alto | Rate limit local previo al bind; sin reintentos; mensaje genérico |
| Pérdida masiva de acceso por 2FA | Alto | Códigos de respaldo, correo alternativo, break-glass, soporte documentado |
| SMTP institucional bloquea o marca spam | Alto | Relay autenticado, SPF/DKIM/DMARC, outbox con reintentos, backend de archivo en dev |
| DC no disponible | Alto | Break-glass local; monitoreo; timeout corto; no fallback silencioso |
| Sesiones sin TLS | Crítico | Publicar solo por HTTPS; `Secure` obligatorio en producción; HSTS |
| CSP rompe scripts inline | Medio | Mover JS a archivos o nonce por request; probar en staging |
| Complejidad de migración | Medio | Alembic + feature flag + rollout por rol |
| Administrador único se bloquea | Medio | Dos administradores como mínimo; break-glass auditado |
| Usuarios externados por error | Medio | Baja lógica (`is_active=false`), revocación de sesiones, auditoría |
| Fatiga de 2FA | Bajo | Política por rol: correo para asistentes, TOTP para supervisión |

---

## 11. Decisiones confirmadas (2026-10-29)

1. **AD/LDAP: todavía no existe.** Se implementa el proveedor y toda su configuración, pero
   **deshabilitado por defecto** (`EET_LDAP_ENABLED=false`). Queda listo para activarse cuando TI
   entregue dominio, base DN, grupos y cuenta de servicio. Se incluye comando de diagnóstico
   `python -m app.cli ldap-check` y pruebas con servidor LDAP simulado.
2. **Correo: dominio institucional disponible.** Se usa SMTP autenticado con remitente del dominio
   (`no-reply@<dominio>`), outbox con reintentos y backend de consola en desarrollo. SPF/DKIM/DMARC
   quedan como dependencia de TI: sin ellos la recuperación de cuentas falla en la práctica.
3. **Exposición: internet e interna.** Sube el nivel de exigencia:
   - HTTPS obligatorio, cookies `Secure` + `HttpOnly` + `SameSite=Lax`, HSTS.
   - Rate limit por IP **y** por cuenta en login, 2FA y recuperación; bloqueo temporal.
   - Protección CSRF en todos los POST, CSP con nonce, `frame-ancestors 'none'`.
   - Rutas de administración y auditoría restringidas a red interna/VPN por configuración
     (`EET_ADMIN_INTERNAL_ONLY=true`), además del rol.
   - Sin enumeración de usuarios ni diferencias de tiempo entre cuenta existente e inexistente.
   - Logs sin secretos; alertas de picos de fallos; revisión de dependencias.
4. **Política de 2FA: aprobada.** TOTP obligatorio para `admin` y `supervision`; TOTP **o** correo
   para `cargador`, `asistente` y `consulta`. "Recordar este equipo" desactivado por defecto.
5. **Alembic: aprobado.** Se introduce como mecanismo único de migración; `init_db()` queda solo
   para desarrollo local.
6. **Sin IdP externo.** Se descarta OIDC por ahora; la abstracción de proveedores deja la puerta
   abierta sin comprometer el diseño.

### Datos operativos pendientes (no bloquean el desarrollo)

- Host, puerto, usuario y contraseña del relay SMTP institucional.
- Dominio exacto del remitente y confirmación de SPF/DKIM/DMARC.
- Ventana de mantenimiento para el despliegue inicial.
- Nombres de los grupos de AD y base DN (para cuando exista).
- Retención de auditoría (propuesta: 1 año) y de sesiones (propuesta: 30 días tras expirar).

---

## 12. Anexos

### A. Dependencias nuevas

```toml
argon2-cffi>=23.1
pyotp>=2.9
segno>=1.6            # QR en SVG, sin Pillow
cryptography>=42
ldap3>=2.9            # puro Python, no requiere libldap
aiosmtplib>=3.0
email-validator>=2.1
alembic>=1.13
```

Todas con alternativa: si no se puede instalar `ldap3`, se puede usar `python-ldap` (requiere
compilador y libldap). `segno` evita la dependencia de Pillow que trae `qrcode`.

### B. Endpoints nuevos

```
GET/POST /auth/login
GET/POST /auth/mfa
POST     /auth/logout
GET/POST /auth/recuperar
GET/POST /auth/restablecer
GET      /auth/seguridad
POST     /auth/seguridad/totp/iniciar
POST     /auth/seguridad/totp/confirmar
POST     /auth/seguridad/email/iniciar
POST     /auth/seguridad/email/confirmar
POST     /auth/seguridad/respaldo/regenerar
GET      /auth/sesiones
POST     /auth/sesiones/{id}/revocar
GET      /auth/usuarios                (admin)
POST     /auth/usuarios                (admin)
POST     /auth/usuarios/{id}/rol       (admin)
POST     /auth/usuarios/{id}/estado    (admin)
POST     /auth/usuarios/{id}/reset     (admin)
```

Públicas: `/auth/login`, `/auth/mfa`, `/auth/recuperar`, `/auth/restablecer`, `/salud`,
`/static/*`. Todo lo demás exige sesión.

### C. Configuración nueva (borrador)

```env
EET_AUTH_ENABLED=false
EET_SECRET_KEY=base64:...
EET_AUTH_PEPPER=...
EET_SESSION_COOKIE=__Host-eet_session
EET_SESSION_ABSOLUTE_MIN=720
EET_SESSION_IDLE_MIN=30
EET_COOKIE_SECURE=true
EET_LOGIN_MAX_INTENTOS=5
EET_LOGIN_BLOQUEO_MIN=15
EET_MFA_ISSUER="Validador EET (AEP)"
EET_MFA_WINDOW=1
EET_EMAIL_BACKEND=console
EET_PUBLIC_BASE_URL=http://localhost:8000
EET_LDAP_URL=ldaps://dc01.aep.gov.py:636
EET_LDAP_BIND_DN=CN=svc-eet,OU=Servicios,DC=aep,DC=gov,DC=py
EET_LDAP_BIND_PASSWORD=...
EET_LDAP_BASE_DN=DC=aep,DC=gov,DC=py
EET_LDAP_USER_FILTER=(sAMAccountName={login})
EET_LDAP_ROLE_MAP={...}
EET_LDAP_TIMEOUT=5
```

### D. Criterios de aceptación

- [ ] Ninguna ruta de negocio responde sin sesión válida.
- [ ] Un `asistente` recibe 403 en `/cruces`, `/qaqc`, `/exportar` y al intentar ver otra entrega.
- [ ] Todos los POST validan CSRF; un token ausente devuelve 403 HTML.
- [ ] Login con contraseña incorrecta y usuario inexistente responden igual (texto y tiempo).
- [ ] Tras 5 fallos, la cuenta se bloquea temporalmente y el evento queda auditado.
- [ ] TOTP rechaza un código reutilizado y acepta ±1 paso de reloj.
- [ ] El código por correo expira a los 10 minutos y admite 5 intentos.
- [ ] El enlace de recuperación funciona una sola vez y revoca todas las sesiones.
- [ ] Ningún log, respuesta o tabla expone contraseñas, códigos, tokens o secretos TOTP.
- [ ] Cookies con `HttpOnly`, `Secure` y `SameSite=Lax` en producción.
- [ ] Usuario LDAP válido entra con su contraseña de dominio; con el DC caído no hay fallback local.
- [ ] `admin` no puede dictaminar fichas ni autorizar reemplazos.
- [ ] Auditoría registra actor verificado, IP y resultado en cada evento.
