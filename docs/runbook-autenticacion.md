# Runbook de autenticación y administración — Validador EET

> Guía operativa de las Fases A1–A6b: alta del primer administrador, gestión
> de cuentas, resets asistidos, rotación de claves, caída de servicios,
> respaldo/restauración, activación de la bandera y restricción por red
> interna. Complementa `docs/despliegue-autenticacion.md` (infraestructura,
> TLS, proxy y checklist de producción) y `docs/fase-a6b-administracion.md`
> (alcance técnico de la administración de usuarios y auditoría).

Todos los comandos se ejecutan desde la raíz del repositorio. En producción,
dentro del contenedor:

```bash
docker compose exec app <comando>
```

En Windows/PowerShell con el entorno local:

```powershell
uv run <comando>
```

---

## 1. Modelo en una página

| Rol | Puede | No puede |
|---|---|---|
| `admin` | Gestionar cuentas, roles, estados, resets y ver auditoría | Dictaminar fichas QA/QC ni autorizar reemplazos |
| `supervision` | Operar el flujo completo (entregas, QA/QC, reemplazos) y ver auditoría | Gestionar cuentas |
| `cargador` | Cargar entregas, malla y mapa; leer cruces y QA/QC en modo lectura | Dictaminar, autorizar reemplazos, exportar ni ver auditoría |
| `asistente` | Ver solo sus propias entregas (por `letra_terna`: A/B/C) | Cruces, QA/QC, reemplazos, exportar, usuarios ni auditoría |
| `consulta` | Leer todo el flujo y exportar el consolidado | Modificar nada, gestionar cuentas ni ver auditoría |

Reglas permanentes:

1. **Nunca hay un solo administrador.** El sistema impide degradar o
   desactivar al último administrador activo, y prohíbe que un administrador
   se cambie el rol o se desactive a sí mismo; esas reglas son la última
   barrera, no una invitación a operar con una sola cuenta.
2. **Separación de deberes.** `admin` no dictamina fichas ni autoriza
   reemplazos: quien administra identidades no altera la clase consolidada.
3. **Toda acción sensible exige motivo** y queda en `auditoria` con actor real
   (`usuario:<id>`), IP y agente de usuario.
4. **Las contraseñas temporales se muestran una sola vez** en la respuesta del
   POST y nunca se guardan en claro, se auditan ni se ponen en una URL.
5. **La administración y la auditoría solo se abren desde la red interna**
   cuando `EET_ADMIN_INTERNAL_ONLY=true`.

---

## 2. Configuración mínima

```env
EET_AUTH_ENABLED=true
EET_SECRET_KEY=<cadena aleatoria de 32+ caracteres>
EET_AUTH_PEPPER=<cadena aleatoria de 16+ caracteres>
EET_COOKIE_SECURE=true
EET_ENVIRONMENT=prod
EET_PUBLIC_BASE_URL=https://eet.aep.gov.py
EET_EMAIL_BACKEND=smtp
EET_SMTP_HOST=mail.aep.gov.py
EET_SMTP_PORT=587
EET_SMTP_STARTTLS=true
EET_SMTP_USER=<usuario del relay>
EET_SMTP_PASSWORD=<secreto del relay>
EET_EMAIL_FROM="Validador EET <no-reply@aep.gov.py>"
EET_ADMIN_INTERNAL_ONLY=true
EET_INTERNAL_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
EET_TRUSTED_PROXY_IPS=<IP o CIDR del reverse proxy>
```

Generar secretos (Linux/macOS o Git Bash):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(24))"   # AUTH_PEPPER
```

Nunca se versionan en Git: van en el gestor de secretos del despliegue
(`docker secret`, variables del orquestador o `.env` fuera del repositorio).

> `EET_ENVIRONMENT=prod` **no arranca** si falta cualquiera de los requisitos
> de endurecimiento: `cookie_secure`, `public_base_url` con `https://`,
> `email_backend=smtp` con `smtp_host`, `expose_docs=false`, secretos largos y
> cookie `__Host-`. El mensaje enumera todos los incumplimientos juntos y
> nunca imprime el valor de un secreto.

---

## 3. Crear el primer administrador

Se hace **una sola vez**, por CLI y desde la red interna, sobre una base ya
migrada (`alembic upgrade head`, que el contenedor aplica al arrancar).

```bash
docker compose exec -e EET_ADMIN_PASSWORD='<clave inicial fuerte>' app \
  uv run python -m app.cli crear-admin \
  --email coordinacion@aep.gov.py \
  --username coordinacion \
  --nombre "Coordinación EET" \
  --must-change
```

`--must-change` obliga a cambiar la clave en el primer acceso. Si se omite
`EET_ADMIN_PASSWORD`, el comando la pide dos veces por terminal (ejecutar con
`docker compose exec -it`; nunca hay una clave por defecto ni se imprime).

Resultado esperado:

```text
Administrador creado: id=1 usuario=coordinacion
```

El alta queda auditada con actor `cli`. Con `EET_AUTH_ENABLED=true`, el primer
acceso pide TOTP (política por defecto de `admin`) y luego el cambio de
contraseña si se usó `--must-change`.

**Inmediatamente después, crear el segundo administrador** (por CLI o desde
`/auth/usuarios`). Un solo administrador es un punto único de fallo: si pierde
el teléfono y no hay otro, hay que intervenir la base.

---

## 4. Alta y mantenimiento de cuentas

### 4.1 Desde la interfaz (recomendado)

`Menú de la cuenta → Usuarios` (solo `admin`, solo red interna).

1. Abrir **Crear usuario**.
2. Completar nombre, usuario, correo institucional y rol.
3. Si el rol es **Asistente**, elegir la letra de terna (A/B/C). Es
   obligatoria: sin letra la cuenta no vería ninguna entrega.
4. Dejar **Segundo factor** en «Predeterminado según el rol» salvo excepción:
   - `admin` y `supervision` → `totp` obligatorio;
   - `cargador`, `asistente`, `consulta` → `any` (TOTP o correo).
5. Escribir una contraseña inicial (mínimo 12 caracteres y su confirmación) o
   marcar **Generar una contraseña temporal automática**.
6. Crear y **copiar la contraseña temporal en ese momento**: no se vuelve a
   mostrar. Entregarla por un canal seguro y exigir el cambio en el primer
   acceso (`must_change_password=true`).

Validaciones: usuario de 3–100 caracteres (`A-Z a-z 0-9 . _ -`), correo con
dominio real (los dominios reservados como `.test` o `.invalid` se rechazan),
unicidad de usuario y correo sin distinguir mayúsculas.

### 4.2 Desde la CLI (altas masivas o scripting)

```bash
docker compose exec -e EET_USER_PASSWORD='<clave inicial>' app \
  uv run python -m app.cli crear-usuario \
  --email operador@aep.gov.py \
  --username operador \
  --nombre "Operador EET" \
  --rol cargador \
  --mfa-policy any
```

La cuenta nace con `must_change_password=true`. La CLI permite
`--mfa-policy disabled`; la interfaz también, pero **no se recomienda** en
producción.

### 4.3 Cambio de rol

`Usuarios → Acciones → Nuevo rol` + **motivo obligatorio**. Reglas:

- no se puede cambiar el rol de la propia cuenta;
- si la cuenta es el último administrador activo, la degradación se rechaza;
- al pasar a `asistente` se exige letra de terna; al salir de `asistente` la
  letra se limpia (las entregas históricas no se tocan: guardan su propia
  `asistente_letra`);
- queda auditado como `role_changed` con `de`, `a`, letras y motivo.

### 4.4 Baja lógica

`Usuarios → Acciones → Desactivar cuenta` + motivo. Efectos inmediatos:

- la cuenta queda `is_active=false`;
- se revocan **todas** sus sesiones;
- se anulan tokens de recuperación, challenges de 2FA y códigos de correo
  pendientes;
- se audita `user_disabled` con la cantidad de sesiones revocadas.

No se puede desactivar la propia cuenta ni al último administrador activo.
Reactivar limpia el bloqueo por intentos fallidos y audita `user_enabled`.

---

## 5. Resets asistidos

### 5.1 Contraseña

`Usuarios → Acciones → Generar contraseña temporal` + motivo.

- El sistema genera 16 caracteres con `secrets` (sin caracteres ambiguos) y la
  muestra **una sola vez** en esa respuesta.
- Marca `must_change_password=true` y revoca todas las sesiones del titular.
- Audita `password_reset_admin` con el motivo (nunca la clave).
- No se permite sobre la propia cuenta: para la propia, usar
  `Cambiar contraseña` (exige la clave actual).
- **No recargar la página** después de generarla: el navegador repetiría el
  POST y emitiría otra clave, invalidando la que se acaba de copiar.
- No se envía por correo automáticamente: entregarla por un canal seguro
  (presencial, teléfono verificado o gestor de contraseñas). Si se perdió la
  pantalla antes de copiarla, generar otra.

### 5.2 Segundo factor (TOTP y códigos de respaldo)

`Usuarios → Acciones → Reiniciar segundo factor` + motivo.

- Borra la fila de `mfa_totp` y **todos** los códigos de respaldo.
- Deja `mfa_enrolled_at=NULL`: con política `totp`, el próximo acceso fuerza
  el re-enrolamiento antes de operar.
- Revoca todas las sesiones y anula tokens/códigos pendientes.
- Audita `mfa_reset_admin`.
- No se permite sobre la propia cuenta: si el administrador perdió su teléfono,
  **otro administrador** ejecuta el reset. Por eso se exige más de un admin.

Flujo para el titular después del reset:

1. Ingresar con la contraseña temporal (o la actual, si solo se reseteó 2FA).
2. Si la política exige TOTP, el sistema lo lleva a `/auth/seguridad`.
3. Escanear el QR o ingresar la clave manual, confirmar el código y guardar los
   10 códigos de respaldo.
4. Cambiar la contraseña si el reset incluyó clave.

---

## 6. Pérdida de acceso: árbol de decisión

| Situación | Acción |
|---|---|
| Usuario olvidó su contraseña, conserva su 2FA | Reset de contraseña (§5.1). Conserva TOTP. |
| Usuario perdió el teléfono, conserva códigos de respaldo | Ingresa con un código de respaldo y luego `Seguridad → activar autenticador` o pide reset de 2FA. |
| Usuario perdió teléfono **y** códigos | Otro administrador hace reset de 2FA (§5.2). |
| Administrador perdió teléfono y códigos | Otro administrador hace reset de 2FA. Si no existe, ver «break-glass». |
| Único administrador bloqueado | Intervención de base de datos (§6.1). |
| Cuenta LDAP no puede ingresar | El Directorio administra la clave; revisar `ldap-check` y el estado de la cuenta en AD. No hay reset local para cuentas LDAP. |

### 6.1 Break-glass del único administrador

Requiere acceso al servidor y a la base. **Registrar la intervención** en el
libro de incidentes y restaurar el estado cuanto antes.

```bash
# 1) Ver el estado de las cuentas administradoras
docker compose exec db psql -U eet -d eet -c \
  "select id, username, email, rol, is_active, mfa_policy, must_change_password
     from usuarios where rol = 'admin' order by id;"

# 2) Reactivar la cuenta y liberar bloqueo (sin tocar la contraseña)
docker compose exec db psql -U eet -d eet -c \
  "update usuarios set is_active = true, failed_attempts = 0, locked_until = null
    where username = 'coordinacion';"

# 3) Si además perdió el 2FA, borrar TOTP y códigos; el próximo acceso
#    re-enrola si la política es totp.
docker compose exec db psql -U eet -d eet -c \
  "delete from mfa_totp where usuario_id = (select id from usuarios where username = 'coordinacion');
   delete from recovery_codes where usuario_id = (select id from usuarios where username = 'coordinacion');
   update usuarios set mfa_enrolled_at = null
    where username = 'coordinacion';"

# 4) Revocar sesiones vivas de esa cuenta
docker compose exec db psql -U eet -d eet -c \
  "update sesiones set revoked_at = now()
    where usuario_id = (select id from usuarios where username = 'coordinacion')
      and revoked_at is null;"
```

Si tampoco se conoce la contraseña, generar una nueva por CLI con otro
`--username` no sirve para recuperar la existente: crear un **nuevo**
administrador temporal y dar de baja el anterior desde la interfaz.

```bash
docker compose exec -e EET_ADMIN_PASSWORD='<clave temporal>' app \
  uv run python -m app.cli crear-admin \
  --email admin.temporal@aep.gov.py \
  --username admin-temp \
  --nombre "Administrador temporal" \
  --must-change
```

Después del incidente: revisar `auditoria` de las últimas 24 h, rotar secretos
si hubo acceso físico o a la base, y documentar el motivo.

---

## 7. Auditoría

`Menú de la cuenta → Auditoría` (roles `admin` y `supervision`, solo red
interna).

- Filtros: acción, actor (`usuario:<id>`, `cli`, `sistema`), rango de fechas y
  entidad/identificador.
- 50 eventos por página; el detalle JSON se abre en cada fila.
- **Descargar CSV**: mismo filtro, hasta 10 000 filas, `text/csv; charset=utf-8`
  con BOM (Excel abre bien los acentos).
- La vista es **solo lectura**: no hay borrado ni edición, tampoco por API.

Acciones que conviene vigilar a diario/semanalmente:

| Acción | Qué indica |
|---|---|
| `login_fail`, `login_locked`, `login_ip_locked` | Credenciales erróneas, spraying o cuenta bloqueada |
| `mfa_fail`, `mfa_ip_locked` | Ataque al segundo factor |
| `acceso_denegado` | Intento de usar una ruta fuera del rol (o fuera de la red interna) |
| `role_changed`, `user_created`, `user_disabled`, `user_enabled` | Cambios de privilegios |
| `password_reset_admin`, `mfa_reset_admin` | Resets asistidos: deben tener motivo y correlato con un pedido real |
| `password_changed`, `password_reset_ok` | Cambios por el propio titular |
| `ldap_error`, `ldap_disabled` | Directorio caído o intento con LDAP apagado |

Retención recomendada: **mínimo 1 año** (definir con la institución). El
crecimiento es bajo (unas pocas filas por usuario y día). No se guardan
contraseñas, códigos, tokens, cookies ni secretos TOTP; `audit._detalle_seguro`
descarta además cualquier clave sospechosa como defensa en profundidad.

---

## 8. Restricción por red interna

Variables:

```env
EET_ADMIN_INTERNAL_ONLY=true
EET_INTERNAL_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
```

Con la bandera activa, `/auth/usuarios*` y `/auth/auditoria*` responden **403**
(«Esta sección solo está disponible desde la red interna.») si
`request.client.host` no es una IP o no pertenece a ninguno de los CIDR. El
403 queda auditado como `acceso_denegado` con `permiso=admin.red_interna`.

### 8.1 La regla de oro del proxy

`requiere_red_interna` mira la IP de la conexión, no una cabecera arbitraria.
Detrás de Nginx/Caddy/IIS **hay que declarar el proxy**:

```env
EET_TRUSTED_PROXY_IPS=127.0.0.1/32        # proxy en el mismo host
```

y arrancar uvicorn con `--proxy-headers --forwarded-allow-ips=<IP del proxy>`
(o confiar en la variable, que `create_app` monta como middleware). Sin esta
configuración:

- si el proxy está en otro host, **todos** los clientes aparecen con la IP del
  proxy: la restricción queda inútil o bloquea a todos, según el CIDR;
- si el proxy está en `127.0.0.1` y el CIDR por defecto incluye
  `127.0.0.1/32`, **todo el mundo** parece local. En producción, o se declara
  el proxy, o se quita `127.0.0.1/32` de `EET_INTERNAL_CIDRS`.

Verificación:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://eet.aep.gov.py/auth/usuarios
# 303 al login (sin sesión) desde internet; con sesión de admin: 403
```

La restricción **no sustituye** la autenticación ni el permiso: se suman. Un
administrador autenticado desde fuera de la red interna recibe 403.

---

## 9. Rotación de claves

### 9.1 Claves Fernet (`EET_FERNET_KEYS`)

Cifran los secretos TOTP y el cuerpo sensible de `email_outbox`. Formato:

```env
EET_FERNET_KEYS="v2:<clave_nueva>,v1:<clave_vieja>"
```

La primera cifra; todas descifran. Procedimiento:

1. Generar la clave nueva:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Ponerla **primera** y conservar la anterior:
   `EET_FERNET_KEYS="v2:<nueva>,v1:<vieja>"`.
3. Reiniciar la aplicación. Verificar login con TOTP de una cuenta de prueba:
   los secretos v1 se siguen leyendo.
4. Re-cifrar los datos viejos (tarea pendiente de automatizar; hoy se puede
   hacer con un script que lea `mfa_totp` y `email_outbox`, descifre con
   `SecretBox` y vuelva a cifrar; hacerlo con backup previo y ventana de
   mantenimiento).
5. Cuando ya no queden blobs `v1`, retirar la clave vieja.

Si se pierde la clave vigente, los secretos TOTP no se pueden descifrar: el
sistema lo trata como «sin segundo factor» y el usuario conserva códigos de
respaldo o pide reset asistido. **No se pierde la contraseña**, pero sí el
enrolamiento.

### 9.2 `EET_SECRET_KEY` y `EET_AUTH_PEPPER`

- `EET_SECRET_KEY`: deriva la clave Fernet cuando no hay `EET_FERNET_KEYS` y
  firma el token CSRF derivado de la sesión. Rotarla invalida los CSRF en vuelo
  (los formularios abiertos fallan con 403 y se recargan) y, si se usa como
  clave de cifrado, obliga a rotar con el formato multi-clave.
- `EET_AUTH_PEPPER`: HMAC de códigos de correo y de respaldo. **Rotarlo
  invalida todos los códigos vigentes** (los usuarios piden uno nuevo) y los
  códigos de respaldo existentes dejan de servir. Planificar con aviso y
  verificar que todos tengan TOTP activo antes.

Procedimiento seguro para el pepper:

1. Backup de la base.
2. Aviso a los usuarios (los códigos de respaldo se regeneran después).
3. Cambiar el valor y reiniciar.
4. Cada usuario con TOTP activo regenera códigos desde `Seguridad`; quien no
   pueda, pide reset asistido.

---

## 10. Caída de servicios

### 10.1 SMTP caído

Síntomas: el login por correo muestra «no se pudo enviar», la recuperación no
llega y `email_outbox` acumula filas con `intentos > 0` y `ultimo_error`.

1. Confirmar el estado del relay (`telnet mail.aep.gov.py 587`, revisar
   credenciales y TLS).
2. Mientras dure: indicar a los usuarios que usen **TOTP o un código de
   respaldo**. El login con contraseña y TOTP no depende del correo.
3. Los envíos fallidos quedan en `email_outbox`; al restablecer el servicio se
   reintentan con `mailer.reintentar_pendientes` (job o comando manual):
   ```bash
   docker compose exec app uv run python -c \
     "from app.auth.mailer import reintentar_pendientes; print(reintentar_pendientes(50))"
   ```
4. Si el relay no vuelve, cambiar `EET_EMAIL_BACKEND=console` solo en
   desarrollo; en producción **no** se debe operar sin correo (la recuperación
   y el segundo factor por correo quedan inutilizables).
5. Revisar `auditoria`: `email_otp_send_failed` y `password_reset_requested`
   con `enviado=false`.

### 10.2 DC / LDAP caído

Hoy `EET_LDAP_ENABLED=false`: no hay dependencia del directorio y las cuentas
son locales. Cuando se habilite:

- Un fallo de LDAP es **fail closed**: no cae a autenticación local. Las
  cuentas LDAP no entran hasta que el DC responda.
- Las cuentas locales, en particular el **administrador de break-glass**,
  siguen funcionando. Debe existir y estar documentado.
- Diagnóstico:
  ```bash
  docker compose exec app uv run python -m app.cli ldap-check --login <usuario>
  ```
  El comando muestra conexión, bind, base, búsqueda, grupos y rol resultante,
  y **nunca** imprime la contraseña de servicio.
- Revisar `auditoria`: `ldap_error`, `ldap_sync`, `ldap_disabled`.
- Si el DC cambió de certificado o de host, actualizar `EET_LDAP_*` y
  reiniciar. No se guardan hashes de AD en la base.

### 10.3 Base de datos caída

La aplicación no arranca sin PostgreSQL. El contenedor reintenta con
`depends_on: condition: service_healthy`; fuera de Docker, el orquestador debe
reintentar. Al volver, las sesiones siguen en la tabla (no hay estado en
memoria) y los usuarios continúan donde estaban dentro de la ventana de
inactividad.

---

## 11. Respaldo y restauración

### 11.1 Qué respaldar

| Dato | Dónde | Frecuencia |
|---|---|---|
| `usuarios` (incluye hashes Argon2id, roles, políticas) | PostgreSQL | Diario |
| `sesiones`, `auth_tokens`, `mfa_totp`, `recovery_codes`, `mfa_email_codes`, `login_attempts` | PostgreSQL | Diario (se pueden perder sin drama; las sesiones se rehacen) |
| `auditoria` | PostgreSQL | Diario; retención ≥ 1 año |
| `email_outbox` | PostgreSQL | Diario (contiene cuerpos cifrados) |
| `EET_SECRET_KEY`, `EET_AUTH_PEPPER`, `EET_FERNET_KEYS`, credenciales SMTP/LDAP | Gestor de secretos | En cada cambio, con historial |
| Claves de cifrado | Fuera de la base, en el gestor de secretos | En cada rotación |

**Las claves no van en el mismo backup que la base**: un backup filtrado con
las claves descifra los secretos TOTP y los cuerpos de correo.

### 11.2 Respaldo

```bash
# Esquema completo (recomendado)
docker compose exec -T db pg_dump -U eet -d eet -Fc > eet-$(date +%F).dump

# Solo autenticación (restauración selectiva)
docker compose exec -T db pg_dump -U eet -d eet -Fc \
  -t usuarios -t sesiones -t auth_tokens -t mfa_totp -t recovery_codes \
  -t mfa_email_codes -t login_attempts -t auditoria -t email_outbox \
  > eet-auth-$(date +%F).dump
```

Guardar además una copia del `.env`/secretos **cifrada** y del certificado TLS.
Probar la restauración al menos una vez por trimestre.

### 11.3 Restauración

```bash
# 1) Detener la aplicación (la base puede seguir)
docker compose stop app

# 2) Restaurar sobre una base limpia
docker compose exec -T db dropdb -U eet --if-exists eet_restore
docker compose exec -T db createdb -U eet eet_restore
docker compose exec -T db pg_restore -U eet -d eet_restore --no-owner < eet-2026-10-29.dump

# 3) Verificar antes de promover
docker compose exec db psql -U eet -d eet_restore -c \
  "select rol, count(*) from usuarios where is_active group by rol;"
docker compose exec db psql -U eet -d eet_restore -c \
  "select count(*) from auditoria;"

# 4) Promover (renombrar) y arrancar
docker compose exec db psql -U eet -d postgres -c \
  "alter database eet rename to eet_old; alter database eet_restore rename to eet;"
docker compose start app
```

Después de restaurar:

- **Las sesiones del backup pueden seguir siendo válidas**: revocarlas todas
  (`update sesiones set revoked_at = now() where revoked_at is null;`) para
  forzar re-login, sobre todo si el backup es de otro momento.
- Verificar que `EET_SECRET_KEY`, `EET_AUTH_PEPPER` y `EET_FERNET_KEYS`
  correspondan al backup. Con claves distintas, los TOTP no descifran y los
  códigos de respaldo no validan: los usuarios deberán re-enrolarse.
- Probar login de un administrador y una descarga de auditoría.
- Ejecutar `alembic upgrade head` si el backup es anterior a una migración
  (el arranque del contenedor ya lo hace).

### 11.4 Restauración de emergencia solo de cuentas

```bash
docker compose exec -T db pg_restore -U eet -d eet --no-owner \
  --data-only -t usuarios eet-auth-2026-10-29.dump
```

Cuidado: `--data-only` hace `COPY` y puede chocar con filas existentes; sobre
una tabla vacía o una base nueva es seguro. No restaura `mfa_totp` ni
`recovery_codes` salvo que se incluyan explícitamente.

---

## 12. Activación de `EET_AUTH_ENABLED`

La bandera arranca en `false` para permitir el despliegue gradual. Activarla
sin completar la lista es un incidente esperando ocurrir.

### 12.1 Antes de activar

- [ ] `alembic upgrade head` aplicado (el contenedor lo hace al arrancar).
- [ ] `EET_SECRET_KEY` y `EET_AUTH_PEPPER` definidos (32+/16+ caracteres).
- [ ] `EET_COOKIE_SECURE=true` y HTTPS con certificado válido.
- [ ] `EET_PUBLIC_BASE_URL` con `https://` y dominio real.
- [ ] SMTP probado con una cuenta real; SPF/DKIM/DMARC del dominio.
- [ ] `EET_ENVIRONMENT=prod` y `EET_EXPOSE_DOCS=false`.
- [ ] `EET_ADMIN_INTERNAL_ONLY=true` con los CIDR correctos y
  `EET_TRUSTED_PROXY_IPS` si hay proxy.
- [ ] Dos administradores creados y probados (TOTP enrolado).
- [ ] Backfill de `username`/`email` de las cuentas semilla.
- [ ] Backup reciente de la base y de los secretos.
- [ ] Runbook y contactos de soporte comunicados.

### 12.2 Activación

```bash
# Cambiar la variable en el entorno del despliegue y reiniciar
docker compose up -d app
docker compose logs --tail=50 app
```

Verificación inmediata:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://eet.aep.gov.py/          # 303 al login
curl -s -o /dev/null -w '%{http_code}\n' https://eet.aep.gov.py/salud     # 200
curl -sI https://eet.aep.gov.py/auth/login | Select-String -Pattern \
  'content-security-policy|strict-transport-security|cache-control'
```

- `/salud` y `/static/*` siguen públicos; el resto exige sesión.
- `/docs` y `/openapi.json` no responden.
- El login incorrecto no revela si la cuenta existe.

### 12.3 Rollback

Volver a `EET_AUTH_ENABLED=false` restaura el comportamiento anterior (sin
sesión), pero **deja la aplicación sin autenticación**: úsese solo como
contención inmediata, desde la red interna, y con un plazo definido. Las
cuentas, roles y auditoría permanecen en la base.

---

## 13. Checklists

### Diario

- [ ] `/salud` responde 200.
- [ ] Errores 5xx en el proxy o en el contenedor.
- [ ] `login_ip_locked`, `mfa_ip_locked` y `acceso_denegado` en auditoría.
- [ ] `email_outbox` sin filas con `intentos >= 5`.
- [ ] Uso de disco de PostgreSQL y tamaño de `auditoria`.

### Semanal

- [ ] Revisar altas, cambios de rol y bajas de la semana con su motivo.
- [ ] Verificar que siguen existiendo **dos** administradores activos.
- [ ] Revisar `password_reset_admin` y `mfa_reset_admin` contra pedidos reales.
- [ ] Probar login con TOTP y con código de respaldo de una cuenta de prueba.
- [ ] Confirmar backup diario y su integridad.

### Mensual / trimestral

- [ ] Restauración de prueba en una base aparte.
- [ ] Revisar intentos fallidos por IP y ajustar umbrales si hay falsos positivos.
- [ ] Revisar cuentas inactivas y dar de baja las que no correspondan.
- [ ] Actualizar dependencias y revisar avisos de seguridad.
- [ ] Revisar certificado TLS y fecha de vencimiento.
- [ ] Rotación planificada de claves si corresponde (§9).

### Incidente de seguridad

- [ ] Contener: revocar sesiones del usuario afectado y desactivar la cuenta si
  corresponde.
- [ ] Preservar: exportar la auditoría filtrada (CSV) antes de cualquier
  limpieza.
- [ ] Analizar: revisar `login_ok`/`login_fail`, `mfa_*`, `role_changed`,
  `password_reset_admin`, `mfa_reset_admin` y `acceso_denegado`.
- [ ] Erradicar: reset de contraseña y 2FA, revisar dispositivos.
- [ ] Recuperar: reactivar con contraseña temporal y re-enrolamiento.
- [ ] Documentar: motivo, alcance, decisiones y acciones de mejora.
- [ ] Rotar secretos si hubo acceso a la base o al servidor.

---

## 14. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `/auth/usuarios` devuelve 403 con sesión de admin | `EET_ADMIN_INTERNAL_ONLY=true` y la IP no está en `EET_INTERNAL_CIDRS`; o el proxy no está en `EET_TRUSTED_PROXY_IPS` | Revisar la IP real en el log de uvicorn/proxy y los CIDR (§8) |
| Todos los accesos parecen venir de `127.0.0.1` | Proxy local sin `EET_TRUSTED_PROXY_IPS` | Declarar el proxy o quitar el loopback del CIDR interno |
| «La cuenta ya está activa/inactiva» | Acción repetida | Refrescar la lista; el estado ya es el pedido |
| «No puede cambiar su propio rol» | Regla de separación de deberes | Otro administrador hace el cambio |
| «No puede restablecer su propia contraseña» | Regla deliberada | Usar `Cambiar contraseña` |
| El alta rechaza el correo | Dominio reservado (`.test`, `.invalid`, `.localhost`) o formato inválido | Usar el correo institucional real |
| CSV con acentos rotos en Excel | Se abrió como ANSI | Usar «Datos → Desde texto/CSV» con UTF-8; el archivo ya lleva BOM |
| El código de respaldo ya no sirve | Se usó o se regeneraron los códigos; o se rotó el pepper | Usar otro código o pedir reset de 2FA |
| TOTP no valida | Reloj del teléfono desincronizado | Activar hora automática; ventana permitida ±1 paso |
| No llega el correo | SMTP caído o en spam | Revisar §10.1, SPF/DKIM/DMARC y la cola `email_outbox` |
| La contraseña temporal no funciona | Se recargó la página y se generó otra | Pedir un nuevo reset y copiarla sin recargar |
