# Fase A6b — Administración de usuarios, auditoría y red interna (informe)

> Alcance: **solo A6b** del plan (`docs/plan-implementacion-autenticacion.md`
> A2.7, A2.8, A6.7 y A6.8). **No** incluye los E2E con navegador ni axe (A6c)
> ni el ensayo completo de despliegue (A6.12).
> Guía operativa: `docs/runbook-autenticacion.md`.

Estado previo: Fases A0–A6a completas; `208` unitarias + `246` integración en
verde; Alembic `0003 (head)`; `EET_AUTH_ENABLED=false` por defecto.

---

## 1. Entregado

| Tarea | Dónde |
|---|---|
| Listado de usuarios con filtros, contadores y paginación de 25 | `auth/router.py`, `auth/service.py`, `templates/auth/usuarios.html` |
| Alta local con validaciones, contraseña manual o generada | `crear_usuario_admin` + `usuarios_crear` |
| Cambio de rol con motivo y letra de terna | `cambiar_rol_usuario` + `usuarios_rol` |
| Activación / baja lógica con revocación de acceso | `cambiar_estado_usuario` + `usuarios_estado` |
| Reset de contraseña temporal mostrada una sola vez | `reset_password_usuario` + `usuarios_reset_password` |
| Reset del segundo factor (TOTP + códigos) | `reset_mfa_usuario` + `usuarios_reset_mfa` |
| Vista de auditoría con filtros y detalle JSON | `listar_auditoria` + `templates/auth/auditoria.html` |
| Exportación CSV con BOM y tope de 10 000 filas | `exportar_auditoria` + `auditoria_csv` |
| Restricción por red interna | `auth/dependencies.py:requiere_red_interna` |
| Navegación en el menú de la cuenta | `templates/components/shell.html` |
| Mensajes de confirmación | `app/web.py` (`FLASH_OK`) |
| Pruebas de integración | `tests/test_auth_admin.py` (18 casos) |

Rutas nuevas (todas con red interna + permiso + CSRF cuando aplica):

| Método | Ruta | Permiso | Respuesta |
|---|---|---|---|
| GET | `/auth/usuarios` | `usuarios.gestionar` | HTML 200 |
| POST | `/auth/usuarios` | `usuarios.gestionar` | 200 con errores o con la clave temporal; 303 nunca |
| POST | `/auth/usuarios/{id}/rol` | `usuarios.gestionar` | 303 `?ok=usuario_actualizado` o 200 con errores |
| POST | `/auth/usuarios/{id}/estado` | `usuarios.gestionar` | 303 `?ok=usuario_actualizado` o 200 con errores |
| POST | `/auth/usuarios/{id}/reset-password` | `usuarios.gestionar` | 200 con la clave temporal (una sola vez) |
| POST | `/auth/usuarios/{id}/reset-mfa` | `usuarios.gestionar` | 303 `?ok=mfa_reseteado` o 200 con errores |
| GET | `/auth/auditoria` | `auditoria.ver` | HTML 200 |
| GET | `/auth/auditoria.csv` | `auditoria.ver` | `text/csv; charset=utf-8` con BOM |

Todos los errores de negocio se devuelven como HTML dentro de la misma
pantalla (nunca JSON ni 500). Los POST sin CSRF válido responden 403 antes de
tocar la lógica.

---

## 2. Reglas de protección

### 2.1 Último administrador activo

`_admins_activos(db, excepto_id=objetivo)` cuenta administradores con
`is_active=true`. La degradación de rol y la baja lógica se rechazan si el
objetivo es un administrador activo y no queda ningún otro.

Es **defensa en profundidad**: por la interfaz no se alcanza, porque antes se
rechazan las acciones sobre la propia cuenta (un actor con sesión siempre es un
administrador activo, así que al menos él seguiría siéndolo). Protege a los
scripts, a la CLI y a futuros endpoints que no pasen por esa regla. Se prueba a
nivel de servicio en `test_ultimo_admin_activo_no_se_degrada_ni_desactiva`.

### 2.2 Acciones sobre la propia cuenta

| Acción | Regla | Motivo |
|---|---|---|
| Cambiar el propio rol | Rechazada | Evita autoelevación o autodegradación sin cuatro ojos |
| Desactivar la propia cuenta | Rechazada | Evita dejar la sesión y la cuenta en un estado incoherente |
| Reset de la propia contraseña | Rechazada (usar `Cambiar contraseña`) | El flujo propio exige la clave actual y ya revoca las demás sesiones |
| Reset del propio 2FA | Rechazado | Lo ejecuta otro administrador; así se cubre la pérdida del teléfono |

### 2.3 Revocación al cambiar el estado

`_invalidar_acceso` hace, en una sola transacción:

1. `update sesiones set revoked_at = now()` para todas las sesiones vivas;
2. `update auth_tokens set used_at = now()` para tokens de recuperación y
   challenges de 2FA pendientes;
3. `update mfa_email_codes set consumed_at = now()` para códigos de correo no
   consumidos.

Se aplica al desactivar, al resetear la contraseña y al resetear el 2FA.
Reactivar limpia `failed_attempts` y `locked_until` para que la cuenta pueda
entrar sin esperar la ventana de bloqueo.

### 2.4 Contraseñas temporales

- Se generan con `secrets.choice` sobre un alfabeto de 62 caracteres sin
  ambiguos (`I`, `O`, `1`, `0`), 16 posiciones.
- Se muestran **una sola vez** en el HTML de la respuesta del POST, dentro de
  `<code id="password-temporal">`, con aviso de copiarla y de no recargar.
- No viajan en la URL (no hay 303), ni en la auditoría, ni en un correo
  automático.
- Se guarda solo `hash_password(temporal)`; `must_change_password=true`.

### 2.5 Validaciones del alta

| Campo | Regla |
|---|---|
| `nombre` | Obligatorio, ≤ 200 |
| `username` | 3–100, `[A-Za-z0-9._-]`; unicidad con `func.lower` |
| `email` | Formato real (`email-validator`, sin dominios reservados); unicidad con `func.lower` |
| `rol` | Uno de `admin, supervision, cargador, asistente, consulta` |
| `letra` | Obligatoria si `rol=asistente`; A/B/C |
| `mfa_policy` | `disabled, email, totp, any`; por defecto `totp` para admin/supervisión y `any` para el resto |
| `password` | ≥ 12 (política completa de `validar_password`), confirmación coincidente |

Todos los incumplimientos se acumulan en una lista y se muestran juntos; el
formulario conserva nombre, usuario, correo, rol, letra y política (nunca la
contraseña).

---

## 3. Red interna

```python
def requiere_red_interna(request: Request) -> None:
    if not settings.auth_enabled or not settings.admin_internal_only:
        return
    host = request.client.host if request.client else None
    direccion = ipaddress.ip_address((host or "").strip())  # ValueError -> 403
    if not any(direccion in red for red in settings.internal_networks):
        raise HTTPException(403, MENSAJE_RED_INTERNA)
```

- `internal_networks` ya existía en `core/config.py` (parsea `internal_cidrs`
  con `ipaddress.ip_network`, tolerante a espacios y fallando al arrancar si un
  CIDR es inválido); **no se agregó configuración nueva**.
- Se aplica a las ocho rutas de `/auth/usuarios*` y `/auth/auditoria*`, por
  delante de `require_permiso`.
- El 403 se audita como `acceso_denegado` con `permiso=admin.red_interna` y
  `motivo=ip_no_parseable|fuera_de_rango`.
- Un host que no sea IP se rechaza incluso con `0.0.0.0/0,::/0`: el contrato es
  «una IP real dentro de una red», no «cualquier cosa que no coincida».
- Una IPv4 mapeada en IPv6 (`::ffff:10.x.x.x`, típica de un socket dual-stack)
  se normaliza a IPv4 antes de comparar.

### La advertencia del proxy

**Detrás de un reverse proxy hay que configurar `EET_TRUSTED_PROXY_IPS`**
(Fase A6a). Sin eso, `request.client.host` es la IP del proxy: si es
`127.0.0.1` y el CIDR por defecto incluye loopback, **todos** los clientes
parecen internos y la restricción deja de proteger; si el proxy está en otro
host, todos quedan fuera. El runbook (§8) lo documenta con el procedimiento de
verificación.

---

## 4. Auditoría

- Filtros `accion`, `actor`, `desde`, `hasta` y `q` (entidad o `entidad_id`);
  `desde`/`hasta` son fechas y `hasta` incluye el día completo. Un valor
  inválido se ignora en vez de romper la vista.
- 50 filas por página, orden `fecha desc, id desc`.
- El `detalle` se serializa con `json.dumps(..., indent=2, sort_keys=True)` y
  se muestra dentro de `<details>`; la tabla muestra fecha, actor, acción,
  entidad, id e IP.
- CSV: `csv.writer` con `QUOTE_MINIMAL` y CRLF, BOM UTF-8 agregado en el
  router, `Content-Disposition: attachment; filename=auditoria_eet.csv`, tope
  de 10 000 filas (`MAX_FILAS_CSV_AUDITORIA`).
- **Inyección de fórmulas**: una celda que empiece con `=`, `+`, `-`, `@`,
  tabulador o retorno de carro se prefija con `'` para que Excel no la evalúe.
- Solo lectura: no hay POST/PUT/DELETE; la prueba verifica que la página no
  publica ningún formulario POST hacia la auditoría.

Eventos nuevos: `user_created`, `role_changed` (con `de`/`a`/motivo),
`user_disabled`, `user_enabled`, `password_reset_admin` y `mfa_reset_admin`.
Ninguno guarda contraseñas, códigos, tokens ni secretos; además
`audit._detalle_seguro` descarta claves sospechosas.

---

## 5. Pruebas

`tests/test_auth_admin.py` — **18 casos de integración**:

| Bloque | Casos |
|---|---|
| Permisos | admin 200; supervisión, carga y consulta 403 |
| Alta | válida; duplicado por usuario (mayúsculas) y por correo; asistente sin letra; clave corta; clave generada mostrada una sola vez |
| Rol | con motivo; sin motivo; sobre la propia cuenta; asistente exige letra |
| Último admin | degradación permitida con otro admin; servicio rechaza degradar y desactivar al último |
| Autoprotección | no puede desactivarse; no puede resetear su clave; no puede resetear su 2FA |
| Baja | revoca todas las sesiones del objetivo y audita con motivo |
| Reset de clave | temporal una sola vez, `must_change_password`, revoca sesiones, auditoría sin la clave |
| Reset de 2FA | borra `mfa_totp`, `recovery_codes` y `mfa_enrolled_at` |
| Auditoría | 200 para admin y supervisión; 403 para carga; CSV con `text/csv`, BOM y cabecera exacta; sin formularios de escritura |
| Filtros | `accion` + `q` devuelven el evento esperado |
| Red interna | 403 con `10.0.0.0/8`; 200 al apagarla; 403 con `0.0.0.0/0` y host no-IP; POST restringido |
| CSRF | alta y cambio de rol sin token → 403 |
| Bandera apagada | `/auth/usuarios` y `/auth/auditoria` renderizan sin sesión |

La fixture limpia usuarios, sesiones, tokens, `mfa_totp`, códigos de respaldo,
códigos de correo, auditoría e intentos, incluidos los usuarios creados desde
la interfaz.

### Totales

| Suite | Antes | Después |
|---|---|---|
| `uv run pytest -q` | 208 | **212** (los 4 nuevos son los códigos flash parametrizados) |
| `uv run pytest -m integration -q` | 246 | **264** (+18) |
| Warnings | 3 / 92 | **3 / 92** |

---

## 6. Verificación manual

| Comprobación | Resultado |
|---|---|
| `uv run python -c "import app.main"` | `ok` |
| `uv run pytest -q` | 212 pasan · 3 warnings |
| `uv run pytest -m integration -q` | 264 pasan · 92 warnings |
| uvicorn :8030 con `EET_AUTH_ENABLED=true EET_COOKIE_SECURE=false` | login 303; `/auth/usuarios` 200; menú con Usuarios y Auditoría |
| Alta desde la UI con `generar_password` | 200; contraseña de 16 caracteres mostrada una sola vez |
| Cambio de rol / baja / reset de clave / reset de 2FA | 303 / 303 / 200 / 303; auditoría con los cinco eventos |
| Auditoría filtrada por `q` | 200 con `user_created`, `role_changed`, `user_disabled`, `password_reset_admin`, `mfa_reset_admin` |
| `GET /auth/auditoria.csv?q=<id>` | 200, `text/csv; charset=utf-8`, `attachment; filename=auditoria_eet.csv`, BOM, 5 filas |
| Reinicio con `EET_ADMIN_INTERNAL_ONLY=true EET_INTERNAL_CIDRS=10.0.0.0/8` | `/auth/usuarios`, `/auth/auditoria` y `/auth/auditoria.csv` → **403** con el mensaje de red interna |
| Bandera apagada | `/`, `/salud`, `/auth/usuarios`, `/auth/auditoria` → 200 |
| `npm run css:build` | 56 609 bytes (local); dentro de la imagen 55 394 bytes (el build de Docker escanea solo `app/`) |
| `docker compose build app` + `up -d` | contenedor arriba; migraciones aplicadas; `/` y `/salud` → 200 |

Los datos de verificación (`verif-a6b`, `verif-a6b-user`, sus sesiones, tokens y
auditoría, incluido el `user_created` con actor `cli`) se borraron al terminar.

---

## 7. Decisiones

1. **La contraseña temporal se devuelve en el POST, sin PRG.** Un 303 obligaría
   a guardarla en sesión o en la URL; ambas opciones son peores. El precio es
   que recargar repite el POST y emite otra clave: la propia vista lo advierte.
2. **Reset de 2FA propio prohibido, no «con confirmación extra».** Un
   administrador que se equivoca dejaría su cuenta sin el segundo factor que su
   rol exige. Otro administrador lo resuelve; por eso el runbook insiste en dos
   admins.
3. **El último admin se protege también a nivel de servicio.** Aunque la UI no
   pueda alcanzar el caso, la regla cubre scripts y futuros consumidores sin
   duplicar lógica.
4. **Reactivar limpia el bloqueo.** Si no, una cuenta dada de baja tras agotar
   los intentos volvería bloqueada 15 minutos sin explicación.
5. **Auditoría solo lectura y CSV con saneo de fórmulas.** El archivo lo abre
   una persona en Excel; un `entidad_id` malicioso no debe ejecutarse.
6. **`internal_networks` se reutiliza, no se duplica.** La propiedad ya existía
   con validación al arranque; agregar otra lista habría creado dos fuentes de
   verdad para el mismo CIDR.
7. **Correo real obligatorio en el alta.** `email_validator` rechaza dominios
   reservados (`.test`, `.invalid`); es la misma dependencia que ya estaba en
   el proyecto y evita cuentas cuyos códigos de recuperación nunca llegarían.
8. **Acciones por fila en `<details>`.** Sin JavaScript y accesible por
   teclado; mantiene la tabla legible y no añade dependencias.

---

## 8. Desviaciones y límites

| Punto | Qué se hizo | Motivo |
|---|---|---|
| Letra de terna en el cambio de rol | Se agregó un selector opcional, obligatorio al pasar a `asistente` | Sin letra, la cuenta no vería ninguna entrega; el brief solo pedía rol y motivo |
| Reset de 2FA propio | **Prohibido** (el brief permitía prohibirlo o exigir confirmación) | Más simple y más seguro; queda documentado |
| Notificación por correo del reset administrativo | **No** se envía | Fuera del alcance; la clave se entrega por canal seguro. Queda como mejora (§9) |
| Re-cifrado de secretos tras rotar `EET_FERNET_KEYS` | Manual | No se agregó un job fuera de alcance; el runbook lo describe |
| `email_validator` con dominios reservados | Rechazados | Los tests usan `@ejemplo.org`; `@ejemplo.test` es inválido para la librería |
| CSS local vs. imagen | 56 609 vs. 55 394 bytes | Tailwind auto-detecta fuentes desde el cwd local (incluye `tests/`); el Dockerfile escanea solo `app/`. Ambas versiones contienen las clases nuevas |
| Retención de auditoría | Sin job de purga | Se define con la institución (≥ 1 año); no se borra nada automáticamente |

---

## 9. Lo que queda (fuera de A6b)

- **A6c**: E2E Playwright (login, 2FA, recuperación, bloqueo, roles) y axe
  sobre `/auth/usuarios` y `/auth/auditoria` (objetivo: 0 violaciones).
- **A6.12**: ensayo de despliegue completo con `auth_enabled=true` en staging.
- Mejoras opcionales: correo de aviso al titular tras un reset administrativo;
  job de re-cifrado tras rotación de claves; job de retención de auditoría;
  exportación de usuarios (CSV) si la institución la pide.
- Pendiente de TI: relay SMTP real, SPF/DKIM/DMARC y definición de los CIDR
  internos definitivos.
