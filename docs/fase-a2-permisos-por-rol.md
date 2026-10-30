# Fase A2 — Autorización por rol y alcance de datos

> Implementación de la Fase A2 de `docs/plan-implementacion-autenticacion.md`
> (matriz de `docs/analisis-autenticacion.md` §6.4).
> No incluye TOTP (A3), correo (A4), LDAP (A5) ni la UI de gestión de usuarios
> (A6): el CLI `crear-usuario` sigue siendo la vía de alta.
> Con `EET_AUTH_ENABLED=false` todo se comporta como antes de la Fase A1.

---

## 1. Alcance entregado

| Pieza | Archivo |
|---|---|
| Matriz única de permisos, etiquetas de rol y helper `puede` | `app/auth/permissions.py` |
| `require_permiso(...)`, `exigir_permiso(...)`, `negar_acceso(...)` | `app/auth/dependencies.py` |
| Globales Jinja `puede(request, permiso)` y `rol_etiqueta` | `app/web.py` |
| Guardas de los 8 routers | `app/routers/*.py` |
| Navegación y acciones condicionadas | `app/templates/*` |
| Pruebas de autorización y alcance | `tests/test_auth_roles.py` |

## 2. Matriz implementada

Una clave por **acción de negocio** (nunca por ruta): mover una URL no cambia
la autorización. Un rol que no figura en la clave queda denegado (cerrado por
defecto).

| Permiso | admin | supervision | cargador | asistente | consulta |
|---|:--:|:--:|:--:|:--:|:--:|
| `dashboard.ver` | ✓ | ✓ | ✓ | ✓ (propio) | ✓ |
| `entregas.ver` | ✓ | ✓ | ✓ | — | ✓ |
| `entregas.propias` | — | — | — | ✓ | — |
| `entregas.gestionar` | — | ✓ | ✓ | — | — |
| `cruces.ver` | — | ✓ | ✓ | — | ✓ |
| `qaqc.ver` | — | ✓ | ✓ | — | ✓ |
| `qaqc.decidir` | — | ✓ | — | — | — |
| `reemplazos.ver` | — | ✓ | ✓ | — | ✓ |
| `reemplazos.proponer` | — | ✓ | ✓ | — | — |
| `reemplazos.autorizar` | — | ✓ | — | — | — |
| `malla.ver` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `malla.cargar` | — | ✓ | ✓ | — | — |
| `mapa.ver` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `mapa.cargar` | — | ✓ | ✓ | — | — |
| `exportar` | — | ✓ | — | — | ✓ |
| `usuarios.gestionar` | ✓ | — | — | — | — |
| `auditoria.ver` | ✓ | ✓ | — | — | — |

**Separación de deberes:** `admin` administra cuentas, auditoría y políticas,
pero no dictamina fichas (`qaqc.decidir`) ni autoriza reemplazos
(`reemplazos.autorizar`); además no ve cruces, QA/QC ni el consolidado.

## 3. Contrato de las dependencias

```python
@router.get("", dependencies=[Depends(require_permiso("qaqc.ver"))])
```

- `EET_AUTH_ENABLED=false` → la dependencia devuelve `None` y **no** consulta
  la base: la aplicación queda igual que antes de A1 (los 191 unitarios y los
  143 de integración previos siguen verdes).
- Con la bandera activa y sin sesión, el `AuthGate` ya redirigió; si aun así no
  hay usuario se responde `401`.
- Con usuario sin permiso: `403` con «No tiene permiso para esta acción.» y una
  fila de auditoría `acceso_denegado`.
- Varios permisos en la misma dependencia son **alternativa** (`entregas.ver`
  *o* `entregas.propias`), que es lo que necesita el listado de entregas.
- `exigir_permiso(request, ...)` permite validar dentro del endpoint cuando la
  decisión depende del formulario (`/reemplazos/{id}/estado`).
- `negar_acceso(request, usuario, permiso, extra)` audita y responde 403 cuando
  la denegación es por el **dato** y no por el rol (entrega de otra letra).
- `require_roles(*roles)` se conserva por compatibilidad, sin uso nuevo.

### Auditoría del 403

`acceso_denegado` guarda `ruta`, `metodo`, `permiso`, `rol`,
`roles_autorizados` e `ip`/`user_agent`, con actor `usuario:<id>`. Se escribe en
una transacción corta e independiente (el 403 interrumpe la petición antes de
abrir la unidad de trabajo del endpoint) y un fallo al auditar nunca enmascara
el 403. El filtro `_detalle_seguro` de `app/auth/audit.py` descarta cualquier
clave sospechosa: no se registran tokens, CSRF ni contraseñas.

## 4. Rutas protegidas

| Router | Lectura | Acciones |
|---|---|---|
| `dashboard` | `dashboard.ver` (router) | — |
| `entregas` | `entregas.ver` **o** `entregas.propias` (router) | `upload`/`confirm`/`reject` → `entregas.gestionar`; `preview` acota por letra |
| `cruces` | `cruces.ver` (router) | — (solo lectura, incluso para `cargador`) |
| `qaqc` | `qaqc.ver` (router) | `POST /{id}` y `POST /{id}/evento` → `qaqc.decidir` |
| `reemplazos` | `reemplazos.ver` (router) | `POST /` y `/{id}/proponer` → `reemplazos.proponer`; `/{id}/estado` con `autorizado`/`completado` → `reemplazos.autorizar` |
| `malla` | `malla.ver` (router) | `upload` → `malla.cargar` |
| `mapa` | `mapa.ver` (router) | `upload` → `mapa.cargar` |
| `exportar` | `exportar` (router, página y `.xlsx`) | — |

El árbol de transiciones de reemplazos y el de fichas **no** se tocaron: la
autorización decide quién puede intentarlo; la validez del cambio sigue siendo
del servicio (`persist.transicionar_*`).

## 5. Alcance de datos del asistente

- `/entregas`: el listado, los contadores por estado y las semanas del filtro se
  calculan sobre `Entrega.asistente_letra == usuario.letra_terna`. La letra
  propia manda sobre el parámetro `?asistente=` de la URL. Un asistente **sin**
  letra asignada no ve ninguna entrega (no es lo mismo que "ve todas").
- `/entregas/{id}/preview`: si la entrega no es de su letra se responde 403
  **antes de leer el archivo**; el `.xlsx` de otra evaluadora nunca se abre.
- `/` (dashboard): solo un panel con sus entregas y un aviso de que los
  resultados consolidados no están disponibles para su rol. No se muestran la
  concordancia de la terna, los KPIs de cruces, el estado del flujo ni las
  últimas entregas globales.
- El filtro se hace en el router (`_entregas_propias`), sin tocar
  `persist.resumen_dashboard`, que sigue devolviendo el proyecto completo.

## 6. Navegación y UI condicionada

`app/web.py` expone el global Jinja `puede(request, permiso)` (y
`rol_etiqueta`) que delega en `app.auth.permissions.puede`. **La UI solo
oculta**: el servidor responde 403 igual si se escribe la URL.

- `components/shell.html`: la navegación se arma con `puede(...)`; los grupos
  sin ítems permitidos no se imprimen. Un `asistente` ve Inicio, Entregas,
  Malla y Mapa; nunca Cruces, QA/QC, Reemplazos ni Exportar. El menú de usuario
  y el logout se mantienen.
- `entregas.html`: el bloque de carga solo con `entregas.gestionar`; sin
  permiso se explica que el alta corresponde a supervisión y carga.
- `entrega_preview.html`: confirmar/rechazar solo con `entregas.gestionar`.
- `qaqc_detalle.html`: sin `qaqc.decidir` no se renderiza ningún formulario de
  la ficha; se muestra el dictamen guardado en modo lectura (el único POST de
  la página es el logout del shell) y se oculta el panel de transiciones.
- `reemplazos.html`: «Reportar» y «Proponer reserva» con
  `reemplazos.proponer`; «Autorizar» y «Completar» con `reemplazos.autorizar`.
- `malla.html` / `mapa.html`: la tarjeta de carga solo con `*.cargar`.
- `exportar.html`: el botón de descarga solo con `exportar`.
- `qaqc_list.html` y `cruces.html` no tienen acciones propias (solo enlaces).

No se cambió ningún `name=` de formulario ni ningún texto normativo, y todos
los POST conservan el campo CSRF (`tests/test_auth_templates.py` sigue verde).

## 7. Pruebas

```powershell
uv run pytest -m integration tests/test_auth_roles.py -q   # 13 pruebas
```

`tests/test_auth_roles.py` crea cuentas reales por rol (con `hash_password`),
entregas y fichas de prueba, y las borra en el teardown (sesiones, auditoría,
intentos y entregas incluidos). Cubre:

| Rol | Comprobaciones |
|---|---|
| `asistente` | `/` 200 sin concordancia; `/entregas` 200 solo con su letra; preview de otra letra 403; `/cruces`, `/qaqc`, `/exportar`, `/reemplazos` 403; navegación reducida; sin letra → sin datos |
| `cargador` | `/cruces` y `/qaqc` 200; `POST /qaqc/{id}/evento` 403; `POST /entregas/upload` sin archivo 422 (no 403); `/exportar` 403; ficha en modo lectura sin formularios |
| `supervision` | lecturas 200; `POST /qaqc/{id}/evento` inválido 303 (no 403); formulario de dictamen visible |
| `consulta` | lecturas y `/exportar` 200; `POST /reemplazos` 403; sin formularios de carga |
| `admin` | `/` 200; `POST /qaqc/{id}/evento` 403; `POST /reemplazos/{id}/estado` 403 |
| 403 | HTML `error.html` («Acceso restringido») y JSON cuando `Accept: application/json` |
| Auditoría | `acceso_denegado` con ruta, método, rol e IP, sin secretos |
| Bandera apagada | `/`, `/cruces`, `/qaqc`, `/entregas`, `/exportar` 200 sin sesión |

## 8. Desviaciones y decisiones

1. **Panel consolidado del dashboard `admin`.** La instrucción pedía ocultarlo
   con `puede('cruces.ver')`; como la matriz §6.4 le asigna «Dashboard: leer» a
   `admin` (y `cruces.ver` no lo incluye), se muestra también a `admin` para no
   dejarlo con un panel vacío. `cruces.ver` sigue gobernando navegación y rutas.
2. **`require_permiso` acepta varios permisos como alternativa** en lugar de uno
   solo: `/entregas` necesita `entregas.ver` *o* `entregas.propias` y así se
   evita duplicar la guarda en el router.
3. **`/reemplazos/{id}/estado` valida dentro del endpoint** (`exigir_permiso`),
   porque el permiso depende de `nuevo_estado`; una dependencia estática no
   puede leer el formulario sin duplicar el parseo.
4. **`admin` no tiene `reemplazos.ver`** (matriz §6.4), así que su 403 en
   `/reemplazos/{id}/estado` sale de la guarda de lectura del router antes de
   llegar a la acción. El resultado observable es el mismo: 403.
5. **Sin `require_mfa_reciente`.** Depende del enrolamiento TOTP (A3); se
   incorporará con la política por rol.
6. **Sin UI de gestión de usuarios.** `usuarios.gestionar` y `auditoria.ver`
   quedan en la matriz y aplicados por el CLI, pero la pantalla es A6.
