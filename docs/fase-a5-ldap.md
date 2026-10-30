# Fase A5 — LDAP/AD parametrizado (informe)

> Fecha: 2026-10-29
> Alcance: proveedor LDAP completo, configuración, mapeo de grupos a roles,
> sincronización, comandos `ldap-check`/`ldap-mapeo` y pruebas con conexión
> simulada. **No** incluye la UI de administración del directorio (A6).
> Estado: implementada y **deshabilitada por defecto** (`EET_LDAP_ENABLED=false`).

Documentación operativa: [`docs/auth-ldap.md`](auth-ldap.md).

---

## 1. Entregado

| Tarea A5 | Implementación |
|---|---|
| A5.1 provider con LDAPS/StartTLS, validación de certificado y timeout | `app/auth/providers.py` (`LdapProvider._servidor`, `_conectar`) |
| A5.2 búsqueda con cuenta de servicio + segundo bind | `LdapProvider.autenticar_detallado` |
| A5.3 mapeo grupos→rol con prioridad y `role_source` | `elegir_rol`, `_sincronizar` |
| A5.4 sincronización de nombre/email sin contraseña | `_sincronizar` |
| A5.5 registro condicional / sin red si está apagado | `proveedor()`, guardas en `LdapProvider` |
| A5.6 `ldap-check` legible | `app/cli.py` (`_cmd_ldap_check`) |
| A5.7 pruebas con servidor simulado | `tests/test_auth_ldap.py` (11 unitarias + 15 integración) |
| A5.8 estado del directorio en UI admin | **Fuera de alcance (A6)** |
| A5.9 break-glass | Documentado; la cuenta local ya existe por CLI y no se toca |

Extra: `ldap-mapeo` para que TI revise el mapa sin exponer secretos.

---

## 2. Diseño del proveedor

```
service.autenticar
  ├─ buscar_usuario(username | email)
  ├─ proveedor(usuario)                 ← elige Local o LDAP
  │    ├─ bandera apagada → LocalProvider (fallo genérico, sin red)
  │    ├─ auth_provider=ldap + activo → LdapProvider
  │    └─ login inexistente + auto_provision → LdapProvider
  └─ LdapProvider.autenticar_detallado
       1. configuración completa
       2. Server(url, use_ssl, tls=CERT_REQUIRED, connect_timeout)
       3. Connection(bind_dn) + start_tls (si ldap://) + bind
       4. search(base_dn, (&(filtro)), size_limit=2)
       5. 0 entradas → no_encontrado | >1 → ambiguo
       6. Connection(dn_usuario, password) + bind   ← valida la contraseña
       7. grupos (memberOf | member) → elegir_rol
       8. sincronizar nombre/email/dn/external_id/rol + commit
```

### Contrato

- `authenticate(db, login, password) -> (Usuario | None, False)`.
- `autenticar_detallado(...) -> ResultadoLdap` con `motivo` y detalle no
  sensible, para que el servicio audite con IP/agente y aplique el retardo.
- `diagnostico(login=None) -> DiagnosticoLdap` para el CLI.

### Conexión inyectable

`LdapProvider(connection_factory=...)` recibe cualquier fábrica con
`bind/search/entries/start_tls/unbind`. La fábrica global
`providers._FABRICA_CONEXION` permite inyectar el simulador desde las pruebas
sin tocar código de producción. Por defecto usa `ldap3.Server` y
`ldap3.Connection` con `get_info=NONE` (evita descargar el esquema en cada
login).

### Errores

Se capturan `LDAPException`, `socket.timeout` y `OSError`. El usuario recibe
siempre el mensaje genérico; la auditoría guarda un código corto
(`_codigo_error`), nunca el mensaje con DN ni la contraseña. No hay fallback a
autenticación local.

### Tiempos

Las cuentas LDAP no usan Argon2id. Para no delatar un fallo instantáneo se
aplica `RETARDO_MINIMO_LDAP = 0.25 s` solo cuando el intento falla; en pruebas
se pone en `0` con `monkeypatch`.

---

## 3. Configuración

Se reutilizan `ldap_enabled`, `ldap_url`, `ldap_bind_dn`,
`ldap_bind_password`, `ldap_base_dn`, `ldap_user_filter`, `ldap_role_map` y
`ldap_timeout`.

Nuevas:

| Variable | Default | Uso |
|---|---|---|
| `EET_LDAP_STARTTLS` | `true` | StartTLS para `ldap://`; ignorado en `ldaps://` |
| `EET_LDAP_DEFAULT_ROLE` | `""` | Rol si ningún grupo mapea; vacío = denegar |
| `EET_LDAP_AUTO_PROVISION` | `false` | Crear cuenta local en el primer login válido |
| `EET_LDAP_GROUP_ATTR` | `memberOf` | `memberOf` (atributo) o `member` (búsqueda inversa) |
| `EET_LDAP_ATTR_NOMBRE` | `displayName` | Atributo del nombre visible |
| `EET_LDAP_ATTR_EMAIL` | `mail` | Atributo del correo |
| `EET_LDAP_ATTR_LOGIN` | `sAMAccountName` | Atributo del identificador |

Validaciones al arrancar (solo con `ldap_enabled=true`): URL, bind DN,
contraseña y base DN obligatorios; esquema `ldap://`/`ldaps://`; roles del mapa
y rol por defecto válidos. Con la bandera apagada no se exige nada.

`ldap_role_map_dict` normaliza claves y valores a minúsculas y conserva el
parseo estricto del JSON.

---

## 4. Mapeo y prioridad

- Acepta DN completo o CN suelto, sin distinguir mayúsculas.
- Prioridad: `admin > supervision > cargador > consulta > asistente`.
- Sin coincidencia y `ldap_default_role` vacío → `ldap_sin_rol` y acceso
  denegado.
- `role_source='manual'` conserva el rol local; `role_source='ldap'` se
  actualiza en cada login.
- El correo solo se actualiza si no lo usa otra cuenta.

---

## 5. Pruebas

`tests/test_auth_ldap.py`:

Unitarias (sin red ni base):

1. normalización del mapa;
2. prioridad `admin` sobre el resto;
3. CN suelto y denegación por defecto;
4. escape del login en el filtro;
5. bandera apagada no instancia conexión;
6. diagnóstico apagado no conecta;
7. diagnóstico con simulador (grupos y rol);
8. StartTLS en `ldap://` y TLS directo en `ldaps://`;
9. `ldap-check` apagado (exit 0, sin conexión);
10. `ldap-mapeo` sin secretos.

Integración (PostgreSQL, directorio simulado):

1. login válido sincroniza nombre/email/DN/external_id/rol;
2. contraseña incorrecta en el segundo bind;
3. usuario no encontrado;
4. entrada duplicada (`ldap_ambiguo`);
5. sin grupo mapeado (`ldap_sin_rol`);
6. rol por defecto explícito;
7. prioridad consulta + supervisión;
8. `role_source='manual'` no se sobrescribe;
9. excepción/timeout/OSError → `ldap_error` controlado (3 casos);
10. aprovisionamiento automático con rol;
11. aprovisionamiento sin rol no crea;
12. LDAP apagado con cuenta LDAP → `ldap_disabled` sin red;
13. el filtro enviado escapa `*)(uid=*`.

Resultados de la fase:

```
uv run pytest -q                     → 208 passed (197 previas + 11 nuevas)
uv run pytest -m integration -q      → 213 passed (198 previas + 15 nuevas)
```

---

## 6. Decisiones

1. **Auditoría técnica en el proveedor, resultado de login en el servicio.**
   El proveedor registra `ldap_fail`, `ldap_ambiguo`, `ldap_sin_rol`,
   `ldap_error`, `ldap_sync` y `user_created`; el servicio añade `login_ok`,
   `login_fail` y `ldap_disabled` con IP y agente. Así se conserva el contexto
   de red sin acoplar el proveedor al request.
2. **Sin bloqueo de cuenta para LDAP.** El bloqueo local permitiría DoS contra
   empleados; AD ya aplica su política. Todos los intentos quedan registrados.
3. **Sin fallback local.** Un DC caído no habilita credenciales locales, tal
   como exige el análisis §6.3.
4. **Correo no se sobreescribe si está en uso.** Evita que un atributo de AD
   redirija recuperaciones de otra cuenta.
5. **Aprovisionamiento desactivado por defecto.** Crear cuentas automáticamente
   sin validar el mapa en producción es riesgoso; queda como opt-in.
6. **`member` implementado como búsqueda inversa.** Es la semántica correcta en
   AD; `memberOf` se lee directo de la entrada.
7. **`get_info=NONE`.** No se descarga el esquema en cada conexión: menos
   latencia y menos dependencia de permisos.

---

## 7. Desviaciones

- **A5.8 (UI de administración del directorio) no se implementa**: la tarea
  pedía expresamente no hacer A6. El estado se puede consultar por CLI
  (`ldap-check`, `ldap-mapeo`).
- **A5.9 break-glass**: no se crea un usuario nuevo automáticamente; se
  documenta el procedimiento y se reutiliza `crear-admin` (cuenta local,
  `auth_provider='local'`, TOTP obligatorio).
- La UI no muestra "Directorio: no configurado" todavía; esa etiqueta queda
  para A6.

---

## 8. Qué falta para activar AD

1. Datos de TI: URL LDAPS, base DN, cuenta de servicio, grupos y atributos.
2. Certificado de la CA interna confiable en el host.
3. Cargar secretos en el entorno (nunca en el repositorio).
4. `ldap-check` y `ldap-check --login` en verde.
5. Definir si las cuentas se crean manualmente o con aprovisionamiento
   automático.
6. Verificar el break-glass local.
7. `EET_LDAP_ENABLED=true` + reinicio.
8. Prueba por rol y revisión de auditoría.

Hasta entonces, el sistema sigue con login local y la bandera apagada; no se
abre ninguna conexión al directorio.
