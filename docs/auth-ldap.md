# LDAP / Active Directory — proveedor de autenticación (Fase A5)

> Estado: **implementado y deshabilitado por defecto** (`EET_LDAP_ENABLED=false`).
> Hoy no existe un dominio AD institucional; este documento deja todo listo para
> que TI lo habilite cuando entregue los datos de conexión. Ver también
> `docs/fase-a5-ldap.md` (informe de la fase) y
> `docs/plan-implementacion-autenticacion.md` §A5.

---

## 1. Qué hace y qué no hace

Hace:

- Valida usuario y contraseña contra LDAP/AD con **bind de servicio + segundo
  bind** con el DN del usuario.
- Mapea grupos del directorio a los roles del sistema mediante configuración.
- Sincroniza nombre, correo, DN e identificador externo en cada login.
- Puede crear la cuenta local en el primer acceso válido (opt-in).
- Diagnostica la configuración con `python -m app.cli ldap-check`.

No hace:

- No guarda la contraseña del dominio ni hashes de AD (`password_hash` queda
  `NULL` en cuentas LDAP).
- No cae a autenticación local si el DC falla (*fail closed*).
- No administra el directorio ni crea usuarios en AD (no hay UI de A6).
- No restablece contraseñas de cuentas LDAP: se administran en el dominio.
- No abre ninguna conexión de red con `EET_LDAP_ENABLED=false`.

---

## 2. Requisitos para TI

Antes de activar el proveedor, TI debe entregar y garantizar:

1. **Cuenta de servicio de solo lectura** (no administradora) con permiso para
   leer el árbol de usuarios y grupos. Su contraseña se rota por procedimiento
   propio y se guarda solo en el gestor de secretos / variables de entorno.
2. **Endpoint LDAPS** (`ldaps://host:636`) o `ldap://` con StartTLS. Se
   **recomienda LDAPS**: el código valida el certificado del servidor con el
   almacén del sistema (`ssl.CERT_REQUIRED`).
3. **Certificado del DC emitido por una CA confiable** por el servidor donde
   corre la aplicación. Si la CA es interna, instalarla en el almacén del
   contenedor/host; no se desactiva la validación.
4. **Base DN** de búsqueda (por ejemplo `DC=aep,DC=gov,DC=py`).
5. **Filtro de usuario** acordado. El valor por defecto es
   `(sAMAccountName={login})`; también funciona `(userPrincipalName={login})`.
6. **Grupos de AD** que corresponden a cada rol y su DN o CN exacto.
7. **Atributos**: `displayName`, `mail`, `sAMAccountName` (o los que use el
   directorio) y `memberOf` (o `member` si se prefiere búsqueda inversa).
8. **Conectividad** desde el servidor de la aplicación al DC en el puerto
   correspondiente, con timeout de 5 s como máximo.
9. **Reloj sincronizado** (NTP) en app y DC: los protocolos de autenticación y
   los logs lo necesitan.

---

## 3. Variables de entorno

Todas usan el prefijo `EET_` y se leen al arrancar (requieren reinicio para
tomar efecto).

```env
# Interruptor maestro: apagado por defecto.
EET_LDAP_ENABLED=false

# Conexión
EET_LDAP_URL=ldaps://dc01.aep.gov.py:636
EET_LDAP_BIND_DN=CN=svc-eet,OU=Servicios,DC=aep,DC=gov,DC=py
EET_LDAP_BIND_PASSWORD=<secreto-en-el-gestor-de-secretos>
EET_LDAP_BASE_DN=DC=aep,DC=gov,DC=py
EET_LDAP_TIMEOUT=5
# Solo aplica a ldap:// (ldaps:// usa TLS directo).
EET_LDAP_STARTTLS=true

# Búsqueda y atributos
EET_LDAP_USER_FILTER=(sAMAccountName={login})
EET_LDAP_ATTR_LOGIN=sAMAccountName
EET_LDAP_ATTR_NOMBRE=displayName
EET_LDAP_ATTR_EMAIL=mail
# memberOf (atributo del usuario) o member (búsqueda inversa de grupos).
EET_LDAP_GROUP_ATTR=memberOf

# Roles
# JSON: "DN o CN del grupo" -> "rol del sistema".
EET_LDAP_ROLE_MAP={"CN=EET-Supervision,OU=Grupos,DC=aep,DC=gov,DC=py":"supervision","CN=EET-Carga,OU=Grupos,DC=aep,DC=gov,DC=py":"cargador","CN=EET-Consulta,OU=Grupos,DC=aep,DC=gov,DC=py":"consulta"}
# Rol si ningún grupo mapea. Vacío = denegar (recomendado).
EET_LDAP_DEFAULT_ROLE=
# Crear la cuenta local en el primer login válido. Desactivado por defecto.
EET_LDAP_AUTO_PROVISION=false
```

### Reglas de configuración

- Con `EET_LDAP_ENABLED=true` el arranque falla si falta URL, bind DN,
  contraseña o base DN. También falla si la URL no empieza con `ldap://` o
  `ldaps://`, si el mapa apunta a un rol desconocido o si el rol por defecto no
  es válido.
- Con la bandera apagada **no se exige nada** y no se intenta conectar.
- El mapa se compara sin distinguir mayúsculas; se aceptan DN completos
  (`CN=EET-Supervision,OU=Grupos,...`) o CN sueltos (`EET-Supervision`).
- Un JSON inválido en `EET_LDAP_ROLE_MAP` impide arrancar: es preferible un
  error explícito a permisos degradados en silencio.

---

## 4. Mapeo de grupos y prioridad de roles

Roles del sistema: `admin`, `supervision`, `cargador`, `consulta`, `asistente`.

Si el usuario pertenece a varios grupos mapeados, gana el de mayor prioridad:

```
admin > supervision > cargador > consulta > asistente
```

Si ningún grupo coincide:

- `EET_LDAP_DEFAULT_ROLE` vacío → **acceso denegado**, auditado como
  `ldap_sin_rol`.
- `EET_LDAP_DEFAULT_ROLE=consulta` → entra con ese rol (decisión explícita de
  TI; no se recomienda para roles operativos).

El rol se actualiza en cada login **solo si** `usuarios.role_source='ldap'`.
Una cuenta con `role_source='manual'` conserva el rol asignado por la
administración local (override consciente). El cambio queda auditado como
`ldap_sync` con los campos modificados.

### Cuentas y sincronización

En cada login válido se actualizan:

| Campo | Regla |
|---|---|
| `nombre` | `displayName` (o el atributo configurado) si viene y no está vacío |
| `email` | `mail` si viene y no lo usa otro usuario |
| `ldap_dn` | DN de la entrada encontrada |
| `external_id` | `sAMAccountName`/UPN (máx. 255) |
| `rol` | solo si `role_source='ldap'` |
| `password_hash` | **nunca** se escribe |

El correo solo se pisa si está libre: dos cuentas no pueden quedar con el mismo
email (el esquema tiene índice único funcional sobre `lower(email)`).

### Aprovisionamiento automático

Con `EET_LDAP_AUTO_PROVISION=true`, si el login no existe en `usuarios` y la
autenticación LDAP es válida **y** hay rol mapeado, se crea la cuenta con:

- `auth_provider='ldap'`, `role_source='ldap'`, `mfa_policy='any'`
- `password_hash=NULL`, `is_active=true`
- nombre, email, `ldap_dn` y `external_id` tomados del directorio

Si no hay rol mapeado no se crea nada. Si el `username` o el correo ya existen,
tampoco se sobreescribe: el login falla de forma genérica. El alta queda
auditada como `user_created` con `origen='ldap'`.

> Recomendación: mantener el aprovisionamiento **desactivado** hasta que el
> mapeo de grupos esté validado en producción; mientras tanto, las cuentas se
> crean por CLI/administración con `auth_provider='ldap'`.

---

## 5. Flujo técnico del login

1. El servicio busca el usuario por `username` o `email` (igual que el login
   local) y decide el proveedor por `usuarios.auth_provider`.
2. Si la cuenta es `ldap` y la bandera está apagada → fallo genérico y
   auditoría `ldap_disabled`, **sin** abrir red.
3. Con LDAP activo:
   1. Conexión al servidor con `Server(...)`; `ldaps://` usa TLS directo y
      `ldap://` negocia StartTLS si `EET_LDAP_STARTTLS=true`.
   2. Bind con la cuenta de servicio.
   3. Búsqueda en la base con `(&(<filtro>))`, tamaño 2 para detectar
      duplicados. El login se escapa con `escape_filter_chars`.
   4. **Segundo bind** con el DN del usuario y la contraseña tecleada.
   5. Lectura de `memberOf`/`member` y mapeo a rol.
   6. Sincronización y `commit`; devuelve el usuario.
4. El servicio mantiene lockout local (solo cuentas locales), contadores,
   `last_login_at` y auditoría `login_ok`/`login_fail`.
5. Cualquier error de LDAP produce un fallo genérico y `ldap_error` con un
   código corto; nunca se registra la contraseña ni el DN completo.

### Tiempos

Las cuentas locales usan Argon2id. Las LDAP no tienen hash local, así que un
fallo de red en 1 ms delataría diferencias; por eso se aplica un retardo mínimo
de 250 ms (`RETARDO_MINIMO_LDAP`) cuando el intento falla demasiado rápido. No
se aplica en el éxito.

### Bloqueo de cuenta

El bloqueo por `failed_attempts`/`locked_until` **solo aplica a cuentas
locales**. Aplicarlo a cuentas de dominio permitiría a cualquiera bloquear a un
empleado con 5 intentos fallidos (DoS trivial). AD administra su propia
política de contraseñas y bloqueo. Todos los intentos quedan en
`login_attempts` para el rate limiting de A6 y el análisis forense.

---

## 6. Activación

1. Completar las variables `EET_LDAP_*` en el entorno del servicio (no en
   `docker-compose.yml` versionado; usar secretos).
2. Verificar conectividad y certificado:
   ```powershell
   Test-NetConnection dc01.aep.gov.py -Port 636
   openssl s_client -connect dc01.aep.gov.py:636 -showcerts
   ```
3. Ejecutar el diagnóstico **antes** de habilitar:
   ```powershell
   uv run python -m app.cli ldap-check --login usuario.de.prueba
   ```
4. Revisar el mapeo:
   ```powershell
   uv run python -m app.cli ldap-mapeo
   ```
5. Crear/ajustar las cuentas locales con `auth_provider='ldap'` (o activar
   `EET_LDAP_AUTO_PROVISION=true` de forma controlada).
6. Poner `EET_LDAP_ENABLED=true` y **reiniciar** el servicio.
7. Probar un login real de un usuario de cada rol y revisar `auditoria`
   (`login_ok`, `ldap_sync`, `ldap_sin_rol`).
8. Mantener una cuenta **break-glass** local (`auth_provider='local'`, rol
   `admin`, TOTP obligatorio) fuera del mapeo LDAP para operar si el DC cae.

---

## 7. Diagnóstico: `ldap-check`

```powershell
# Con el directorio apagado: mensaje claro, exit 0 y ninguna conexión.
uv run python -m app.cli ldap-check

# Con el directorio activo: conexión, bind, base, grupos y usuario opcional.
uv run python -m app.cli ldap-check --login jperez

# Mapa configurado (sin secretos).
uv run python -m app.cli ldap-mapeo
```

Salida típica:

```
Diagnóstico del directorio LDAP/Active Directory
  URL:              ldaps://dc01.aep.gov.py:636
  Base DN:          DC=aep,DC=gov,DC=py
  Cuenta servicio:  CN=svc-eet,OU=Servicios,DC=aep,DC=gov,DC=py
  Filtro usuario:   (sAMAccountName={login})
  Transporte:       LDAPS (TLS directo)
  ...
  [OK   ] Conexión y bind de servicio: ldaps://dc01.aep.gov.py:636 (LDAPS)
  [OK   ] Entradas visibles en la base: 2 de ejemplo (máx. 5)
  [OK   ] Grupos en la base: 12 visible(s) (máx. 500)
  [OK   ] Búsqueda del usuario jperez: 3 grupo(s); rol resultante: supervision
```

- Código de salida `0`: todo correcto **o** directorio deshabilitado (no es un
  error).
- Código de salida `1`: configuración incompleta o fallo de conexión/búsqueda.
- Nunca imprime `EET_LDAP_BIND_PASSWORD` ni contraseñas de usuario.

---

## 8. Solución de problemas

| Síntoma | Causa probable | Acción |
|---|---|---|
| `[ERROR] Conexión y bind de servicio: invalidCredentials` (AD código **49**) | Contraseña de servicio vencida o cuenta bloqueada | Rotar la contraseña de servicio y actualizar el secreto |
| AD código **32** (`noSuchObject`) | `EET_LDAP_BASE_DN` incorrecto o sin permiso de lectura | Revisar el DN base y los permisos de la cuenta de servicio |
| AD código **34** (`invalidDNSyntax`) | DN mal formado (coma, escape, OU inexistente) | Corregir el DN; probar con `ldapsearch`/`ldp.exe` |
| `timeout` / `socket.timeout` | Firewall, DC caído o latencia > `EET_LDAP_TIMEOUT` | Verificar puerto 636/389, subir el timeout con criterio |
| Error de certificado (`CERTIFICATE_VERIFY_FAILED`) | CA interna no confiable en el host | Instalar la CA raíz/intermedia en el almacén del sistema |
| `ldap_sin_rol` en auditoría | El usuario no está en ningún grupo mapeado | Agregarlo al grupo o ajustar `EET_LDAP_ROLE_MAP` |
| `ldap_ambiguo` | Más de una entrada coincide con el filtro | Acotar `EET_LDAP_USER_FILTER` (p. ej. `(&(objectClass=user)(sAMAccountName={login}))`) |
| Login válido pero rol incorrecto | `role_source='manual'` en la cuenta | Cambiar a `role_source='ldap'` si el rol debe seguir al directorio |
| El login no entra y `ldap_disabled` aparece | Bandera apagada | Activar `EET_LDAP_ENABLED=true` y reiniciar |
| El correo no se actualiza | Otro usuario ya usa ese `mail` | Resolver el duplicado; no se sobreescribe por seguridad |

Para depurar sin la aplicación, usar `ldp.exe` (Windows) o:

```bash
ldapsearch -H ldaps://dc01.aep.gov.py:636 -D "CN=svc-eet,..." -W \
  -b "DC=aep,DC=gov,DC=py" "(sAMAccountName=jperez)" displayName mail memberOf
```

---

## 9. Seguridad

- La contraseña del dominio viaja solo en el bind TLS y **no se persiste**.
- La cuenta de servicio debe ser de solo lectura y con contraseña rotada.
- Un fallo de LDAP no habilita el login local (*fail closed*).
- Los errores al usuario son genéricos; los códigos técnicos quedan solo en
  auditoría (`ldap_error`, `ldap_fail`, `ldap_ambiguo`, `ldap_sin_rol`,
  `ldap_disabled`, `ldap_sync`).
- El login se escapa antes de interpolarse en el filtro
  (`escape_filter_chars`), lo que neutraliza inyección LDAP.
- Se valida el certificado del servidor; no se ofrece una opción para
  desactivar la validación.
- Break-glass: al menos un administrador local fuera del directorio.

---

## 10. Checklist de activación futura

- [ ] Dominio y base DN definidos por TI.
- [ ] Cuenta de servicio de solo lectura creada y secreto cargado.
- [ ] LDAPS o StartTLS operativo; certificado validado desde el servidor.
- [ ] Grupos por rol creados y DN/CN confirmados.
- [ ] `EET_LDAP_ROLE_MAP` revisado con `ldap-mapeo`.
- [ ] `ldap-check` en verde (conexión, bind, base, grupos).
- [ ] `ldap-check --login` con un usuario de cada rol: rol esperado.
- [ ] Cuentas locales con `auth_provider='ldap'` (o aprovisionamiento
      automático evaluado y aprobado).
- [ ] Break-glass local con TOTP verificado.
- [ ] Plan de contingencia si el DC cae (break-glass + comunicación).
- [ ] Prueba de login real por rol y revisión de auditoría.
- [ ] `EET_LDAP_ENABLED=true` en el entorno y reinicio del servicio.
- [ ] Monitoreo de `ldap_error`/`ldap_fail` y alertas de picos.

---

## 11. Situación actual

No existe AD institucional al momento de esta implementación. Por eso:

- `EET_LDAP_ENABLED=false` por defecto; la aplicación arranca y opera igual.
- Todo el flujo está cubierto por pruebas con un directorio simulado
  (`tests/test_auth_ldap.py`), sin red.
- La UI de administración del directorio queda para la Fase A6.
