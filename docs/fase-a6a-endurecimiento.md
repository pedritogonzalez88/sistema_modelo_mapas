# Fase A6a — Endurecimiento para exposición a internet (informe)

> Alcance: **solo A6a** del plan (`docs/plan-implementacion-autenticacion.md`
> A6.1–A6.6 y A6.11 en su parte de configuración). **No** incluye la UI de
> administración de usuarios/auditoría (A6b) ni los E2E con navegador (A6c).
> Guía operativa: `docs/despliegue-autenticacion.md`.

Estado previo: Fases A0–A5 completas, `208` unitarias + `213` integración en
verde, Alembic `0003 (head)`, `EET_AUTH_ENABLED=false` por defecto.

---

## 1. Entregado

| ID | Tarea | Dónde |
|---|---|---|
| A6.1 | Rate limiting por IP en login, 2FA y recuperación | `auth/service.py`, `auth/router.py` |
| A6.2 | Cabeceras: CSP con nonce, HSTS, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `frame-ancestors 'none'` | `auth/middleware.py` |
| A6.3 | Nonce por request en los 7 `<script>` inline | `templates/*` |
| A6.4 | Límite de subida (`max_upload_mb`) con rechazo temprano | `routers/{entregas,malla,mapa}.py` |
| A6.5 | `/docs` y `/openapi.json` ocultos en producción | ya existía; ahora **validado** en `prod` |
| A6.6 | Proxy de confianza (`X-Forwarded-Proto/For`) | `core/config.py`, `main.py` |
| A6.11 | Runbook de rotación, backups y checklist | `docs/despliegue-autenticacion.md` |
| — | Validación de `EET_ENVIRONMENT` (prod falla, staging advierte) | `core/config.py` |
| — | `utcnow()` sin `DeprecationWarning` | `core/security.py`, `auth/service.py`, `auth/router.py` |

---

## 2. Validación de producción

`Settings` gana dos campos:

```python
environment: str = "dev"          # dev | staging | prod
trusted_proxy_ips: str = ""       # CIDR separados por coma; vacío = no confiar
```

y dos propiedades derivadas: `trusted_proxy_networks` (lista de
`ip_network`, error explícito si un CIDR es inválido) y `trusted_proxy_hosts`
(la lista cruda que consume uvicorn).

`model_validator(mode="after")` valida en este orden: entorno conocido → secretos
de auth presentes → requisitos del entorno → LDAP → formato de mapa de roles,
redes internas, proxy y claves.

Con `environment="prod"` **y** `auth_enabled=true` falla al arrancar si:

| Requisito | Mensaje (sin el valor) |
|---|---|
| `cookie_secure` | `EET_COOKIE_SECURE debe ser true (cookies Secure sobre HTTPS).` |
| `public_base_url` | `EET_PUBLIC_BASE_URL debe comenzar con https://` |
| `email_backend` | `EET_EMAIL_BACKEND debe ser 'smtp'` |
| `smtp_host` | `EET_SMTP_HOST es obligatorio con EET_EMAIL_BACKEND=smtp.` |
| `expose_docs` | `EET_EXPOSE_DOCS debe ser false` |
| `secret_key` | `EET_SECRET_KEY debe tener al menos 32 caracteres.` |
| `auth_pepper` | `EET_AUTH_PEPPER debe tener al menos 16 caracteres.` |
| `session_cookie` | `EET_SESSION_COOKIE debe comenzar con '__Host-'` |

Todos los incumplimientos se agregan en **un solo** `ValueError` (el operador ve
todo el paquete junto, no de a uno). `staging` recorre la misma lista y emite un
`logging.warning` por cada punto. `dev` no exige nada.

La validación **solo** se aplica con la autenticación activa, para no romper el
rollout gradual con la bandera apagada.

---

## 3. Cabeceras y CSP

`SecurityHeadersMiddleware` genera `secrets.token_urlsafe(16)` **antes** de
`call_next`, lo deja en `request.state.csp_nonce` y lo usa en la cabecera. Las
plantillas lo consumen con `nonce="{{ request.state.csp_nonce }}"`.

```
Content-Security-Policy: default-src 'self'; base-uri 'self'; form-action 'self';
  frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; font-src 'self';
  style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-<16B>'; connect-src 'self'
```

| Cabecera | Valor | Condición |
|---|---|---|
| `Content-Security-Policy` | la de arriba | siempre |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | solo `request.url.scheme == "https"` |
| `X-Content-Type-Options` | `nosniff` | siempre |
| `Referrer-Policy` | `no-referrer` | siempre |
| `X-Frame-Options` | `DENY` | siempre |
| `Permissions-Policy` | `geolocation=(), camera=(), microphone=()` | siempre |
| `Cross-Origin-Opener-Policy` | `same-origin` | siempre |
| `Cross-Origin-Resource-Policy` | `same-origin` | siempre |
| `Cache-Control` | `no-store` | `/auth/*`, con `setdefault` (no pisa uno más específico) |

### Nonce en los scripts inline (7)

| Plantilla | Bloque |
|---|---|
| `base.html` | marca `classList.add("js")` en `<head>` |
| `auth/base_auth.html` | ídem para el layout sin sidebar |
| `entregas.html` | validación progresiva de la zona de carga |
| `entrega_preview.html` | copiar hash y confirmar rechazo |
| `exportar.html` | estado de espera del botón de descarga |
| `qaqc_detalle.html` | filas de evidencia y confirmación de transiciones |
| `auth/mfa.html` | cuenta atrás de reenvío del código por correo |

Los dos `<script src="{{ url_for('static', …) }}">` no llevan nonce: se resuelven
con `script-src 'self'`. Verificado con `grep`: **no** quedan manejadores inline
(`onclick=`, `onload=`, `onsubmit=`, …) ni `javascript:`.

### Por qué `'unsafe-inline'` en estilos

El panel y las barras de progreso usan `style="width:NN%"` con porcentajes
calculados por el servidor (dona de distribución y avance de cargas). Convertirlo
exigiría CSS dinámico por respuesta o cientos de clases `w-[NN%]`. Los valores
**no** provienen de entrada del usuario (son porcentajes de los datos del
proyecto), por lo que el riesgo es nulo y el coste de migrarlo alto. `script-src`
nunca lo admite. Ver `docs/despliegue-autenticacion.md` §7 para la ruta de salida
(variables CSS) si algún día se quiere retirar.

---

## 4. Proxy de confianza

`create_app()` monta `uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware`
como el middleware **más externo**, y solo cuando `EET_TRUSTED_PROXY_IPS` no está
vacío:

```python
app.add_middleware(AuthGateMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
if settings.trusted_proxy_hosts:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxy_hosts)
```

Con la variable vacía no se monta nada: `request.client.host` es la IP real de la
conexión y una cabecera `X-Forwarded-For` falsificada **no** puede mover el
contador del rate limiting. uvicorn 0.54 admite CIDR en `trusted_hosts`.

---

## 5. Rate limiting (servidor)

Sin Redis ni memoria compartida: todo se cuenta sobre las tablas existentes, así
que sobrevive a reinicios y es consistente entre workers.

| Punto | Límite | Fuente | Al exceder |
|---|---|---|---|
| Login por cuenta | `login_max_intentos` (5) | `usuarios.failed_attempts`/`locked_until` | bloqueo 15 min, `login_locked` |
| Login por IP | `login_ip_max_15min` (20) / 15 min | `login_attempts` (`factor=password`) | bloqueo 15 min, `login_ip_locked` |
| Challenge 2FA | 5 intentos | `auth_tokens.mfa_attempts` | challenge invalidado → login |
| 2FA por IP | 10 fallos / 15 min | `login_attempts` (`factor=mfa`) | challenge invalidado → login |
| Envío de código | 5 / hora / usuario (+60 s de espera) | `mfa_email_codes` | mensaje de límite, sin envío |
| Envío de código por IP | 10 / hora | `mfa_email_codes.ip` | «Intente más tarde.», sin envío |
| Recuperación por cuenta | 3 / hora | `auth_tokens` (`purpose=reset`) | respuesta genérica |
| Recuperación por IP | 10 / hora (enlaces) **+** 10 sondeos fallidos / hora | `auth_tokens` + `login_attempts` (`factor=recovery`) | respuesta genérica |

Detalles de implementación:

- **Login**: el corte por IP se evalúa *antes* de `buscar_usuario` y de verificar
  la contraseña (coste constante ante un ataque) y **no** registra un intento
  nuevo: si lo hiciera, la tabla crecería sin control durante el propio bloqueo.
  El mensaje es el mismo que el del bloqueo por cuenta («Demasiados intentos
  fallidos…»), sin revelar si la cuenta existe.
- **`ip is None` desactiva el límite por IP**: las pruebas, la CLI y las tareas
  internas llaman al servicio sin cliente; bloquear `None` cerraría el login.
- **2FA**: cada fallo se registra en `login_attempts` con `factor='mfa'` (antes
  solo quedaba en auditoría, que no sirve para contar). El tope por IP complementa
  el del challenge porque un atacante puede encadenar challenges de varias cuentas.
- **Envío**: el límite por IP se consulta **después** del de la cuenta, de modo
  que el cooldown y el tope por usuario se mantienen intactos; el mensaje es
  neutro y no distingue el motivo real.
- **Recuperación**: se cerró un hueco. Antes solo se contaban los enlaces
  *emitidos* por IP; sondear identificadores inexistentes no dejaba rastro y el
  tope nunca se activaba. Ahora cada intento sobre una cuenta no elegible queda en
  `login_attempts` (`factor='recovery'`) y 10 sondeos en una hora bloquean la IP.
  La respuesta sigue siendo `RESPUESTA_RECUPERACION` idéntica.

---

## 6. Límite de subida

En `entregas.upload`, `malla.upload` y `mapa.upload`, antes de procesar nada:

```python
_validar_tamano(file.size)          # lo informa el parser multipart, sin leer
contenido = await file.read()
_validar_tamano(len(contenido))     # un cliente que no declare el tamaño no escapa
```

Al exceder `settings.max_upload_mb * 1024 * 1024` se lanza
`HTTPException(413, "El archivo supera el límite de X MB.")`, que el manejador de
errores convierte en `error.html` con el título nuevo **«Archivo demasiado
grande»** (y un texto que sugiere reducir o dividir el archivo).

El rechazo temprano es la primera barrera; el proxy debe tener además
`client_max_body_size` alineado (§4 de la guía). El guardia se repite en los tres
routers porque son módulos independientes y el helper es de cuatro líneas: se
prefirió la duplicación mínima antes que crear un módulo nuevo fuera del alcance
de la fase.

---

## 7. Fechas: `utcnow()` y `DeprecationWarning`

`app/core/security.py` expone:

```python
def utcnow() -> datetime:
    """UTC naive, compatible con las columnas DateTime del esquema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

Se reemplazaron **28** llamadas a `datetime.utcnow()` (26 en `auth/service.py`,
2 en `auth/router.py`). La semántica de las columnas **no** cambia: siguen siendo
`DateTime` naive con hora UTC.

| Suite | Antes | Después |
|---|---|---|
| `pytest -q` (unitarias) | 208 pasan · **3** warnings | 208 pasan · **3** warnings |
| `pytest -m integration -q` | 213 pasan · **712** warnings | 246 pasan · **92** warnings |

Reducción del **87 %**. De los 92 restantes: 3 son de librerías
(`StarletteDeprecationWarning` de `starlette.testclient`, `ldap3`/`pyasn1`), ~20
son las **3** llamadas de `app/auth/mailer.py` (archivo fuera del alcance
permitido de esta fase) y el resto son `datetime.utcnow()` **dentro de los
propios archivos de prueba**. Ningún warning propio queda en `auth/service.py`,
`auth/router.py` ni `core/security.py` (verificado ejecutando
`test_auth_login` + `test_auth_roles` + `test_auth_csrf`: 6 warnings, todos
externos o de `mailer.py`).

---

## 8. Pruebas

`tests/test_auth_hardening.py` — **33 casos** de integración:

| Bloque | Casos |
|---|---|
| CSP/nonce | CSP completa, nonce del header == nonce de cada script inline, nonce distinto por petición, misma garantía en página autenticada, `script-src` sin `'unsafe-inline'`, `style-src` con él |
| Cabeceras | nosniff, referrer, XFO, Permissions-Policy, COOP, CORP; HSTS ausente en HTTP y presente con `base_url="https://testserver"`; `no-store` en `/auth/*` y no en `/salud` |
| Producción | configuración completa válida, 8 casos parametrizados (uno por requisito), el error no filtra secretos, sin auth no exige endurecimiento, `staging` solo advierte, entorno desconocido falla, parseo de `trusted_proxy_ips` y CIDR inválido |
| Rate limit | login bloqueado por IP sin revelar cuenta (servicio), bloqueo tras agotar los fallos (HTTP), challenge 2FA por IP, `/auth/mfa` → login con `error=mfa_bloqueado`, envío por IP sin emitir código, recuperación con sondeos fallidos |
| Subida | 413 HTML en entregas, malla y mapa (`max_upload_mb=0`), y que con límite holgado no haya 413 |
| Bandera apagada | `/` y `/entregas` 200 con cabeceras de endurecimiento |

Totales tras la fase: **208 unitarias + 246 integración = 454 en verde**.

Las pruebas que siembran `login_attempts` o `mfa_email_codes` los borran en el
teardown (fixture `limpieza`), incluida la IP ficticia `testclient` que usa
Starlette, para no contaminar contadores entre casos.

### Ajuste en una prueba existente

`tests/test_auth_recovery.py` comparaba el HTML de cuatro respuestas de
`/auth/recuperar` para demostrar que no hay enumeración. Con un nonce por
petición el HTML ya no es idéntico byte a byte, así que se agregó el helper
`_sin_nonce()` (normaliza `nonce="…"`) y las dos aserciones comparan el cuerpo con
esa normalización. **La garantía no se debilita**: el contenido sigue dependiendo
solo del flujo, nunca del identificador probado, y el nonce es un valor aleatorio
ajeno a la entrada.

---

## 9. Verificación manual

| Comprobación | Resultado |
|---|---|
| `uv run python -c "import app.main"` | `ok` |
| `pytest -q` | 208 pasan · 3 warnings |
| `pytest -m integration -q` | 246 pasan · 92 warnings |
| `curl -I http://127.0.0.1:8029/` (auth on) | 303 a `/auth/login?next=%2F`, CSP con nonce, **sin** HSTS |
| `curl -I http://127.0.0.1:8029/auth/login` | CSP, `no-store`, `no-referrer`, `DENY`, `nosniff`, Permissions-Policy, COOP, CORP |
| nonce header vs. body (misma respuesta) | coinciden (`nonce-<x>` ↔ `nonce="<x>"`) |
| `/entregas` autenticada | los dos `<script nonce>` con el mismo nonce del header |
| `TestClient(base_url="https://testserver")` | `strict-transport-security: max-age=31536000; includeSubDomains` |
| Subida de 1 byte con `EET_MAX_UPLOAD_MB=0` | **413** HTML «Archivo demasiado grande» / «límite de 0 MB» |
| 11 fallos de login desde 127.0.0.1 (`EET_LOGIN_IP_MAX_15MIN=10`) | intentos 1–10 genéricos; **11 y 12 bloqueados**; auditoría `login_ip_locked` ×3 |
| Bandera apagada | `/`, `/entregas`, `/salud` → 200 con CSP |
| `npm run css:build` | sin cambios: `app.css` = 56 528 bytes (Tailwind 4.3.3) |
| `docker compose build app` + `up -d` | contenedor arriba; `/` y `/entregas` → 200 con CSP |

Los datos de verificación (usuario `verif-a6a`, intentos de 127.0.0.1 y su
auditoría) se borraron al terminar.

---

## 10. Decisiones

1. **Nonce por petición, no por sesión.** Un nonce reutilizable deja de proteger
   si un XSS alcanza a leer una respuesta anterior. El coste es que el HTML deja
   de ser byte-idéntico entre peticiones (ver §8).
2. **`'unsafe-inline'` solo en `style-src`.** Razón acotada y documentada; el
   vector real (scripts) queda cerrado con nonce.
3. **HSTS condicionado al esquema**, no a una bandera. Emitirlo por HTTP no tiene
   efecto y en `dev` dejaría el navegador clavado en `https://localhost`.
4. **Rate limit en las tablas, sin Redis.** La escala del sistema (decenas de
   usuarios, un contenedor) no justifica una dependencia nueva; `login_attempts`
   ya existe, tiene índice por `(ip, created_at)` y la auditoría lo respalda.
5. **El bloqueo por IP del login no registra el intento.** Registrar cada
   petición bloqueada convertiría el propio bloqueo en un amplificador de
   escritura.
6. **Duplicación mínima del guardia de tamaño** en los tres routers antes que
   crear un módulo nuevo fuera del alcance de la fase.
7. **`utcnow()` en `core/security.py`** (y no un módulo `core/time.py`) para no
   sumar superficie de importación: seguridad ya es el módulo de utilidades
   puras del núcleo.
8. **La validación de `prod` exige `__Host-` en la cookie de sesión.** Es el
   prefijo que impide que un subdominio comprometido sobrescriba la cookie; en
   `dev`/`staging` por HTTP no puede usarse porque exige `Secure`.

---

## 11. Desviaciones

| Punto | Qué se hizo | Motivo |
|---|---|---|
| `app/auth/mailer.py` con 3 `datetime.utcnow()` | **sin tocar** | El archivo está en la lista de «no tocar» de la fase; quedan ~20 de los 92 warnings |
| `tests/test_auth_recovery.py` | se agregó `_sin_nonce()` y se ajustaron 2 aserciones | El nonce por petición hace variar el HTML; el archivo no estaba en la lista permitida, pero sin el ajuste la suite no puede quedar en verde (ver §8) |
| `app/templates/error.html` | se agregó además un texto para 413 | El brief pedía solo el título; sin texto, la página mostraba el mensaje genérico |
| Recuperación por IP | se agregó el contador de sondeos fallidos | «Asegúrate de contar por IP correctamente»: el contador anterior solo veía enlaces emitidos |
| `entregas.html` (JS) | se mantiene el tope de 10 MB del cliente | Es validación de UX previa al envío; el tope del servidor (`max_upload_mb`, 20 MB) es el que manda y no se tocó la lógica de negocio |

---

## 12. Lo que queda (fuera de A6a)

- **A6b**: UI de administración de usuarios (alta, cambio de rol, baja lógica,
  reset asistido) y vista de auditoría con filtros y exportación.
- **A6c**: E2E con Playwright (login, 2FA, recuperación, bloqueo, roles) y axe
  sobre las pantallas nuevas.
- A6.12: ensayo de despliegue completo con `auth_enabled=true` en `staging`
  usando la guía `docs/despliegue-autenticacion.md`.
- Pendiente de TI: host/usuario del relay SMTP y registros SPF/DKIM/DMARC del
  dominio (sin ellos la recuperación falla en la práctica).
