# Fase A4 — Correo, código de segundo factor y recuperación

> Entregada. Complementa `docs/fase-a3-totp.md` y las Fases A1/A2.
> No incluye LDAP (A5) ni el endurecimiento final (A6).

---

## 1. Alcance entregado

| Área | Implementación |
|---|---|
| Mailer | `app/auth/mailer.py`: backends `console`, `file`, `smtp` + `email_outbox` |
| Código por correo | 6 dígitos, HMAC con pepper, 10 min, 5 intentos, un solo uso |
| Reenvío | `POST /auth/mfa/enviar` con cooldown de 60 s y 5 códigos por hora |
| Recuperación | `GET/POST /auth/recuperar` y `GET/POST /auth/restablecer` |
| Notificaciones | Confirmación de contraseña restablecida/cambiada y de TOTP activado |
| Pruebas | `tests/test_auth_email.py` (12) y `tests/test_auth_recovery.py` (9) |

Archivos tocados: `app/auth/mailer.py` (nuevo), `app/auth/router.py`,
`app/auth/service.py`, `app/auth/middleware.py` (solo la ruta pública del
reenvío), `app/core/config.py` (`email_dir`/`email_path`), `app/web.py` (flash),
plantillas `auth/{mfa,login,seguridad,recuperar,restablecer}.html`,
`tests/conftest.py` y tres archivos de pruebas de auth existentes (ver §9).

---

## 2. Mailer y `email_outbox`

### Backends

| Backend | Uso | Comportamiento |
|---|---|---|
| `console` | desarrollo | Log con **destinatario y asunto**; el cuerpo nunca se registra |
| `file` | pruebas / inspección | Escribe `YYYYMMDD-HHMMSS-<random>.eml` en `settings.email_dir` |
| `smtp` | producción | `smtplib`, timeout 10 s, STARTTLS si `smtp_starttls`, login si `smtp_user` |

`settings.email_dir` por defecto `uploads/mail` (ignorada por Git) y la
propiedad `settings.email_path` crea la carpeta al usarla.

### Contrato

```python
enviar(destinatario, asunto, texto, html=None, sensible=False) -> bool
reintentar_pendientes(db, limit=20) -> {"revisados", "enviados", "fallidos"}
```

- Sanea destinatario y asunto: cualquier `\r`, `\n` o `\x00` descarta el
  correo (`False`) **antes** de registrar nada (anti header injection).
- Cada intento deja una fila en `email_outbox` con `destinatario`, `asunto`,
  `cuerpo`, `intentos`, `enviado_en` y `ultimo_error`.
- `enviar` **no lanza**: un fallo de SMTP/DNS/TLS se registra y devuelve
  `False`, para que el login muestre un error controlado.
- `intentos` cuenta despachos: el primer envío exitoso queda en `1`; un fallo
  más un reintento exitoso queda en `2`.

### Cifrado del cuerpo

`cuerpo` guarda un sobre JSON (`{"texto": ..., "html": ...}`) para poder
reintentar el correo completo desde la cola. Cuando `sensible=True` (códigos y
enlaces de un solo uso) el sobre se cifra con `SecretBox`:

```
enc:<key_id>:<token Fernet en ascii>
```

- Nunca se guarda el código ni el token en claro.
- El `key_id` permite descifrar tras una rotación de `EET_FERNET_KEYS`.
- Si no hay claves configuradas (autenticación apagada), se conserva la fila
  con `enc:` sin cuerpo recuperable y se avisa en el log sin contenido.

`reintentar_pendientes` no corre solo al arrancar: queda expuesto para un cron
(`python -c "from app.auth.mailer import reintentar_pendientes, ..."`). **No**
se agregó un subcomando a `app/cli.py` porque ese archivo quedó fuera del
alcance permitido de la fase; la función es pública y documentada.

---

## 3. Código por correo

### Decisión del factor en el login

| TOTP confirmado | Política | Correo | Factor |
|---|---|---|---|
| sí | `totp` / `any` | — | TOTP (comportamiento A3) |
| sí | `email` | sí | correo |
| no | `email` / `any` | sí | correo |
| no | `totp` | — | enrolamiento forzado (A3) |
| — | `disabled` | — | sin segundo factor |

Con `any` y TOTP, el usuario puede pedir explícitamente el correo desde la
pantalla de verificación (`POST /auth/mfa/enviar`). La query `?metodo=email`
**no** habilita nada por sí sola: el método se resuelve en el servidor y, para
validar, debe existir un código emitido, vigente y con intentos disponibles.

### Emisión

1. Código `f"{secrets.randbelow(1_000_000):06d}"`.
2. Se marcan como consumidos los códigos previos no usados del usuario.
3. Se inserta `mfa_email_codes(purpose='mfa', code_hash=HMAC, expires_at=+10min,
   attempts=0, ip)` y se confirma **antes** de enviar.
4. Se audita `email_otp_sent`; si el envío falla, `email_otp_send_failed`.
5. El correo dice: código, vencimiento, «no comparta», y enlace a recuperación
   por si no fue el usuario. Sin imágenes ni píxeles de seguimiento.

### Validación

- `POST /auth/mfa` con `metodo=email` toma el código no consumido más reciente.
- Rechaza expirados (y los consume), compara en tiempo constante, incrementa
  `attempts` y audita `email_otp_fail`.
- En éxito: `consumed_at`, challenge `used_at`, `mfa_enrolled_at` si era nulo,
  sesión nueva con `mfa_at`, cookie de challenge borrada y `email_otp_ok`.
- 5 fallos invalidan el código; 5 fallos del challenge devuelven al login.

### Reenvío

`POST /auth/mfa/enviar` (CSRF, ruta pública) exige challenge vigente:

- cooldown de 60 s desde el último `created_at`;
- máximo 5 códigos por hora por usuario;
- al exceder responde la pantalla de MFA con el motivo (sin emitir ni enviar).

La pantalla muestra el correo enmascarado (`j***@dominio`), `aria-live` para el
aviso de envío y una cuenta atrás visual simple; sin JS el texto sigue
explicando el límite y el servidor manda igual. El código nunca aparece en el
HTML ni en logs.

---

## 4. Recuperación de contraseña

### Solicitud (`GET/POST /auth/recuperar`)

- Respuesta **siempre** la misma: estado 200, mismo HTML y mismo texto
  «Si la cuenta existe, enviamos instrucciones al correo registrado.», exista o
  no la cuenta, sea local o LDAP, esté activa o no, y también al alcanzar el
  límite.
- Solo se emite enlace para cuentas locales, activas, con hash y correo.
- Token de 32 bytes: en `auth_tokens` solo queda `sha256` (purpose `reset`),
  `expires_at=+30min`, `ip`. Un enlace nuevo invalida el anterior.
- Límites por hora: 3 por cuenta y 10 por IP (se cuentan filas `reset`).
- Auditoría `password_reset_requested` con `{enviado, motivo}`; **sin** token,
  enlace ni identificador crudo.

### Restablecimiento

- `GET /auth/restablecer?token=` valida hash, purpose, `used_at` y expiración.
  Inválido → mismo mensaje + CTA para solicitar otro.
- `POST` valida otra vez el token y la política (`validar_password` + igualdad).
  Con errores **no** consume el token.
- En éxito: hash nuevo, `password_changed_at`, `must_change_password=False`,
  token `used_at`, demás enlaces y challenges invalidados, **todas las sesiones
  revocadas**, auditoría `password_reset_ok` y correo de confirmación sin token.
- Redirige a `303 /auth/login?ok=password_reset`.

---

## 5. Notificaciones (A4.7)

- Confirmación al restablecer: «Contraseña actualizada», fecha, IP y aviso de
  sesiones cerradas; sin token ni enlace de acción.
- Aviso al cambiar la contraseña desde `/auth/cambiar-password`.
- Aviso al activar el segundo factor TOTP (incluye recordatorio de los códigos
  de respaldo).

Todas son best-effort: un fallo de correo no revierte el cambio ni la sesión.

---

## 6. Pantallas

| Ruta | Plantilla | Notas |
|---|---|---|
| `/auth/recuperar` | `auth/recuperar.html` | campo `autocomplete="email"`, mensaje neutro, `aria-live` |
| `/auth/restablecer` | `auth/restablecer.html` | requisitos visibles, `autocomplete="new-password"`, token inválido con CTA |
| `/auth/mfa?metodo=email` | `auth/mfa.html` | código `one-time-code`, reenvío con cuenta atrás, cambio a autenticador |
| `/auth/login` | `auth/login.html` | enlace «¿Olvidaste tu contraseña?» |
| `/auth/seguridad` | `auth/seguridad.html` | tarjeta «Código por correo» con estado y correo enmascarado |

Todas mantienen `csrf_input()`, etiquetas, ayuda y texto además del color.

---

## 7. Pruebas

- `tests/test_auth_email.py` (12, integración, backend `file` + `tmp_path`):
  flujo completo, 5 fallos, expiración, método sin código emitido, cooldown y
  límite horario, `any` con/sin TOTP, política `totp`, cifrado del outbox,
  ausencia del código en logs, inyección de cabeceras y reintento con descifrado.
- `tests/test_auth_recovery.py` (9, integración): respuesta idéntica para
  local/inexistente/LDAP/inactivo, token de un solo uso, expiración a 30 min,
  revocación de sesiones, confirmación sin token, contraseña corta sin consumo,
  confirmación distinta, límite 3/hora, token inválido y CSRF.
- `tests/test_auth_templates.py`: mínimo de formularios actualizado a 30 y las
  pantallas nuevas incluidas en el barrido de CSRF.

Totales: **197 unitarias + 198 de integración = 395** en verde.

---

## 8. Verificación manual (uvicorn :8027)

Con `EET_AUTH_ENABLED=true`, `EET_COOKIE_SECURE=false`, `EET_EMAIL_BACKEND=file`
y `EET_EMAIL_DIR=<tmp>`:

1. login de `a4-manual` (`mfa_policy=email`) → `303 /auth/mfa?metodo=email`;
2. lectura del `.eml`, extracción del código y `POST /auth/mfa` → `303 /`;
3. sesión válida en `/`;
4. `POST /auth/recuperar` → lectura del enlace del `.eml`, restablecimiento →
   `303 /auth/login?ok=password_reset`;
5. la cookie de sesión anterior → `303 /auth/login` (revocada);
6. login con la contraseña nueva → challenge por correo de nuevo.

Datos de prueba eliminados al final (usuario, sesiones, tokens, códigos, cola y
auditoría). Con la bandera apagada, `/` responde `200` sin sesión. Imagen Docker
reconstruida y `/` → `200`.

---

## 9. Decisiones y desviaciones

1. **Cambio de comportamiento de `any`:** antes, sin TOTP no se pedía segundo
   factor. Ahora usa correo. Se ajustaron las fixtures genéricas
   (`tests/conftest.py`, `tests/test_auth_roles.py`) a `mfa_policy="disabled"` y
   dos casos de `tests/test_auth_mfa.py` que asumían el comportamiento viejo.
   Son cambios mínimos para que las pruebas midan lo que dicen medir.
2. **Sin subcomando CLI:** `app/cli.py` no estaba en los archivos permitidos. Se
   expone `reintentar_pendientes` para cron o script; si se quiere
   `python -m app.cli reenviar-correo`, es un cambio de tres líneas sobre el CLI.
3. **Sobre JSON en `cuerpo`:** el esquema solo tiene una columna de texto; para
   reintentar texto **y** HTML se guarda un sobre JSON (cifrado si es sensible).
   Las filas antiguas o de texto plano siguen siendo legibles.
4. **`intentos` cuenta el primer despacho:** así la evidencia coincide entre el
   envío inicial y los reintentos.
5. **Correo en modo `any`:** se ofrece cambiar a correo desde la pantalla de
   verificación; el cambio es un `POST` (no un enlace GET) para que emita el
   código con CSRF y quede auditado.
6. **Un solo enlace de reset válido:** solicitar otro invalida el anterior,
   además de los límites por hora. Reduce la ventana de un enlace filtrado.
7. **Notificaciones best-effort:** los avisos no bloquean cambios ya
   confirmados; el flujo principal no depende del SMTP.
8. **`email_from` defectuoso:** si la configuración trae saltos de línea, se usa
   `no-reply@localhost` en lugar de abortar el envío.

---

## 10. Pendiente para A5/A6

- A5: `LdapProvider` real; hoy la recuperación ya trata a LDAP como no elegible
  sin delatarlo.
- A6: CSP con nonce (la cuenta atrás de la pantalla de MFA es un script inline),
  rate limiting por IP en el challenge, E2E Playwright y axe sobre las pantallas
  nuevas.
