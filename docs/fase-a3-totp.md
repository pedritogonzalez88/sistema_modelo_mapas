# Fase A3 — Segundo factor TOTP, códigos de respaldo y sesiones revocables

> Estado: **entregada**. Complementa `docs/plan-implementacion-autenticacion.md`
> §4 (Fase A3) y `docs/analisis-autenticacion.md` §6.3 (flujos) y §6.7
> (secretos). No incluye código por correo, recuperación por correo ni LDAP:
> eso corresponde a las fases A4 y A5.

## 1. Alcance entregado

| Pieza | Dónde |
|---|---|
| Contador de intentos del challenge | `migrations/versions/0003_mfa_challenge.py`, `AuthToken.mfa_attempts` |
| Enrolamiento TOTP (secreto 160 bits, QR `segno`, clave manual) | `app/auth/service.py`, `app/auth/router.py` |
| Confirmación con ventana ±1 y antirrepetición | `verificar_totp(...)`, `last_used_step` |
| 10 códigos de respaldo (HMAC + pepper, un solo uso) | `recovery_codes`, `generar_codigos_respaldo`, `hmac_code` |
| Challenge de login (5 min, 5 intentos) | `auth_tokens` propósito `mfa` + cookie `eet_mfa` |
| Política por rol y gate de enrolamiento forzado | `app/auth/middleware.py` |
| Pantallas | `app/templates/auth/mfa.html`, `app/templates/auth/seguridad.html` |
| Pruebas de integración | `tests/test_auth_mfa.py` (21 casos) |

CLI: `crear-admin` deja `mfa_policy='totp'` (ya lo hacía) y `crear-usuario`
acepta `--mfa-policy {disabled,email,totp,any}`.

## 2. Esquema

`0002` ya había creado `mfa_totp` (`secret_enc`, `key_id`, `confirmed_at`,
`last_used_step`), `recovery_codes`, `auth_tokens` y `sesiones.mfa_at`. La
Fase A3 solo agrega:

```sql
ALTER TABLE auth_tokens ADD COLUMN mfa_attempts INTEGER NOT NULL DEFAULT 0;
```

- `NOT NULL DEFAULT 0` para no reescribir filas existentes (tokens de otros
  propósitos quedan con 0 y el contador solo se usa con `purpose='mfa'`).
- El índice `ix_auth_tokens_token_hash` (único) ya existía y es el que resuelve
  la búsqueda del challenge por hash: no se duplica.
- `downgrade()` elimina la columna; probado con `alembic upgrade head` y
  `alembic check` (sin operaciones pendientes).

## 3. Enrolamiento

```
GET  /auth/seguridad              estado, códigos restantes, sesiones activas
POST /auth/seguridad/totp/iniciar genera secreto (pyotp, 160 bits), lo cifra
                                  con SecretBox, confirmed_at=NULL y pinta el QR
POST /auth/seguridad/totp/confirmar verifica el código, activa y muestra 10
                                  códigos de respaldo una única vez
POST /auth/seguridad/respaldo/regenerar exige TOTP vigente, invalida los no
                                  usados y emite 10 nuevos
POST /auth/sesiones/{id}/revocar  revoca otra sesión propia
```

Detalles:

- El QR se genera con `segno.make(uri).svg_inline(scale=4)` y se inyecta inline
  (`| safe`); el `otpauth://` **no** se escribe en el HTML. La imagen queda
  dentro de un contenedor `aria-hidden="true"` y la alternativa accesible es la
  clave manual agrupada en bloques de 4.
- Las respuestas con secreto pendiente, códigos o sesiones se sirven con
  `Cache-Control: no-store`.
- Los códigos de respaldo se muestran en una lista y se pueden descargar como
  `.txt` con un enlace `data:` generado en el servidor: no hay endpoint extra
  que pueda volver a exponerlos.
- Al confirmar, la sesión en curso se marca con `mfa_at` (el usuario acaba de
  demostrar el segundo factor) y `usuario.mfa_enrolled_at` queda seteado. No se
  cierran las demás sesiones.

## 4. Challenge de login

```
POST /auth/login  (contraseña correcta)
  ├─ TOTP confirmado y policy ∈ {totp, any}
  │    └─ no crea sesión: auth_tokens(purpose='mfa', sha256, 5 min, ip,
  │       mfa_attempts=0) + cookie eet_mfa/__Host-eet_mfa → 303 /auth/mfa?next=
  ├─ policy == 'totp' y sin enrolar
  │    └─ crea sesión normal → 303 /auth/seguridad (enrolamiento obligatorio)
  └─ policy ∈ {disabled, email} o 'any' sin TOTP → sesión normal (A4: correo)
GET  /auth/mfa   sin challenge → 303 /auth/login?error=desafio_expirado
POST /auth/mfa   TOTP o código de respaldo; éxito → sesión con mfa_at + 303 next
```

- El challenge es una fila de `auth_tokens`: solo se guarda el `sha256`; la
  cookie (HttpOnly, `SameSite=Lax`, `Secure` según configuración) lleva el token
  en claro y `max_age=300`.
- Un login nuevo invalida los challenges pendientes del usuario: nunca hay dos
  tokens vivos para la misma cuenta.
- El contador sube en cada POST. Al llegar a 5 fallos el challenge se marca
  `used_at` y se vuelve al login con `?error=mfa_bloqueado`; el usuario debe
  empezar por la contraseña.
- Mensaje de fallo siempre genérico («Código incorrecto o expirado.»), sin
  distinguir TOTP de respaldo ni si el challenge existía.
- Auditoría: `mfa_ok` (con `metodo`), `mfa_fail` (con `intentos` y `motivo`),
  `mfa_enrolled`, `mfa_recovery_regenerated`, `session_revoked`. Ni el secreto,
  ni el código, ni el token aparecen en la auditoría (además del filtro de
  claves reservadas de `app/auth/audit.py`).

## 5. Política y gate

`RUTAS_PUBLICAS` suma `/auth/mfa` (entre la contraseña y el segundo factor el
usuario todavía no tiene sesión).

Con sesión válida, en `AuthGateMiddleware`, en este orden:

1. `must_change_password` → solo `/auth/cambiar-password` y `/auth/logout`.
2. `mfa_policy == 'totp'` y `mfa_enrolled_at is None` → solo
   `/auth/seguridad`, `/auth/seguridad/*`, `/auth/logout` y `/static/*`; el
   resto redirige a `/auth/seguridad`.

El gate decide con `mfa_enrolled_at` (no descifra secretos por petición).

## 6. Decisiones

| Tema | Decisión | Motivo |
|---|---|---|
| Revocar la sesión actual | **No permitido** en `/auth/sesiones/{id}/revocar` (redirige con `?error=sesion_actual`); la propia se cierra con «Salir» | Evita dejar al usuario en una sesión revocada a mitad de navegación y mantiene un único camino de cierre (el logout, que además borra el challenge) |
| Re-enrolar con TOTP activo | **Bloqueado** en `iniciar`: responde con un aviso y remite al reset asistido | Reemplazar el secreto confirmado por uno pendiente dejaría la cuenta sin segundo factor si el usuario abandona el asistente |
| Intentos del challenge | 5; el quinto fallo invalida el challenge | Alineado con `login_max_intentos` y con §6.3 |
| Código usado en la confirmación | Se registra en `last_used_step` | Antirrepetición: el mismo código no sirve para otro login en la misma ventana (hay que esperar ≤30 s) |
| Desafíos simultáneos | Uno por usuario | Evita acumular tokens vivos al reintentar el login |
| Flash `mfa_activado` / `mfa_recovery_regenerated` | No se usan: los códigos se muestran en la misma respuesta | Un `303` + flash perdería la única oportunidad de verlos. Sí se usa `session_revoked` |
| `crear-usuario --mfa-policy` | Sin valor explícito toma la política del rol (`admin`/`supervision` → `totp`, resto `any`) | Mantiene la regla «2FA por rol» del análisis §2 para roles críticos |

## 7. Pruebas

`tests/test_auth_mfa.py` (marcadas `integration`, 21 casos):

1. login con TOTP confirmado → 303 `/auth/mfa`, sin cookie de sesión, challenge
   en base con hash de 64 hex;
2. código correcto → sesión con `mfa_at`, `last_used_step` actualizado,
   `mfa_ok` auditado y `next` respetado;
3. 4 fallos conservan el challenge y el 5.º lo invalida
   (`?error=mfa_bloqueado`, `mfa_fail` ×5); después hace falta la contraseña;
4. replay del mismo código rechazado;
5. código de respaldo válido una sola vez (y tolerancia a minúsculas/espacios);
6. política `totp` sin enrolar: sesión creada pero `/`, `/entregas`, `/cruces`,
   `/qaqc` y `/exportar` redirigen a `/auth/seguridad`;
7. revocar otra sesión la cierra y desaparece del listado; la propia y las de
   otro usuario se rechazan;
8. QR + clave manual presentes, `otpauth://` ausente, códigos mostrados una vez
   y no recuperables por `GET /auth/seguridad`; solo hay HMAC en la base;
9. CSRF obligatorio en los cuatro POST nuevos y en `/auth/mfa`.

Totales tras A3: **196 unitarias** y **177 de integración** (21 nuevas), todas
en verde.

## 8. Verificación manual (uvicorn :8026)

Con `EET_AUTH_ENABLED=true EET_COOKIE_SECURE=false`, admin creado por CLI:

```
login con política totp sin enrolar -> 303 /auth/seguridad
GET /auth/seguridad -> 200 cache=no-store
POST totp/iniciar -> 200 qr=True clave_manual=True
POST totp/confirmar -> 200 codigos=10
login con TOTP -> 303 /auth/mfa sin_sesion=True
POST /auth/mfa totp -> 303 / sesion=True
POST /auth/mfa respaldo -> 303 sesion=True
5 fallos -> [200, 200, 200, 200, 303] ubicacion=/auth/login?error=mfa_bloqueado
```

Datos de prueba eliminados al final (usuario, sesiones, tokens, códigos,
intentos y auditoría asociada). Con la bandera apagada, `/` responde 200 sin
sesión.
