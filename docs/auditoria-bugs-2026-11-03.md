# Auditoría de bugs y errores de código — 2026-11-03

Sistema: Validador EET (FastAPI + SQLAlchemy 2.0 + PostGIS + Jinja2).
Alcance: `app/`, `migrations/`, `scripts/`, `deploy/`, `tests/`.

## Metodología

- Revisión paralela con 4 subagentes especializados por área:
  1. Autenticación, sesiones, CSRF, MFA, LDAP y correo.
  2. Dominio EET, importador, persistencia y análisis estadístico.
  3. Routers, plantillas, JavaScript y exportaciones.
  4. CLI, configuración, Alembic, Docker, nginx y scripts operativos.
- Verificación local con `ruff`, `pytest`, `compileall`, importación de la app y
  pruebas puntuales de comportamiento (parser de puntos, openpyxl, URLs).
- Las conclusiones de los subagentes fueron contrastadas leyendo el código y
  ejecutando pruebas cuando era posible.

### Estado de las pruebas

Primera pasada local (sin Docker):

```
pytest -q                -> 243 passed, 271 deselected (integration)
ruff check app tests scripts -> 145 avisos (mayoría estilo; 10 relevantes)
compileall app scripts migrations -> OK
import app.main          -> OK
```

Después se instaló WSL 2 + Docker (Ubuntu 26.04) y se ejecutó el despliegue
completo contra PostgreSQL 16/PostGIS 3.4:

```
pytest -m integration -o addopts=  -> 273 passed
pytest -q (sin integration)        -> 290 passed
```

Ver “Despliegue validado” al final para el detalle del entorno.

---

## Críticos

### C-1. Una entrega con errores de validación se acepta igualmente

- `app/services/persist.py:154-206`
- `app/services/importer.py:218-233`
- `app/templates/entrega_preview.html:95-130`

`confirmar_entrega()` vuelve a parsear el archivo pero nunca inspecciona
`parsed.errores`. La vista previa afirma “Los cruces con errores no se
confirmarán”, pero el botón “Confirmar y cruzar” se renderiza siempre y el
servicio marca la entrega como `aceptada`.

Consecuencia: celdas con etiquetas inválidas se descartan y las filas
duplicadas contradictorias se resuelven quedándose con la primera, sin bloquear
la confirmación. El usuario cree que no se guardó nada erróneo.

Fix: rechazar la confirmación si `parsed.errores` no está vacío (o exigir una
confirmación explícita y auditada), y definir el comportamiento ante duplicados
(fallar o “último gana” con auditoría).

### C-2. Borrar una celda en una corrección no borra la interpretación anterior

- `app/services/persist.py:164-178`
- `app/services/persist.py:437-445`

La lógica trata el vacío como “ausencia” (`continue`, no se guarda fila), pero
`_ultima_interpretacion()` busca la última entrega aceptada que sí tenga fila.
Si A entregó `(101, 1990) = clase 5` y en una corrección vacía esa celda, la
fila antigua sigue siendo la “última” y el cruce conserva la clase 5 para
siempre. No existe tombstone ni borrado lógico.

Fix: persistir explícitamente la ausencia (por ejemplo `es_ausente` o una fila
con clase NULL) y hacer que la lectura se detenga en la entrega más reciente
aunque no haya valor.

---

## Altos

### A-1. El orden de versiones es incorrecto entre semanas

- `app/services/persist.py:129-132` (versión por `letra + semana`)
- `app/services/persist.py:437-445` (ordena solo por `version DESC`)

Las versiones se numeran por semana, pero la consulta de “última
interpretación” ordena por `version` global. La semana 05 corregida (v2) gana
sobre la semana 06 recién entregada (v1). Un dato viejo puede sobrescribir al
más nuevo.

Fix: ordenar por `Entrega.recibida_en DESC, Entrega.id DESC`, o usar una
versión monotónica global por letra.

### A-2. Recalcular un cruce pisa una decisión QA/QC ya cerrada

- `app/services/persist.py:474-477`
- `app/services/persist.py:867-879`

`recalcular_cruce()` sobrescribe siempre `clase_consolidada` y
`regla_version`. Si supervisión cerró una ficha por consenso y luego entra una
entrega que toca ese punto/año, la clase consolidada vuelve al valor automático
y la ficha permanece cerrada: el dashboard no muestra nada pendiente y la
decisión humana desaparece sin rastro.

Fix: si existe una ficha cerrada, preservar el consenso o reabrirla
explícitamente con auditoría; nunca sobrescribir un resultado `qaqc-*` en
silencio.

### A-3. (Reclasificado) La ficha QA/QC solo se abre para discrepancia absoluta o SC

- `app/services/persist.py:456-463` y `480-494`
- `app/domain/cross_engine.py:56-74`
- `app/services/store.py:86-89`
- Protocolo: `docs/analiza el modelo y el Protocolo, como acotacion l.md:126-144,235-244`

La revisión inicial interpretó que “mayoría” e “incompleto” también debían
abrir ficha porque `resolver_tabla7()` los marca como revisables. Al contrastar
con el protocolo se confirmó lo contrario: la tabla 6.3 muestra **Ficha “—”**
para mayoría (`1,5,1`) y para terna incompleta (`6,—,6`), y el texto indica que
los registros incompletos no son desacuerdos. La persistencia ya aplicaba la
regla correcta (3 clases distintas o SC); la inconsistencia estaba en el
`Store` en memoria, que encolaba ambos casos.

Fix aplicado: regla única `requiere_ficha_qaqc()` en `app/domain/qaqc.py`,
usada por `persist.recalcular_cruce()` y por `Store.ejecutar_cruce()`, con
pruebas para mayoría, incompleto, discrepancia y SC.

### A-4. `clase_definitiva` no se valida en rango 1–9

- `app/routers/qaqc.py:157-161`
- `app/services/persist.py:833-834`, `865-875`, `653`

El router solo comprueba que sea entero. Un POST con `clase_definitiva=99` y
`dictamen=consenso` se guarda y se copia a
`resultados_cruce.clase_consolidada`; luego `_muestra_con_mapa()` hace
`_CLASES[cons - 1]` y produce `IndexError` (500). Con `0` se indexa `-1` y se
devuelve silenciosamente “Otras tierras”.

Fix: validar `1 <= n <= 9` en router, servicio y modelo; hacer que
`clase_a_dominio()` rechace códigos fuera de rango.

### A-5. El límite de envío de códigos por correo se evade desde `/auth/login`

- `app/auth/router.py:175-179`
- `app/auth/service.py:1138-1188`

El reenvío (`/auth/mfa/enviar`) sí aplica `puede_enviar_codigo()` y
`puede_enviar_codigo_por_ip()`, pero el login inicial llama directamente a
`emitir_codigo_email()`. Cada POST con contraseña correcta invalida el desafío
anterior y envía otro correo, sin cooldown de 60 s ni topes de 5/hora/cuenta y
10/hora/IP. Permite inundar el buzón/SMTP y anular el código que el usuario
legítimo está introduciendo.

Fix: aplicar ambos límites también en el login (o dentro de
`emitir_codigo_email()`), y limitar la creación de desafíos por cuenta/IP.

### A-6. Cambiar el rol no actualiza la política MFA ni la fuente del rol

- `app/auth/service.py:2090-2092`
- `app/auth/providers.py:620`, `655`

`cambiar_rol_usuario()` solo asigna `usuario.rol`. No recalcula
`mfa_policy` (un `consulta` con MFA desactivado promovido a `admin` queda sin
TOTP) ni cambia `role_source`. En cuentas LDAP, el siguiente login vuelve a
aplicar el rol del directorio y revierte la degradación manual hecha por un
administrador.

Fix: recalcular la política al cambiar de rol y marcar `role_source="manual"`
cuando un administrador impone un rol; al provisionar LDAP, derivar la política
del rol mapeado.

### A-7. Un archivo corrupto produce 500 y deja un archivo huérfano

- `app/services/importer.py:100`
- `app/services/persist.py:134-138`
- `app/routers/entregas.py:118-122`; también `malla.py:87-91` y `mapa.py:54-58`

`load_workbook()` lanza `BadZipFile`/`InvalidFileException`, que no heredan de
`ValueError`; los routers solo capturan `ValueError`. Además el archivo se
escribe en `uploads/` **antes** de parsearlo. Un `.xlsx` corrupto o un CSV
renombrado devuelve 500 y deja basura en disco sin fila `Entrega`.

Fix: centralizar la conversión a `ValueError("archivo inválido")`, parsear
primero y escribir después (o limpiar en `finally`).

### A-8. La auditoría de acciones registra un actor fijo, no el usuario real

- `app/routers/entregas.py:167`, `qaqc.py:190,212`, `reemplazos.py:45,60,76`,
  `malla.py:89`, `mapa.py:55`
- `app/services/persist.py:119-120` (`actor` no se usa en
  `crear_entrega_pendiente`; ruff `ARG001`)

Ningún router pasa el usuario autenticado; se usan los valores por defecto
`"cargador"`/`"supervision"`. La auditoría no permite saber quién confirmó una
entrega o emitió un dictamen, y la propia plantilla lo muestra literalmente
(“actor supervision”).

Fix: pasar `actor=f"usuario:{request.state.usuario.id}"` desde todos los
routers y usar ese valor también en `crear_entrega_pendiente`.

### A-9. IDs de punto decimales se corrompen (`101.0` → `1010`)

- `app/services/importer.py:67-73`

Verificado en ejecución:

```
101.0 -> 1010
"101.0" -> 1010
"101,5" -> 1015
```

`_id_plano()` elimina el punto y el parser concatena los dígitos. Un Excel que
exporta el ID como número/texto decimal guarda las interpretaciones bajo el
punto 1010; el punto 101 nunca forma terna y el error es invisible.

Fix: normalizar `float` entero a `int` y rechazar IDs ambiguos en lugar de
extraer una secuencia de dígitos.

### A-10. `init_db()` (DDL) se ejecuta en el arranque de producción

- `app/main.py:36`
- `app/core/database.py:3-6`, `43-59`

El docstring dice que `init_db()` es “uso exclusivo de desarrollo y pruebas”,
pero el `lifespan` lo llama siempre. En producción ejecuta
`CREATE EXTENSION`, `create_all()` y `ALTER TABLE` con el usuario de runtime:
puede fallar si no tiene permisos DDL, competir entre réplicas o crear esquema
fuera de Alembic.

Fix: llamar a `init_db()` solo con `environment == "dev"` (o en tests) y dejar
las migraciones al entrypoint.

### A-11. En staging, nginx rompe el CSRF de los navegadores reales

- `deploy/nginx.conf:51` (`proxy_set_header Host $host`)
- `app/auth/csrf.py:127-129`
- `deploy/docker-compose.staging.yml:74-76` y `.env.staging.example:42`

Nginx publica `https://localhost:8443`, pero `$host` no incluye el puerto. La
app compara el `Origin` del navegador (`https://localhost:8443`) con
`request.base_url` (`https://localhost/`) y todos los POST devuelven 403. El
smoke test pasa porque `httpx` no envía `Origin`.

Fix: usar `proxy_set_header Host $http_host;` (o `$host:$server_port`) y añadir
`Origin` a la prueba de humo.

### A-12. Inyección de fórmulas en el XLSX exportado

- `app/routers/exportar.py:56`
- `app/services/persist.py:520-522`

`estrato` y `origen` provienen de la malla cargada por el usuario y se escriben
sin sanear. Verificado: openpyxl guarda `=HYPERLINK("http://evil","x")` como
`data_type='f'`, y Excel la evalúa al abrir el archivo.

Fix: anteponer `'` a todo texto que empiece por `= + - @` (reutilizar la
política de `_celda_csv` de `app/auth/service.py`) o forzar `data_type="s"`.

### A-13. `seed-usuarios` reactiva cuentas y pisa la política MFA

- `app/cli.py:312-318`

La misma rama se aplica a cuentas nuevas y existentes: `is_active = True` y
`mfa_policy = mfa_policy_para_rol(rol)`. Ejecutar la semilla para corregir un
nombre reactiva un administrador desactivado por incidente y degrada políticas
endurecidas. Contradice el runbook, que promete conservar bloqueos y datos
ajenos.

Fix: en filas existentes, no tocar `is_active` ni bajar la política MFA sin una
bandera explícita y auditada.

### A-14. Códigos de un solo uso sin consumo atómico (carreras)

- `app/auth/service.py:576-581` (bloqueo de cuenta)
- `app/auth/service.py:896-909` (códigos de respaldo)
- `app/auth/service.py:918-922` (replay TOTP)
- `app/auth/service.py:1006-1030`, `1351-1377` (intentos MFA)
- `app/router.py:621-626` + `service.py:1587-1638` (token de reset)

Todas son lecturas-modificación-escritura sin `SELECT ... FOR UPDATE` ni
`UPDATE ... WHERE`. Con peticiones concurrentes: el contador de intentos no
llega a 5, el mismo código de respaldo/TOTP se acepta dos veces y el mismo token
de reset puede aplicarse dos veces. El “una sola vez” no está garantizado.

Fix: consumir con `UPDATE ... WHERE <condición> RETURNING` y comprobar
`rowcount`; incrementar contadores en SQL.

---

## Medios

| # | Ubicación | Problema |
|---|-----------|----------|
| M-1 | `persist.py:281-298`, `867-879`, `routers/reemplazos.py:76` | `Reemplazo` y `FichaQAQC` son dos máquinas de estado que no se sincronizan: completar uno deja al otro pendiente para siempre. |
| M-2 | `persist.py:198-219` | `confirmar_entrega` hace `commit()` antes de recalcular cruces y movimientos; si falla el paso 3/4 la entrega queda `aceptada` con datos parciales. |
| M-3 | `persist.py:199-221`, `routers/entregas.py:167-171` | Las observaciones de reserva/malla se calculan después del commit y el router descarta el resultado; nunca se muestran. |
| M-4 | `persist.py:248-257` | Una reserva inexistente se crea automáticamente en la malla (contradice 4.7) y, por M-3, nadie se entera. |
| M-5 | `persist.py:300-330` | `proponer_reserva` no excluye reservas en estado `propuesto`: dos orígenes pueden recibir la misma reserva. |
| M-6 | `persist.py:334-349` | `transicionar_reemplazo` permite `propuesto → completado` saltándose la autorización; un estado corrupto produce `KeyError` (500). |
| M-7 | `analysis.py:38-44` | Los pesos no se validan (no negativos, suma 1, finitos). Pasar hectáreas en vez de proporciones produce exactitud > 1 y áreas imposibles. |
| M-8 | `analysis.py:77-97` | Kappa se calcula con conteos sin ponderar mientras la exactitud global es ponderada por área; ambos estadísticos describen poblaciones distintas. |
| M-9 | `models/__init__.py:113`, `persist.py:189`, `507-530` | La columna geometry está fijada a SRID 4326, pero las inserciones usan `Proyecto.srid`; cambiar el SRID del proyecto rompe la inserción. |
| M-10 | `persist.py:192-196` | La comparación de coordenadas es textual (`ST_AsText` vs f-string): `500000` vs `500000.0` genera falsos avisos. Además es N+1. |
| M-11 | `importer.py:173-176` | Un `ID_EV` no vacío pero desconocido se reasigna a la evaluadora de la hoja en vez de rechazarse. |
| M-12 | `importer.py:135-166`, `100` | Hojas con encabezado no reconocido y fórmulas sin valor en caché se omiten en silencio; una entrega sin datos se acepta con “0 errores”. |
| M-13 | `persist.py:510-517`, `614-621`; `database.py:14` | Con `autoflush=False` y sin restricciones únicas, filas duplicadas en el CSV/XLSX crean registros duplicados y multiplican la matriz. |
| M-14 | `persist.py:166-178`, `routers/entregas.py:112-114` | Se guarda solo la letra seleccionada; un libro combinado A/B/C subido como A descarta B y C. No se valida `letra` ni `tipo` (los esquemas `EntregaCreate` existen pero no se usan). |
| M-15 | `persist.py:448-495` | Las reglas de plausibilidad de la Tabla 6 no se aplican en la ruta de base de datos; no hay columna ni aviso para `IMPROBABLE`. |
| M-16 | `main.py:45-59,75` | No hay manejador de `RequestValidationError`: rutas HTML devuelven 422 JSON ante parámetros inválidos. |
| M-17 | `routers/cruces.py:57-76` | `page` no se acota; `?page=999` muestra “Ningún cruce coincide con los filtros” sin filtros activos. |
| M-18 | `cruces.py:122-124` | `return_to` acepta `/\evil.com`; el navegador lo normaliza a `https://evil.com/` (open redirect al pulsar “Volver”). |
| M-19 | `routers/qaqc.py:155-161`, `schemas/__init__.py:36-41` | `FichaUpdate` no se usa: `dictamen` y `clase_definitiva` aceptan cualquier valor. |
| M-20 | `routers/entregas.py:177-179` | El motivo de rechazo es obligatorio solo en HTML; un POST manual lo omite y se audita “Sin motivo registrado”. |
| M-21 | `templates/dashboard.html:16-17,158,171`, `mapa.html:36-50` | Enlaces visibles para roles que reciben 403 (`admin` → `/qaqc`, `cargador` → `/exportar`). |
| M-22 | `auth/service.py:1494-1552` | El reset de contraseña envía correo de forma síncrona solo para cuentas elegibles: el tiempo de respuesta revela si la cuenta existe. |
| M-23 | `auth/service.py:577-581`, `router.py:155-160` | El bloqueo devuelve un mensaje distinto (“Demasiados intentos…”) y permite enumerar usuarios locales. |
| M-24 | `auth/middleware.py:93-94`, `111-112` | `delete_cookie()` sin `secure=True`: los navegadores rechazan borrar cookies `__Host-` sin `Secure`; la cookie obsoleta sigue enviándose. |
| M-25 | `auth/middleware.py:162-163` vs `service.py:707-720,1363` | El gate de enrolamiento usa `mfa_enrolled_at` y el login usa `MfaTotp.confirmed_at`; una verificación por correo marca `mfa_enrolled_at` y puede saltarse el enrolamiento TOTP. |
| M-26 | `auth/router.py:139` | La rama “ya autenticado → redirigir” es código muerto: el middleware no carga usuario para rutas públicas. |
| M-27 | `auth/providers.py:655` | Las cuentas LDAP auto-provisionadas reciben `mfa_policy="any"` sin importar el rol (incluido admin). |
| M-28 | `core/config.py:293-327`, `scripts/preflight-auth.py:180-187` | `admin_internal_only=false` no es un problema de producción: el preflight solo advierte y puede salir 0 con administración/auditoría expuestas. |
| M-29 | `core/config.py:56` vs `docker-compose.yml:10` | El DSN por defecto apunta a `localhost:5433`, pero compose publica `5432`; el arranque local y los tests de integración fallan sin override. |
| M-30 | `Dockerfile:31-34` | La imagen no copia `scripts/`, aunque la documentación ordena ejecutar `preflight-auth.py` dentro del contenedor. |
| M-31 | `deploy/docker-compose.staging.yml:24-29` | PostgreSQL de staging se publica en `0.0.0.0:5434` con credenciales fijas `eet/eet`. |
| M-32 | `migrations/versions/0001_baseline.py:27-32`, `Dockerfile:45` | La migración base no es idempotente y el contenedor siempre ejecuta `alembic upgrade head`; un volumen creado con `init_db()` sin `alembic_version` falla al arrancar. |
| M-33 | `deploy/docker-compose.staging.yml:53-55` | Staging comparte `../uploads` con desarrollo, contradiciendo “no comparte nada”. |
| M-34 | `scripts/smoke-staging.py:161-165,272-281` | Si faltan credenciales, el login se marca SKIP pero el script sale 0 y afirma que la cookie de sesión está verificada. |
| M-35 | `scripts/preflight-auth.py:246-266` | Si el esquema no existe, el preflight continúa y lanza `ProgrammingError` en vez de reportar FAIL. |
| M-36 | `app/cli.py:729-730` | `limpiar-sesiones --dias 0` borra todas las sesiones, incluidas las vivas (el docstring dice lo contrario). |
| M-37 | `app/cli.py:320-325` | `seed-usuarios --con-password` resetea las seis cuentas y no revoca sesiones, pese a que el runbook exige revocación. |
| M-38 | `app/cli.py:488-506` | `re-cifrar` marca como “actual” cualquier fila con el `key_id` vigente sin descifrarla; un blob corrupto pasa como válido. |
| M-39 | `app/seed_data.py:71` | La semilla crea un administrador activo con un Gmail personal (`viento100@gmail.com`). |
| M-40 | `app/routers/dashboard.py:24,31-40` | Los KPIs del asistente se calculan sobre las últimas 20 entregas y se muestran como totales. |

---

## Bajos

| # | Ubicación | Problema |
|---|-----------|----------|
| B-1 | `services/store.py:7` | Import `ResultadoSeccion5` sin usar (ruff F401). |
| B-2 | `services/persist.py:869`, `cli.py:142` | `datetime.utcnow()` obsoleto desde Python 3.12; existe `utcnow()` propio. |
| B-3 | `routers/qaqc.py:108`, `reemplazos.py:35` | `datetime.now()` sin zona horaria frente a `now()` de PostgreSQL; los “días” pueden desviarse. |
| B-4 | `routers/entregas.py:122,169,181`; `malla.py:91`; `mapa.py:58`; `reemplazos.py:48,62,78`; `persist.py:576` | `raise HTTPException` dentro de `except` sin `from` (ruff B904). |
| B-5 | `services/persist.py:50,373-374,382,539,585-586,852` | Imports dentro de funciones (ruff PLC0415). |
| B-6 | `services/store.py:145-191` | La ruta legacy `Store` transpone la matriz (pasa `(consenso, mapa)` y usa la evaluadora A como mapa). Solo la usan tests, pero contradice el diseño. |
| B-7 | `templates/entrega_preview.html:31` | Expone `entrega.archivo_path` (ruta del servidor) en el atributo `title`. |
| B-8 | `templates/entregas.html:18-19,202-204`; `malla.html:25-26,161-166`; `reemplazos.html:28-29,188-193` | CTAs de estado vacío apuntan a formularios que no se renderizan para roles de solo lectura. |
| B-9 | `routers/qaqc.py:210-219` | Un `ficha_id` inexistente redirige con “transición inválida” en vez de 404. |
| B-10 | `routers/entregas.py:129`, `qaqc.py:137,181`, `reemplazos.py:76` | Búsquedas por PK sin filtrar `proyecto_id`; IDOR latente en un despliegue multiproyecto. |
| B-11 | `models/__init__.py:323`, migraciones | `MfaTotp.key_id` y otros defaults son solo del ORM (`default=`) sin `server_default`; inserts crudos fallan. |
| B-12 | `tests/test_cli_operacion.py:230-237,279-286` | Pruebas de integración no aisladas: cuentan filas globales y pueden tocar datos reales de desarrollo. |
| B-13 | `static/js/app.js:165-171`, `templates/qaqc_detalle.html:483-487` | Cancelar un `confirm()` deja el botón deshabilitado y con “Guardando…” 15 s. |
| B-14 | `routers/entregas.py:112-114`, `schemas/__init__.py:7-10` | `EntregaCreate` no se usa: `asistente_letra`, `semana` y `tipo` no se validan en servidor. |
| B-15 | importer `_parsear_movimiento` | Solo acepta `R<n>, [NBT]` y `A<n>` exactos; variantes (`R-558, N`, `R558 N`) se pierden como texto libre. |
| B-16 | `persist.py:546,556` | El parser del mapa XLSX solo lee `libro.active` y es sensible a mayúsculas en `A2024`. |
| B-17 | `persist.py:570-572,614-625` | `cargar_mapa` no elimina clases obsoletas cuando el mapa corregido omite un punto/año. |
| B-18 | `persist.py:192` | `PuntoMalla` con `geom NULL` nunca se rellena con coordenadas posteriores. |
| B-19 | `persist.py:685-688` | “Evaluaciones aceptadas” cuenta todas las versiones aceptadas; se infla tras correcciones. |
| B-20 | `domain/classes.py:45-57` | `normalizar_etiqueta` deja pasar texto desconocido y `es_valido` no se usa; el motor acepta “Zona ignota” como clase. |

---

## Calidad de código y pruebas

- `ruff` reporta 145 avisos; la mayoría son estilo (orden de imports,
  `Depends` en defaults, complejidad). Los relevantes para correctitud ya están
  listados arriba (F401, B904, DTZ003, ARG001, PLC0415).
- La suite por defecto pasa (243 pruebas). Esto demuestra que los fallos
  principales **no están cubiertos**:
  - no hay pruebas de `persist.confirmar_entrega`/`recalcular_cruce`;
  - el importador no prueba IDs flotantes, fórmulas sin caché ni `ID_EV`
    inválido;
  - no hay pruebas de carreras (MFA, reset, lockout);
  - `test_analysis.py` usa muestras perfectamente diagonales, que pasan con la
    matriz traspuesta y no detectan el problema de `Store`;
  - no hay pruebas de páginas fuera de rango, uploads corruptos ni
    `return_to` con barra invertida.
- Las 273 pruebas de integración se ejecutaron después en WSL 2 + Docker:
  **273 passed**.

## Orden de corrección recomendado

1. **Integridad de datos**: C-1, C-2, A-1, A-2, A-3, A-4, A-9.
2. **Seguridad de autenticación**: A-5, A-6, A-14, M-22, M-23, M-24.
3. **Auditoría y trazabilidad**: A-8, M-1, M-2.
4. **Robustez de entrada**: A-7, M-11, M-12, M-13, M-14, M-19, M-20.
5. **Despliegue**: A-10, A-11, M-28, M-29, M-30, M-31, M-32.
6. **Exportaciones**: A-12, M-15, M-7, M-8.

## Correcciones aplicadas (2026-11-03)

| ID | Corrección | Archivos |
|----|------------|----------|
| C-1 | `confirmar_entrega()` rechaza archivos con errores y el router vuelve a la vista previa con un flash | `services/persist.py`, `routers/entregas.py`, `web.py` |
| C-2 | Las celdas vacías se persisten como ausencia (clase NULL), de modo que una corrección borra el valor anterior | `services/persist.py` |
| A-1 | La última interpretación se ordena por `recibida_en`/`id`, no por la versión semanal | `services/persist.py` |
| A-2 | `recalcular_cruce()` preserva el consolidado y la regla de una ficha cerrada | `services/persist.py` |
| A-3 | Regla única `requiere_ficha_qaqc()`: solo discrepancia absoluta o SC; `Store` se alinea con el protocolo | `domain/qaqc.py`, `services/persist.py`, `services/store.py` |
| A-4 | `clase_definitiva` se valida 1–9 en router, servicio y conversión dominio/BD | `services/persist.py`, `routers/qaqc.py` |
| A-5 | Los topes de envío por cuenta/IP se aplican dentro de `emitir_codigo_email()` | `auth/service.py` |
| A-6 | Cambiar el rol recalcula `mfa_policy`, marca `role_source="manual"` y la provisión/sincronización LDAP deriva la política del rol | `auth/service.py`, `auth/providers.py` |

Pruebas nuevas:

- `tests/test_persist_entregas.py`: C-1, C-2, A-1, A-2, A-3 y A-4 sobre
  SQLite en memoria con el subconjunto de tablas necesario (sin PostGIS).
- `tests/test_auth_regresiones.py`: A-5 y A-6 sin PostgreSQL.
- `tests/test_qaqc.py`: regla `requiere_ficha_qaqc()` para unanimidad,
  mayoría, incompleto, discrepancia y SC.

Resultado final (WSL 2 + Docker): **290 pruebas no-integration + 273
integration = 563 passed**. La corrección C-2 no necesitó migración:
`interpretaciones.clase` ya era `NULL`. La Fase 0 sí agregó la migración `0004`
para restricciones únicas y CHECK 1–9.

Residual conocido de A-5: el envío de correos ya está topado, pero un atacante
con la contraseña todavía puede crear challenges nuevos en `/auth/login` y, con
`crear_desafio()`, invalidar el challenge anterior del titular (DoS de la cookie
MFA, sin coste de correo). Se recomienda un tope de challenges por cuenta/IP o
dejar de invalidar los anteriores y confiar en su vencimiento de 5 minutos.

## Análisis de errores observados en la puesta en marcha

### E-1. `ERR_CONNECTION_REFUSED` en `localhost:8000`

- **Evidencia:** `wsl -l -v` mostraba `Ubuntu Stopped`; al iniciar WSL,
  `docker ps -a` mostró los contenedores con `ExitCode 0/143` y el arranque
  automático los recuperó.
- **Causa:** se apagó la VM de WSL (inactividad/`wsl --shutdown`), no la app. Sin
  VM no hay proceso escuchando el puerto 8000 en Windows.
- **Fix:** `C:\Users\Pedrito\.wslconfig` con `vmIdleTimeout=3600000`,
  `restart: unless-stopped`, `scripts/start-local.ps1` y las tareas
  **EET WSL Keepalive** (sesión WSL persistente) + **EET Local Watchdog**
  (revisa `/salud` cada 5 min). Probado apagando WSL: el stack volvió y se
  mantuvo estable.

### E-2. 7 fallos y 89 errores en la primera corrida de integración

- **89 errores:** `test_a11y_static.py` fallaba con
  `FileNotFoundError: /app/uploads/seed-entrega.xlsx`. El seed guardaba una ruta
  absoluta del contenedor y las pruebas corrían en el host WSL. Se corrigió a
  ruta relativa `uploads/seed-entrega.xlsx`, válida en ambos contextos.
- **7 fallos:** `test_web_routes.py` asume `/cruces/81/2008`, `/qaqc/1`,
  `/entregas/1/preview` y más de 50 filas para paginar; una base recién migrada
  no las tiene. Se sembraron datos demo idempotentes.
- **Resultado:** 273/273 integración. **No era un fallo de la app**, sino falta
  de aislamiento. Se corrigió con el fixture `datos_web` de `tests/conftest.py`,
  que crea y limpia el cruce 81/2008, la ficha 1, la entrega 1 y las filas de
  paginación. El seed queda solo para la demo visual.

### E-3. El login demo devolvía 200 en vez de 303

- **Causa:** `deploy/.env.local` se sincronizaba desde Windows con CRLF. El
  `\r` quedaba al final de `EET_DEMO_*_PASSWORD`, el seed hasheaba la contraseña
  con `\r` y el formulario enviaba la contraseña sin él.
- **Fix:** `sed -i 's/\r$//' deploy/.env.local` dentro de `test-local.sh`.
  Verificado con `login_smoke.py`: 303 y sesión válida.

### E-4. `GET /health` devuelve 401

- **Evidencia:** 2 peticiones en 4 h desde `172.18.0.1` (host Windows), sin
  ruta `/health` en la app. El `AuthGate` protege rutas desconocidas y responde
  401. No hubo ningún 500.
- **Impacto:** ruido si algún monitor externo espera 200. No afectaba a
  usuarios. **Resuelto:** `/health` es ahora un alias público de `/salud` en
  `app/main.py` y `app/auth/middleware.py`; responde 200 con y sin sesión.

### E-5. `FATAL: terminating connection due to administrator command`

- **Evidencia:** 17 líneas `FATAL` en PostgreSQL, todas durante paradas de
  Docker/WSL; 0 `PANIC`, 0 corrupción. El directorio de datos se recuperó
  limpiamente en cada arranque.
- **Conclusión:** mensajes normales de apagado, no errores de datos.

### E-6. `docker compose config` mostró secretos en la salida

- **Evidencia:** el comando imprimió `EET_SECRET_KEY`, `EET_AUTH_PEPPER` y las
  contraseñas demo.
- **Impacto:** son secretos de desarrollo local; el riesgo es bajo, pero deben
  rotarse si el entorno se comparte o el log se envía a terceros.
- **Resuelto:** se rotaron `EET_SECRET_KEY` (nueva `v2`) y `EET_AUTH_PEPPER`.
  Se configuró `EET_FERNET_KEYS="v2:<nueva>,v1:<anterior>"`, de modo que los
  correos/TOTP antiguos siguen descifrándose con `v1`. `mfa_totp` estaba vacío,
  así que no hay secretos TOTP huérfanos. Para validar sin imprimir valores use
  `docker compose config --quiet`.

### E-7. `test-local.ps1` devolvió exit 1 en una corrida

- **Causa:** el comando exterior `... 2>&1 | Tee-Object ...` convirtió los
  avisos de pytest en `NativeCommandError` de PowerShell; el script imprimía
  “Todo OK” y la ejecución directa devolvía 0.
- **Fix:** se añadió `-p no:warnings` a pytest y se recomienda no canalizar
  stderr a `Tee-Object`. La corrida final devuelve 0.

### E-8. Advertencias no bloqueantes

- `npm ci` reportó 5 vulnerabilidades moderadas en el toolchain de build
  (Tailwind/Playwright); no se copian al runtime.
- `pytest` muestra avisos de `datetime.utcnow()` obsoleto y de `httpx`/
  Starlette; no afectan a los resultados.
- `ruff`: quedan avisos de estilo, pero sin errores `F`/`E9` en `app/`.

### E-9. La app no declaraba healthcheck en Compose

- **Resuelto:** `docker-compose.yml` declara un healthcheck para `app` contra
  `/salud` (cada 10 s, 12 reintentos, 40 s de gracia). `docker compose ps`
  muestra `(healthy)` y `docker compose up --wait` espera a que la app responda.
- `scripts/start-local.ps1` además consulta `/salud` hasta 60 s y el watchdog
  revisa cada 5 minutos.

## Fase 0 aplicada (2026-11-03)

Además de las correcciones C/A anteriores, se ejecutó la Fase 0 del plan de
mejoramiento:

- **Migración `0004`** con 7 restricciones `UNIQUE` y 5 `CHECK` de clases 1–9;
  también declaradas en `app/models/__init__.py` para que `init_db`/tests y
  Alembic coincidan. Cadena `upgrade → downgrade → upgrade` verificada.
- **Confirmación transaccional** en `persist.confirmar_entrega`: un solo
  `commit`; interpretaciones, malla, cruces y movimientos se revierten juntos.
  Las observaciones de reserva ahora se guardan en `detalle_validacion`.
- **Importador**: `abrir_libro()` normaliza archivos corruptos a
  `ValueError` (400 HTML); se parsea antes de escribir y se limpia el archivo si
  falla la fila. `preview` también responde 400 ante un archivo ilegible.
- **`init_db()` solo en `dev`**: staging/producción delegan en Alembic.
- **CI** `.github/workflows/ci.yml`: lint, unitarias, integración con PostGIS y
  build de imagen.
- **Backup** `scripts/backup.ps1` / `backup-local.sh` con verificación de
  restauración; probado con 8 usuarios y 60 cruces.
- **Auditoría previa** `scripts/audit_duplicates.py`: sin duplicados ni valores
  fuera de rango.

Resultado: **290 pruebas unitarias + 273 de integración = 563 passed**.
Queda pendiente la idempotencia de la migración baseline `0001` (M-32) y los
hallazgos P1 de la Fase 1.

### Fase 1 iniciada (2026-11-03)

- **Historial de cruces** (`resultados_cruce_historial`, migración `0005`):
  cada recálculo guarda A/B/C, resultado, clase consolidada, regla, actor y
  entrega. Verificado en SQLite y PostgreSQL.
- **Pesos y kappa**: validación de proporciones (suma 1, finitas, no negativas)
  y kappa ponderado por área. Se elimina el fallback silencioso a uniforme.
- **Acciones de usuarios en modal**: `<dialog>` accesible con fallback sin JS;
  validado en Edge (4 formularios, Escape y cierre).
- Suite: **290 + 273 = 563 pruebas**.

## Despliegue validado (WSL 2 + Docker)

- Ubuntu 26.04 LTS (resolute), WSL 3.0.3, kernel 6.18.54.1.
- Docker Engine 29.1.3 y Docker Compose 2.40.3 instalados dentro de Ubuntu.
- Proyecto copiado a `/opt/sistema_modelo_mapas` (ext4, no `/mnt/c`).
- Contenedores: `eet-app` (puerto 8000) y `eet-mapa-aep`
  (postgis/postgis:16-3.4, puerto 5432), ambos con
  `restart: unless-stopped`.
- Migraciones Alembic 0001 → 0003 aplicadas; `/salud` y las 8 vistas
  principales responden 200, igual que `/cruces/81/2008`, `/qaqc/1` y
  `/entregas/1/preview`.
- Datos demo sembrados para las pruebas que asumen contenido: 60 cruces,
  56 fichas y 1 entrega con `.xlsx` válido.
- Autenticación activada con `deploy/.env.local` (secretos aleatorios, cookies
  no `Secure` solo por HTTP local). Cuentas demo `demo.admin` y
  `demo.consulta`; sus contraseñas se entregaron en la sesión y no se guardan en
  el repositorio. Ambas usan `mfa_policy=disabled` **solo para la demo**.
- Verificado el login real: `demo.admin` → `/auth/usuarios` 200;
  `demo.consulta` → `/auth/usuarios` 403.
- `wsl --shutdown` + rearranque: los contenedores volvieron solos por
  `restart: unless-stopped`; **EET WSL Keepalive** mantiene la VM viva y
  **EET Local Watchdog** revisa `/salud` cada 5 minutos. Probado apagando WSL:
  el stack volvió y se mantuvo disponible (log “stack levantado”).
- `app` tiene healthcheck contra `/salud`; `docker compose ps` muestra
  `(healthy)`.
- `/health` responde 200 como alias público de `/salud`.
- Secretos rotados: `EET_SECRET_KEY` v2 + `EET_AUTH_PEPPER` nuevos, con la
  clave v1 anterior en `EET_FERNET_KEYS` para descifrar correos antiguos.
- `pytest -m integration`: **273 passed**.
- `pytest -q` (sin integración): **290 passed**.
- Automatización local: `scripts/test-local.ps1` (Windows) +
  `scripts/test-local.sh` (WSL) levantan el stack, siembran
  `scripts/seed_demo_data.py` y ejecutan ambas suites más el smoke de login
  (`scripts/login_smoke.py`). Uso documentado en `scripts/README.md`.

Advertencia: las cuentas demo y `EET_COOKIE_SECURE=false` son válidas solo para
este entorno local. En producción hay que usar HTTPS, cookies `Secure` y exigir
TOTP a `admin`/`supervision`.

Nota de aislamiento: la primera corrida de integración dio 7 fallos y 89
errores porque `test_web_routes.py` y `test_a11y_static.py` asumen filas demo
(`/cruces/81/2008`, `/qaqc/1`, `/entregas/1/preview` y >50 registros para
paginar) que una base recién migrada no tiene. Tras sembrarlas, todo pasa. Lo
recomendable es que esas pruebas creen sus propios datos (fixture) en lugar de
depender de una base de desarrollo con contenido.

## Nota sobre skills

Las skills disponibles en este entorno son `opencode` (documentación de
OpenCode) y `report` (reporte de incidencias de OpenCode); ninguna aplica a este
repositorio. Por eso el trabajo se apoyó en subagentes especializados por área
en lugar de en esas skills.

## Correcciones aplicadas (2026-11-06)

| ID | Corrección |
|---|---|
| A-5 residual | `crear_desafio` ya no invalida challenges vivos; purga los vencidos. Un login nuevo no deja fuera al titular. |
| A-11 staging | `deploy/nginx.conf` usa `Host $http_host` y `X-Forwarded-Host $http_host`, conservando el puerto 8443. |
| A-12 | `app/core/excel.py` centraliza `celda_segura`; el XLSX consolidado y el CSV de auditoría prefijan fórmulas. |
| A-13 / M-37 | `seed-usuarios` conserva `is_active` salvo `--reactivar`; `--con-password` revoca sesiones y tokens. |
| A-14 | Bloqueo `FOR UPDATE` del challenge MFA y consumo atómico de códigos de respaldo y pasos TOTP. |
| M-16 | Manejador de `RequestValidationError`: HTML para formularios y JSON para integraciones. |
| M-17 | `listar_cruces` ajusta `page` a la última página y devuelve la página efectiva. |
| M-24 | `delete_cookie` conserva `secure`, `httponly` y `samesite` al revocar cookies. |
| M-29 | DSN por defecto alineado al compose de desarrollo (`localhost:5432`). |
| M-31 / M-33 | Staging publica PostgreSQL solo en `127.0.0.1:5434` y usa un volumen de uploads propio. |
| M-32 | Migraciones `0001`–`0005` idempotentes para bases creadas con `init_db()` sin `alembic_version`. Verificado en base temporal. |
| M-40 | El panel del asistente calcula totales/pendientes/aceptadas con agregados, no con las últimas 20 filas. |
| B-4 | Sin `B904` pendientes; `raise ... from` en los dos casos restantes. |

Validación:

```text
307 unitarias + 283 integración = 590 passed
ruff F,E9,B904                  = OK
alembic upgrade desde init_db   = 0005 (head), check limpio
nginx staging/prod/external     = nginx -t OK
docker compose staging          = config OK
```

Los cambios locales del lanzador oculto del watchdog (`scripts/run-hidden.vbs`,
`register-autostart.ps1`) no forman parte de esta corrección.
