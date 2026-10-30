# E2E y accesibilidad de las pantallas de autenticación

**Fecha:** 30/10/2026
**Alcance:** flujo completo de autenticación (login, TOTP, códigos de respaldo,
segundo factor, administración de usuarios, auditoría/CSV, recuperación por
correo, permisos por rol) y auditoría axe + teclado de las pantallas de `auth`.
**Referencias:** `docs/runbook-autenticacion.md`, `docs/qa-accesibilidad.md`,
`docs/plan-implementacion-autenticacion.md` (A6.9 y A6.10).

---

## 1. Entorno de la corrida

| Elemento | Valor |
|---|---|
| Aplicación | `uv run uvicorn app.main:app --port 8033` (código del repositorio, sin Docker) |
| Bandera | `EET_AUTH_ENABLED=true`, `EET_COOKIE_SECURE=false` |
| Secretos | `EET_SECRET_KEY` (32+ caracteres) y `EET_AUTH_PEPPER` de prueba |
| Correo | `EET_EMAIL_BACKEND=file`, `EET_EMAIL_DIR` en `%TEMP%\opencode\mail-e2e` |
| Red interna | `EET_ADMIN_INTERNAL_ONLY=false` (para no depender del CIDR en local) |
| Enlaces de correo | `EET_PUBLIC_BASE_URL=http://127.0.0.1:8033` |
| Antibruteforce | `EET_LOGIN_MAX_INTENTOS=10` (el E2E provoca fallos a propósito) |
| Navegador | Chrome 156.0.8078.76 headless vía Playwright 1.64.1 |
| axe-core | 4.14.0 (`node_modules/axe-core`) |
| Base de datos | PostgreSQL 16 + PostGIS (`eet-mapa-aep`, healthy) |

Datos de prueba con prefijo `e2e-`: administrador `e2e-admin`
(rol `admin`, política `totp`) y usuario `e2e-user` (alta por interfaz). Al
final de la corrida se eliminaron **todos** (§8).

> Los dos valores que no son los del encargo literal (`EET_PUBLIC_BASE_URL` y
> `EET_LOGIN_MAX_INTENTOS`) están justificados en §7.2: sin el primero el
> enlace del correo apuntaría a `http://localhost:8000` (contenedor sin
> autenticación) y sin el segundo el propio E2E dispararía el bloqueo de cuenta.

---

## 2. Cómo reproducir

```powershell
# 1) Entorno (ver §1) y app en :8033
$env:EET_AUTH_ENABLED="true"; $env:EET_COOKIE_SECURE="false"
$env:EET_SECRET_KEY="clave-e2e-local-de-32-caracteres-minimo"
$env:EET_AUTH_PEPPER="pimienta-e2e-local"
$env:EET_EMAIL_BACKEND="file"
$env:EET_EMAIL_DIR="$env:TEMP\opencode\mail-e2e"
$env:EET_ADMIN_INTERNAL_ONLY="false"
$env:EET_PUBLIC_BASE_URL="http://127.0.0.1:8033"
$env:EET_LOGIN_MAX_INTENTOS="10"
$env:EET_ADMIN_PASSWORD="ClaveE2EAdmin-2026!"
uv run uvicorn app.main:app --port 8033

# 2) Preparar el entorno (crea e2e-admin si falta y deja su 2FA sin enrolar)
./scripts/e2e-auth-reset.ps1

# 3) E2E (10 pasos, capturas y report.json)
node scripts/e2e-auth.mjs --base http://127.0.0.1:8033

# 4) Accesibilidad: axe a 1440 y 390 + teclado (reutiliza el secreto TOTP que
#    guarda el E2E en %TEMP%\opencode\e2e-totp-secret.txt; si no existe, enrola
#    el administrador y lo guarda ahí)
node scripts/a11y-auth.mjs --base http://127.0.0.1:8033

# 5) Limpieza de datos de prueba y del correo temporal (§8)
```

Artefactos:

| Ruta | Contenido |
|---|---|
| `scripts/e2e-auth.mjs` | E2E de los 10 pasos (Playwright, sin dependencias nuevas) |
| `scripts/a11y-auth.mjs` | axe + teclado de las pantallas de auth |
| `scripts/totp.mjs` | TOTP RFC 6238 con `node:crypto` (compartido por ambos) |
| `scripts/e2e-auth-reset.ps1` | Deja el entorno listo y borra residuos `e2e-*` |
| `docs/qa-visual/e2e-auth/` | 18 capturas, `report.json`, `resumen.md`, `auditoria-e2e.csv` |
| `docs/qa-visual/axe-auth/` | JSON íntegro por pantalla y ancho, `teclado-auth.json`, `resumen.md` |
| `docs/qa-visual/capturas-auth/` | 16 capturas (8 pantallas × 2 anchos) |
| `docs/qa-visual/logs-auth/` | Salidas de las corridas (antes y después) |

---

## 3. E2E — resultado

**10/10 pasos OK** (`docs/qa-visual/e2e-auth/report.json` → `"ok": true`,
`erroresConsola: []`). Detalle completo, con la cadena HTTP de cada
petición, en `docs/qa-visual/e2e-auth/resumen.md`.

| # | Paso | Resultado | HTTP observado |
|---|---|---|---|
| 1 | Login: skip link, etiquetas, credenciales incorrectas | OK | `GET /auth/login` 200 · `POST /auth/login` 200 (mensaje genérico, sin cookie) |
| 2 | Login del admin → enrolamiento obligatorio | OK | `POST /auth/login` 303 → `GET /auth/seguridad` 200 |
| 3 | Clave manual del QR, TOTP generado en Node, 10 códigos de respaldo | OK | `POST /auth/seguridad/totp/iniciar` 200 · `.../confirmar` 200 |
| 4 | Logout, login y `/auth/mfa` con TOTP hasta `/` | OK | `POST /auth/logout` 303 · `POST /auth/login` 303 · `GET /auth/mfa?metodo=totp` 200 · `POST /auth/mfa` 303 · `GET /` 200 |
| 5 | Código de respaldo una vez; repetido falla | OK | `POST /auth/mfa` 303 (aceptado) y `POST /auth/mfa` 200 («Código incorrecto o expirado») |
| 6 | Alta `e2e-user`, rol, baja/alta, reset de contraseña y de 2FA | OK | `POST /auth/usuarios` 200 · `/rol` 303 · `/estado` 303 ×2 · `/reset-password` 200 · `/reset-mfa` 303 |
| 7 | Auditoría: eventos y CSV | OK | `GET /auth/auditoria` 200 · `GET /auth/auditoria.csv` 200 `text/csv; charset=utf-8` con BOM |
| 8 | Recuperación por correo de extremo a extremo | OK | `POST /auth/recuperar` 200 · `GET /auth/restablecer?token=…` 200 · `POST /auth/restablecer` 303 · login con la clave nueva → `/auth/mfa?metodo=email` 200 · `POST /auth/mfa` 303 · `/` 200 |
| 9 | Permisos del admin | OK | menú sin enlaces a `/cruces` · `GET /cruces` **403** «No tiene permiso para esta acción.» |
| 10 | Cierre de sesión | OK | `POST /auth/logout` 303 · `GET /` 200 → `/auth/login`, sin cookie de sesión |

### 3.1 Detalles verificados que no se ven en una captura

- **Mensaje genérico sin enumeración:** una cuenta existente con clave errónea
  y una cuenta inexistente devuelven el **mismo** texto
  (`Usuario o contraseña incorrectos.`) y ninguna deja cookie `eet_session`
  ni `eet_mfa`.
- **TOTP calculado en el propio test** (`node:crypto`, base32 + HMAC-SHA1,
  paso de 30 s, 6 dígitos). El servidor es antirreplay: el generador avanza un
  paso por uso y espera al siguiente ciclo cuando hace falta (se ve en el log
  como «esperando Ns para no reutilizar el paso TOTP»).
- **Código de respaldo de un solo uso:** el mismo código funciona una vez
  (`303 → /`) y al repetirlo el challenge responde `200` con el error genérico
  y **sin** crear sesión.
- **Auditoría:** la vista muestra en la primera página las seis acciones del
  flujo (`user_created`, `role_changed`, `user_disabled`, `user_enabled`,
  `password_reset_admin`, `mfa_reset_admin`). El CSV descargado
  (115 958 bytes, SHA-256 `A3A073B7…A157C8`) empieza con BOM `EF BB BF`,
  declara `text/csv; charset=utf-8` y contiene `user_created`,
  `password_reset_admin` y `e2e-user`.
- **Contraseña temporal:** se captura de `#password-temporal` en la respuesta
  del `POST` (16 caracteres, distinta de la anterior) sin recargar la página.
- **Recuperación por correo:** se lee el `.eml` más reciente de
  `EET_EMAIL_DIR`, se decodifica quoted-printable (el token viaja como
  `=3D…`), se abre el enlace del propio correo y se define la contraseña nueva.
  El acceso posterior exige el código por correo (política `any` sin TOTP), que
  también se lee del mismo buzón: el E2E cubre así los dos canales del segundo
  factor.

Evidencia visual: `01-login.png`, `02-login-error-generico.png`,
`03-seguridad-enrolamiento.png`, `04-seguridad-qr-clave-manual.png`,
`05-codigos-respaldo.png`, `06-mfa-totp.png`, `07-inicio-con-sesion.png`,
`08-respaldo-usado-ok.png`, `09-respaldo-reutilizado-falla.png`,
`10-usuarios-alta.png`, `11-usuarios-password-temporal.png`,
`12-auditoria-eventos.png`, `13-recuperar-solicitud.png`,
`14-restablecer-aplicado.png`, `15-acceso-password-nueva.png`,
`16-inicio-menu-admin.png`, `17-cruces-403-admin.png`,
`18-logout-redirige-login.png`.

---

## 4. Accesibilidad — axe-core

Pantallas auditadas (a 1440 y 390 px): `/auth/login`, `/auth/recuperar`,
`/auth/restablecer?token=invalido` (anónimas) y, con sesión de administrador,
`/auth/seguridad` (tres estados: sin activar, códigos recién generados y
enrolado), `/auth/mfa` (con challenge pendiente) y `/auth/usuarios`,
`/auth/auditoria`.

### 4.1 Antes de las correcciones (1440 px)

| Regla | Impacto | Pantallas | Nodos | Detalle |
|---|---|---|---|---|
| `color-contrast` | serious | `/auth/login`, `/auth/recuperar`, `/auth/restablecer?token=invalido` | 3 | `footer > p` del layout de auth: `#64748b` sobre `#f4f7f6` = **4.41:1** (mínimo 4.5:1) |
| `landmark-unique` | moderate | `/auth/usuarios` | 2 | Dos `<nav aria-label="Ruta de navegación">` (topbar + cabecera) y una `<section>` homónima del `div.table-wrap role="region"` («Cuentas registradas») |
| `landmark-unique` | moderate | `/auth/auditoria` | 1 | Dos `<nav aria-label="Ruta de navegación">` |

**Total: 5 reglas / 6 nodos** (3 `serious`, 2 `moderate`). Evidencia:
`docs/qa-visual/logs-auth/a11y-run-2.log` (contraste) y
`a11y-run-3.log` (landmarks).

> La pasada de 390 px del estado *anterior* no llegó a completarse (el script se
> detuvo en el challenge por un fallo propio, ya corregido). Los dos defectos
> son independientes del ancho —un color de texto y un nombre de landmark— y la
> pasada final sí cubre ambos anchos.

### 4.2 Después de las correcciones

```
[a11y-auth] 16 auditorías · 0 reglas / 0 nodos · critical=0 serious=0 moderate=0 minor=0
```

**0 violaciones `critical`, `serious`, `moderate` y `minor`** en 16 auditorías
(8 estados de pantalla × 2 anchos). Detalle por pantalla y ancho en
`docs/qa-visual/axe-auth/resumen.md` y el resultado íntegro de axe (JSON) en
`docs/qa-visual/axe-auth/<slug>-<ancho>.json`.

### 4.3 Reglas incompletas (criterio humano)

axe dejó **1 nodo incompleto** de `color-contrast` en `/auth/seguridad` a
390 px: la celda del agente de usuario de la tabla de sesiones, que aparece
recortada (`elmPartiallyObscured`). Medido sobre el estilo calculado:

| Elemento | Color / fondo | Contraste | Criterio |
|---|---|---|---|
| `td[title]` (agente de usuario) | `rgb(15,23,42)` sobre `rgb(255,255,255)` | **17.85:1** | 1.4.3 AA (4.5:1) ✔ |

---

## 5. Accesibilidad — teclado

`docs/qa-visual/axe-auth/teclado-auth.json` (mismo dato en el resumen de axe):

| Pantalla | Foco inicial | Primer Tab | Skip link | Paradas | Trampa | Ocultos | Enter envía |
|---|---|---|---|---|---|---|---|
| login @1440 | `#campo-login` (autofocus) | `#campo-password` visible con anillo | alcanzable con `Shift+Tab`, visible | 7 | no | 0 | sí |
| login @390 | `#campo-login` | `#campo-password` visible con anillo | alcanzable, visible | 7 | no | 0 | sí |
| mfa @1440 | `#campo-codigo` | `summary` («Usar un código de respaldo») visible | alcanzable, visible | 6 | no | 0 | sí |
| mfa @390 | `#campo-codigo` | `summary` visible | alcanzable, visible | 6 | no | 0 | sí |
| usuarios @1440 | `body` | primer enlace visible con anillo | alcanzable, visible | 21 | no | 0 | sí |
| usuarios @390 | `body` | primer enlace visible con anillo | alcanzable, visible | 18 | no | 0 | sí |

Notas:

- El login y el challenge llevan `autofocus` en el primer campo (decisión de
  producto, no un defecto): el **skip link sigue siendo el primer tabulable del
  documento** y se alcanza con `Shift+Tab`; el primer `Tab` deja un anillo de
  foco visible. Se comprobó en ambos anchos y en el E2E (paso 1).
- El envío con `Enter` se prueba capturando el evento `submit` y cancelándolo:
  no se envía nada ni se modifica la base.

---

## 6. Correcciones aplicadas

| # | Archivo | Cambio | Motivo |
|---|---|---|---|
| 1 | `app/static/css/input.css` | `.auth-footer`: `text-ink-400` → `text-ink-600` | `color-contrast` 4.41:1 → **7.04:1** (1.4.3) |
| 2 | `app/templates/auth/usuarios.html`, `app/templates/auth/auditoria.html` | Se quita `breadcrumb=` de `ui.page_header` | El shell ya pinta la miga desde `migas`: había **dos** breadcrumbs idénticos (visual y `landmark-unique`) |
| 3 | `app/templates/auth/usuarios.html` | `ui.table_wrap("Cuentas registradas")` → `"Listado de cuentas registradas"` | `landmark-unique`: la región de la tabla y la `<section>` compartían nombre accesible |
| 4 | `app/auth/csrf.py` | `_origen_valido` acepta `Origin: null` **solo** si `Sec-Fetch-Site` es `same-origin` (o ausente) | Bloqueante encontrado por el E2E, ver §7.1 |
| 5 | `tests/test_auth_csrf.py` | 2 pruebas nuevas del origen opaco (mismo origen → 303; `cross-site` → 403) | Fijar el comportamiento de #4 |

Los cambios 1–3 son de plantilla/CSS y no alteran textos normativos, datos ni
lógica de negocio; el 4 es la única modificación fuera de ese alcance y está
justificada en §7.1.

Tras recompilar (`npm run css:build`) e **reconstruir la imagen Docker**
(`docker compose build app && docker compose up -d app`), se verificó con la
bandera apagada: `GET /` → **200**, `/salud` → 200 `{"estado":"ok"}`,
`/static/css/app.css` → 200 con la regla `.auth-footer{…color:var(--color-ink-600)}`
y las clases de auth (`clave-manual__valor`, `codigos-lista`, `qr-envoltorio`,
`auth-detalle`, `table-wrap`, `page-header__title`).

---

## 7. Hallazgos e incidencias

### 7.1 Bloqueante: ningún formulario funcionaba en un navegador real (CSRF + `Origin: null`)

- **Síntoma:** el primer E2E se detuvo en el paso 1: `POST /auth/login` →
  **403 «Token CSRF inválido o ausente.»** con el token correcto en el
  formulario y en la cookie.
- **Causa raíz:** `SecurityHeadersMiddleware` envía `Referrer-Policy:
  no-referrer` (endurecimiento A6a, para no filtrar tokens en el `Referer`).
  Por la especificación de Fetch, con esa política el navegador envía
  **`Origin: null`** en *todos* los POST de formulario, incluso del mismo
  origen. `csrf._origen_valido` recibía `null`, no podía parsearlo y devolvía
  `False`, así que **cualquier** POST desde Chrome/Firefox fallaba con 403. La
  suite no lo detectaba porque `TestClient`/`httpx` no envían la cabecera.
- **Evidencia:** cabeceras capturadas por Playwright —
  `content-type: application/x-www-form-urlencoded`, **`origin: null`**, sin
  `referer`; el mismo POST con `Origin: http://127.0.0.1:8033` devuelve 200.
- **Corrección:** un origen opaco (`null` o no parseable) se acepta **solo** si
  `Sec-Fetch-Site` confirma `same-origin` (cabecera que fija el navegador y que
  una página no puede falsear) o si no viene (clientes que no son navegadores).
  Un origen distinto sigue devolviendo 403 y un `Origin: null` con
  `Sec-Fetch-Site: cross-site` también. El control principal sigue siendo el
  token (double-submit / por sesión), que no cambió.
- **Alternativa descartada:** cambiar `Referrer-Policy` a `same-origin` haría
  que el `Referer` de la propia página de restablecimiento viajara con el token
  en las peticiones de `/static/*` (se filtraría en los logs del proxy).
- **Cobertura:** `test_origin_opaco_del_mismo_origen_es_aceptado` y
  `test_origin_opaco_de_otro_sitio_es_403` (integración).

### 7.2 Desviaciones de entorno (no del código)

| Desviación | Motivo |
|---|---|
| `EET_PUBLIC_BASE_URL=http://127.0.0.1:8033` | Por defecto es `http://localhost:8000`: el enlace del correo habría apuntado al contenedor, que corre **sin** autenticación. |
| `EET_LOGIN_MAX_INTENTOS=10` | El paso 1 y el paso 5 provocan fallos deliberados; con el umbral de 5 la cuenta de prueba se bloqueaba a mitad del flujo. El antibruteforce tiene cobertura propia en la suite. |
| `bypassCSP: true` en el contexto de Playwright | La CSP con nonce impide inyectar `axe-core`; no altera DOM, estilos ni semántica. (Los scripts de accesibilidad anteriores a A6a no lo necesitaban porque entonces no había CSP.) |
| Secreto TOTP en `%TEMP%\opencode\e2e-totp-secret.txt` | Permite que la auditoría reutilice el enrolamiento sin volver a mostrarlo; nunca se versiona. |

### 7.3 Incidencias del propio arnés (corregidas)

- El primer `Tab` del login no es el skip link porque el campo de usuario lleva
  `autofocus`; la comprobación se reescribió para medir lo que exige WCAG
  (primer tabulable del documento, alcanzable, foco visible), no una intuición.
- El `.eml` del restablecimiento usa quoted-printable: el token llega como
  `=3D…`. El lector ahora decodifica QP/base64 por variantes y descarta
  coincidencias parciales.
- El E2E enviaba el formulario de **filtros** (GET) en lugar del de alta porque
  compartían `action="/auth/usuarios"`; se distingue por `method="post"`.
- El escucha que captura el `submit` para la prueba de `Enter` bloqueaba los
  envíos reales posteriores; ahora se retira tras la primera captura.

---

## 8. Limpieza y estado de la base

Al terminar se eliminaron los datos de prueba (`docs/qa-visual/logs-auth/` y
`scripts/e2e-auth-reset.ps1` documentan el procedimiento):

| Elemento | Filas borradas |
|---|---|
| `usuarios` (`e2e-admin`, `e2e-user`) | 1 + 1 (el segundo ya había caído en el reset) |
| `sesiones` / `auth_tokens` | 4 / 3 |
| `mfa_totp` / `recovery_codes` | 1 / 10 |
| `login_attempts` (`identificador LIKE 'e2e%'`) | 4 |
| `email_outbox` (`destinatario LIKE 'e2e%'`) | 21 |
| `auditoria` (actor `cli`, `usuario:<id e2e>`, `login_fail` con identificador `e2e*`, entidad del usuario) | 169 |

Conteos de negocio **intactos** después de la limpieza:

| Tabla | Antes | Después |
|---|---|---|
| `resultados_cruce` | 15 400 | **15 400** |
| `fichas_qaqc` | 221 | **221** |
| `entregas` | 6 | **6** |
| `puntos_malla` | 440 | **440** |
| `usuarios` | 5 (+2 de prueba) | **5** |
| `sesiones`, `auth_tokens`, `mfa_totp`, `recovery_codes` | — | **0** |

También se borró el directorio de correo temporal (`mail-e2e`) y se detuvo
únicamente el `uvicorn` de `:8033`.

---

## 9. Pendientes y limitaciones

1. **Lector de pantalla (humano).** NVDA + Firefox y VoiceOver + Safari sobre
   `/auth/login`, `/auth/mfa`, `/auth/seguridad` y `/auth/usuarios`: anuncio de
   `role="status"`/`role="alert"` tras un error, lectura de la clave manual del
   autenticador y de la lista de códigos de respaldo. axe valida la estructura
   que consumen los lectores, no el anuncio real.
2. **Revisión visual humana** de los estados (badges «Activado/Sin activar/No
   habilitado por su política», código temporal y códigos de respaldo) para
   confirmar que el estado no se comunica solo por color.
3. **`/auth/cambiar-password`** no estaba en el alcance pedido y no se auditó
   con axe (sí se ejercita indirectamente: `must_change_password` redirige ahí
   y el shell del admin lo enlaza).
4. **Variante de correo de `/auth/mfa`** (`?metodo=email`): el E2E la ejercita
   de extremo a extremo, pero la captura axe corresponde a la variante TOTP.
   Es la misma plantilla con el bloque de correo activo; conviene añadirla a la
   próxima pasada.
5. **A6.12** (ensayo de despliegue con `auth_enabled=true` en staging) sigue
   fuera de esta corrida: aquí se validó el flujo en local con HTTPS simulado
   (`cookie_secure=false`), no con TLS real ni proxy.
6. Los estados `seguridad-sin-activar` y `seguridad-codigos-nuevos` solo son
   alcanzables con un administrador sin enrolar: para volver a auditarlos hay
   que ejecutar `scripts/e2e-auth-reset.ps1` antes de la pasada.
