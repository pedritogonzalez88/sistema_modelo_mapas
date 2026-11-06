# Propuesta de mejoramiento — Validador EET

**Fecha:** 2026-11-03
**Sistema:** Validador de Exactitud Temática (EET) — INGEI/BTR2 Paraguay
**Stack actual:** FastAPI + SQLAlchemy 2 + PostgreSQL/PostGIS + Jinja2 + Tailwind + Alembic
**Estado:** desplegado en WSL 2 + Docker; 306 pruebas unitarias y 278 de integración pasan.

---

## 1. Resumen ejecutivo

El sistema tiene una base sólida: monolito modular en FastAPI, dominio EET bien
delimitado, uso correcto de PostGIS, autenticación completa (sesiones, CSRF, TOTP,
correo, LDAP), auditoría, plantillas accesibles y una suite de pruebas amplia.
No requiere una reescritura.

La deuda principal no está en “qué hace”, sino en **cómo garantiza que lo haga
siempre igual y de forma verificable**:

1. **Consistencia transaccional y concurrencia:** hay confirmaciones que hacen
   `commit` antes de recalcular cruces y operaciones de un solo uso sin bloqueo.
2. **Modelo de datos:** faltan restricciones únicas y de rango; el versionado de
   entregas no distingue “semana” de “cobertura” y no hay historial de cruces.
3. **Duplicación de reglas:** `Store` (en memoria) y `persist` implementan dos
   caminos; `Reemplazo` y `FichaQAQC` son dos máquinas de estado que no se
   sincronizan.
4. **Importación frágil:** parser acoplado a la persistencia, errores silenciosos,
   IDs ambiguos, fórmulas sin caché y archivos grandes en memoria.
5. **Operación:** secretos en archivo de entorno, rate limiting en proceso, sin
   CI/CD, sin observabilidad, sin backups automatizados ni migraciones
   idempotentes en producción.

La propuesta es evolucionar a un **monolito modular con casos de uso explícitos,
transacciones únicas, restricciones en base de datos, pruebas herméticas y una
base operativa mínima** antes de escalar. Con las decisiones de alcance
confirmadas (un proyecto, un servidor, una instancia), la inversión estimada
baja a **8–12 semanas** con 1 backend + 0,5 QA/DevOps + 0,5 UX, o **6–8
semanas** con 2 backend.

No se invertirá en multi-proyecto, alta disponibilidad, *rate limiting*
distribuido ni almacenamiento objeto en esta etapa; el diseño deja las costuras
para incorporarlos después sin rehacer el núcleo.

---

## 2. Alcance y estado de partida

### 2.1 Lo que ya está resuelto

- Correcciones críticas de integridad: confirmación bloqueada con errores,
  celdas vacías como ausencia, orden por entrega más reciente, preservación de
  fichas cerradas, validación 1–9.
- Autenticación: topes de correo en login, política MFA por rol, provisión LDAP.
- Operación local: WSL 2 + Docker, healthcheck, `/health`, watchdog y keepalive.
- Seguridad inmediata: secretos rotados (`v2` vigente, `v1` para descifrar).
- Pruebas: rutas y accesibilidad autocontenidas mediante `datos_web`.

### 2.2 Lo que queda pendiente (de la auditoría)

- Confirmación no atómica y observaciones de malla descartadas.
- Duplicación `Reemplazo`/`FichaQAQC`, reservas ad-hoc y doble asignación.
- Importador: 500 ante archivos corruptos, IDs decimales (`101.0 → 1010`),
  fórmulas sin caché, hojas ignoradas.
- Análisis: pesos sin validar, kappa sin ponderar, SRID fijo a 4326,
  plausibilidad no persistida.
- Seguridad: `init_db()` en producción, nginx con `Host $host` (CSRF en staging),
  export XLSX con fórmulas, actor de auditoría fijo.
- Operación: migraciones no idempotentes, imagen root, sin backups ni CI/CD.
- Pruebas: autenticación e infraestructura aún comparten base de desarrollo;
  no hay pruebas de concurrencia, carga ni e2e.

### 2.3 Decisiones de alcance confirmadas (2026-11-03)

| Tema | Decisión | Implicancia en la propuesta |
|---|---|---|
| Proyecto | **Uno solo** | Se conserva `proyecto_id` por compatibilidad, pero no se invierte en multi-proyecto ni multi-tenant. Se elimina del roadmap la fase de multiproyecto. |
| Hosting | **Mismo servidor** | PostgreSQL/PostGIS, app y archivos en el mismo host. Backups a disco/NAS externo. Sin nube gestionada ni alta disponibilidad por ahora. |
| Instancias | **Una sola** (por ahora) | El rate limiting en proceso es suficiente; se deja una interfaz `RateLimiter` para Redis si se escala. Sin locks distribuidos. |
| LDAP | **Sí, más adelante** | Auth local primero. `app/auth/providers.py` ya abstrae el proveedor; se integra en una fase posterior sin bloquear las P0. |
| Duplicados | **Ventana solo si existen** | Auditoría previa; si hay duplicados, backup + ventana de consolidación; si no, las restricciones se aplican online. |

Con estas decisiones, el objetivo deja de ser “preparar para escala” y pasa a
ser **“hacer confiable el despliegue actual de un solo proyecto y un solo host”**.

---

## 3. Diagnóstico del diseño actual

### 3.1 Fortalezas

| Área | Fortaleza |
|---|---|
| Dominio | Reglas EET separadas en `app/domain` (`cross_engine`, `qaqc`, `transition_rules`). |
| Datos | PostgreSQL/PostGIS, modelo largo de interpretaciones, Alembic. |
| Web | Server-rendered con Jinja2, componentes reutilizables, accesibilidad auditada. |
| Seguridad | Sesiones con hash, CSRF, TOTP, códigos de respaldo, LDAP, auditoría. |
| Calidad | 605 pruebas, ~82% de cobertura, ruff limpio, import-linter y mypy en dominio/core/services. |
| Operación local | Docker Compose, healthcheck, watchdog, arranque reproducible. |

### 3.2 Debilidades y riesgos

| Área | Evidencia | Impacto | Prioridad |
|---|---|---|---|
| Transacciones | `persist.confirmar_entrega` hace `commit` antes de recalcular cruces y movimientos | Entregas aceptadas con datos parciales si falla un paso | P0 |
| Concurrencia | Lockout, intentos MFA, códigos de respaldo y tokens de reset son read-modify-write | Topes evadibles; un código puede usarse dos veces | P0 |
| Modelo | Sin `UniqueConstraint` para `(proyecto, punto, año, letra)` ni claves naturales | Duplicados, matrices infladas, reservas repetidas | P0 |
| Versionado | `version` se numera por semana; no hay tabla de cobertura por entrega | Correcciones y celdas vacías dependen de la fecha de recepción | P1 |
| Estado | `Reemplazo` y `FichaQAQC` no se sincronizan | Fichas pendientes eternas; consolidado no refleja el reemplazo | P1 |
| Importación | Parser y persistencia acoplados; errores no bloqueantes; IDs decimales | Pérdida silenciosa de datos, 500 ante archivo corrupto | P0 |
| Estadística | Pesos sin validar; kappa sin ponderar; SRID fijo | Exactitud/áreas imposibles; resultados no reproducibles | P1 |
| Pruebas | Suites de auth/integración sobre base compartida; sin concurrencia | Fallos por datos previos y regresiones no detectadas | P1 |
| Seguridad | Secretos en `.env.local`; rate limit en memoria; `init_db` en prod | Rotación manual; límites por worker; DDL no controlado | P1 |
| Operación | Sin CI/CD, métricas, trazas, backups ni restore drills | Incidentes detectados tarde; RPO/RTO desconocidos | P1 |
| UX | Enlaces visibles para roles sin permiso; sin acciones masivas | Fricción y 403 evitables | P2 |

### 3.3 Deuda técnica priorizada

- **P0:** transacción única de confirmación, restricciones únicas/CHECK,
  importador que no pierda datos, concurrencia de códigos, pruebas herméticas.
- **P1:** cobertura de entregas, historial de cruces, máquina de estados única,
  validación estadística, observabilidad, backups, CI/CD, secretos.
- **P2:** UX de roles, acciones masivas, dashboards, internacionalización.

---

## 4. Arquitectura objetivo

### 4.1 Principios

1. **Monolito modular**, no microservicios: el volumen y el equipo no justifican
   distribuirlo; sí separar responsabilidades.
2. **La base de datos es la fuente de verdad**: reglas críticas también como
   restricciones (`UNIQUE`, `CHECK`, `FK`, `ON CONFLICT`).
3. **Un caso de uso = una transacción**: validar, escribir, recalcular, auditar
   y publicar; commit al final.
4. **Idempotencia explícita**: huella SHA-256, claves naturales y `upsert`.
5. **Historial inmutable** para cruces, fichas y auditoría; el estado actual es
   una vista materializada.
6. **Pruebas herméticas**: cada test crea y revierte su propio estado.
7. **Seguridad y observabilidad por defecto**: sin secretos en repo, logs
   estructurados, métricas y trazas.
8. **Entrega progresiva**: primero integridad y pruebas; después escala y UX.

### 4.2 Módulos objetivo

```text
app/
  domain/           reglas puras: clases, tabla7, plausibilidad, estados
  application/      casos de uso y transacciones (confirmar, recalcular, resolver)
  infrastructure/   SQLAlchemy, PostGIS, correo, LDAP, almacenamiento, outbox
  api/              routers FastAPI (entrada/salida, sin reglas)
  web/              plantillas, componentes, formularios
  auth/             identidad, sesiones, MFA, permisos, auditoría
  analysis/         estimadores, validación de pesos, exportación
```

Regla: `api` y `web` no acceden directamente a SQLAlchemy; llaman a
`application`. `domain` no importa FastAPI ni SQLAlchemy.

### 4.3 Cambios al modelo de datos

1. **Cobertura de entrega** (`entrega_alcance`): `(entrega_id, punto_id, anio,
   letra, estado)` para distinguir “no opina” de “dijo vacío”. Complementa los
   tombstones actuales y permite auditoría sin depender de la fecha.
2. **Restricciones**:
   - `UNIQUE (entrega_id, punto_id, anio, asistente_letra)` en interpretaciones.
   - `UNIQUE (proyecto_id, punto_id, anio)` en `resultados_cruce` vigente.
   - `UNIQUE (proyecto_id, punto_origen, punto_reserva)` en reemplazos.
   - `CHECK (clase BETWEEN 1 AND 9)` y equivalentes en `clase_consolidada` y
     `clase_definitiva`.
   - `UNIQUE (proyecto_id, punto_id, anio)` en malla y clases del mapa.
3. **Historial de cruces** (`resultado_cruce_historial`): append-only con
   `regla_version`, `parametros`, `actor`, `entrega_id` y vigencia. La tabla
   actual queda como “último vigente”.
4. **Expediente único**: `fichas_qaqc` como entidad de workflow y
   `reemplazos.ficha_id` como acción; una sola máquina de estados. El cierre por
   reemplazo actualiza ficha, cruce y mapa en la misma transacción.
5. **Outbox**: `eventos_outbox` para correo, notificaciones y futuras
   integraciones; reintentos y estado. Reutilizar `email_outbox` o migrar.
6. **Auditoría inmutable**: `actor_id` FK, `request_id`, `hash_anterior`/`hash`
   para detectar alteraciones; export firmado.
7. **Almacenamiento de archivos**: objeto (S3/MinIO) con versión y retención;
   la base guarda URI y SHA-256, no rutas locales.

### 4.4 Flujo transaccional de confirmación

```text
BEGIN
  bloquear entrega (SELECT ... FOR UPDATE)
  validar archivo y errores (si hay errores -> rollback, queda pendiente)
  insertar interpretaciones y ausencias (upsert)
  recalcular solo cruces afectados
  procesar movimientos de reserva
  actualizar entrega a aceptada
  escribir auditoría + outbox
COMMIT
```

Si falla cualquier paso, no queda una entrega “aceptada” a medias. Un reintento
con la misma huella SHA-256 es idempotente.

### 4.5 Flujo de importación

```text
subir archivo -> guardar original + SHA-256
  -> parsear en seco (dry-run) con informe de errores/advertencias
  -> mostrar vista previa (sin tocar cruces)
  -> confirmar (transacción única)
  -> si es grande, job en background + estado/progreso
```

El parser no persiste; devuelve un `ImportReport` con filas, celdas, errores y
advertencias. El caso de uso decide.

---

## 5. Plan de mejoras por frente

### F1. Integridad y dominio (P0, 2 semanas)

- Transacción única en confirmación; eliminar commits intermedios.
- **Auditoría previa de duplicados** (`scripts/audit_duplicates.py`). Si existen,
  backup + ventana de consolidación; si no, aplicar restricciones online
  (`CREATE UNIQUE INDEX CONCURRENTLY`, `CHECK ... NOT VALID` + `VALIDATE`).
- Restricciones únicas/CHECK para interpretaciones, cruces, malla, mapa,
  reemplazos y clases 1–9.
- `upsert`/`ON CONFLICT` para malla, mapa, interpretaciones y cruces.
- Unificar `Reemplazo` y `FichaQAQC` en un solo workflow (una sola transacción
  actualiza ficha, cruce y mapa).
- Persistir plausibilidad, regla y parámetros; historial de cruces.
- Bloqueo optimista/pesimista en operaciones de un solo uso (MFA, reset, lockout).
- **Entregable:** suite de integridad + migración `0004` + pruebas de concurrencia.
- **Nota:** al ser un solo proyecto, no se agrega complejidad multi-tenant;
  `proyecto_id` se conserva solo por compatibilidad.

### F2. Importación robusta (P0/P1, 2 semanas)

- Separar `parser` de `persistencia`; `ImportReport` y dry-run.
- Validar plantilla, años, IDs (enteros/decimales ambiguos), fórmulas sin caché,
  hojas desconocidas y duplicados.
- Rechazar archivos corruptos con 400 HTML, sin 500 ni archivos huérfanos.
- Límite de filas/tamaño, lectura `read_only`, procesamiento en background.
- Política explícita de duplicados (fallar o “último gana” auditado).
- **Entregable:** importador probado con property-based testing (Hypothesis).

### F3. Estadística y trazabilidad (P1, 2 semanas)

- Validar pesos: finitos, no negativos, suma 1 ± tolerancia; error explícito.
- Kappa ponderado por área y coherencia con exactitud global.
- Intervalos de confianza y áreas con denominadores documentados.
- SRID: transformar a 4326 con `ST_Transform` o parametrizar la columna.
- Guardar hash de la muestra y versión de reglas para reproducibilidad.
- Exportar XLSX con metadatos y sin inyección de fórmulas.
- **Entregable:** informe de análisis reproducible + pruebas numéricas.

### F4. Seguridad (P1, 2 semanas)

- Aplicar política MFA en login para roles críticos; break-glass auditado.
- **Rate limiting en proceso** (suficiente para una instancia). Dejar una
  interfaz `RateLimiter` y documentar la migración a Redis cuando se escale.
- Secretos en Vault/SOPS/age; rotación documentada; `EET_FERNET_KEYS`.
- Corregir `nginx` (`Host $http_host`) y probar CSRF con `Origin` real.
- `init_db()` solo en dev/test; producción solo Alembic.
- Auditoría con actor real, `request_id` y hash encadenado.
- Escaneo de dependencias (`pip-audit`, `npm audit`, Trivy), SAST y secret
  scanning en CI.
- **LDAP:** se mantiene la abstracción actual; la integración real se planifica
  para una fase posterior, no bloquea esta etapa.
- **Entregable:** checklist de seguridad + pruebas de abuso.

### F5. Pruebas (P0/P1, 2–3 semanas)

- Base de datos por test: transacción con rollback o template DB/testcontainers.
- Factories y datos deterministas; eliminar dependencia de base de desarrollo.
- Pruebas de concurrencia para MFA, reset, lockout y confirmación.
- Property-based tests del importador; contract tests de plantillas/rutas.
- E2E Playwright de los flujos críticos; axe en CI.
- Carga con k6 para subida y recálculo.
- **Entregable:** pipeline `lint + typecheck + unit + integration + e2e + security`.

### F6. Observabilidad y operación (P1, 2–3 semanas)

- Logs JSON con `request_id`, usuario, entrega y duración; sin secretos.
- Métricas: latencia/errores HTTP, pool DB, cola, importación, recálculo,
  correos fallidos.
- `/health` (liveness) y `/ready` (DB/cola); alertas.
- Backups PostGIS (pg_dump/WAL), retención y restore drill trimestral.
- Migraciones idempotentes; `alembic check` en CI; baseline stamp para bases
  existentes.
- Imagen non-root, read-only, tags fijos, SBOM y límites de recursos.
- **Entregable:** runbook + paneles + backup probado.

### F7. UX y accesibilidad (P2, 2–3 semanas)

- Navegación y CTAs según permisos; 403/404 consistentes.
- Asistente de importación con dry-run, errores por celda y progreso.
- Acciones masivas (aceptar/rechazar, reintentar, exportar selección).
- Dashboard con denominadores, drill-down y estados vacíos útiles.
- Mantener WCAG 2.2 AA y pruebas de teclado/axe.
- **Entregable:** prototipo + pruebas de usabilidad con supervisión/asistentes.

### F8. Despliegue y CI/CD (P1, 2 semanas)

- Local: Docker Compose + WSL (actual) documentado.
- **Staging/prod en el mismo servidor:** Docker Compose + systemd,
  PostgreSQL/PostGIS local con volumen, nginx/TLS, secretos en archivo
  protegido o gestor simple.
- Backups a disco/NAS externo (no en el mismo volumen de datos).
- CI/CD: build una vez, migraciones, smoke tests, promoción y rollback.
- Migraciones compatibles hacia atrás; ventana solo si la auditoría detecta
  duplicados.
- Sin alta disponibilidad, clúster ni object storage en esta etapa; el diseño
  deja la costura para incorporarlos después.
- **Entregable:** pipeline de despliegue + staging reproducible en un host.

---

## 6. Roadmap propuesto

| Fase | Duración | Contenido | Criterio de salida |
|---|---|---|---|
| 0. Quick wins | 1–2 semanas | Auditoría de duplicados, restricciones/CHECK (online si no hay duplicados), transacción única, importador sin 500, `init_db` dev-only, nginx/CSRF, CI skeleton, secretos | Suite verde; 0 pérdidas de datos en pruebas de fallo |
| 1. Fundación | 3 semanas | Cobertura de entregas, historial de cruces, workflow único, importador dry-run, análisis validado, pruebas herméticas | Migración `0004`; 100% tests aislados; concurrencia cubierta |
| 2. Seguridad y operación | 3–4 semanas | MFA en login, rate limit en proceso con seam, observabilidad, backups externos, staging, CI/CD, escaneo | RPO/RTO definidos; staging con TLS y alertas |
| 3. UX y estabilización | 4–6 semanas | Asistente de importación, acciones masivas, dashboards, carga, ajustes de accesibilidad | p95 < 500 ms; usabilidad validada |

Secuencialmente son ~11–15 semanas; con backend y QA trabajando en paralelo,
el esfuerzo calendario estimado es **8–12 semanas**. LDAP se planifica aparte y
no bloquea estas fases.

### 6.1 Estado de la Fase 0 (2026-11-03)

Implementado:

- [x] `scripts/audit_duplicates.py`: 0 duplicados y 0 valores fuera de rango en
  la base actual.
- [x] Migración `0004` + restricciones en los modelos: 7 `UNIQUE` y 5 `CHECK`
  (clases 1–9). Cadena `upgrade → downgrade 0003 → upgrade` probada en una base
  nueva.
- [x] Confirmación de entrega transaccional: un solo commit; si falla el
  recálculo o un movimiento, se revierte todo y la entrega queda pendiente.
- [x] Importador: archivos corruptos se convierten en `ValueError` (400 HTML),
  se parsea antes de escribir y no quedan archivos huérfanos.
- [x] `init_db()` solo en `dev`; staging/producción usan Alembic.
- [x] CI en `.github/workflows/ci.yml`: lint, unit, integración con PostGIS y
  build de imagen.
- [x] Backup `scripts/backup.ps1` con `--verify`; restauración probada
  (8 usuarios y 60 cruces recuperados en una base temporal).
- [x] Suite final: **319 unitarias + 286 integración = 605 pruebas**.

Pendiente de la Fase 0:

- [ ] Ejecutar el workflow en GitHub (aquí solo se validaron los comandos
  equivalentes).
- [ ] Idempotencia de la migración baseline `0001` para volúmenes creados con
  `init_db()` sin `alembic_version` (M-32).
- [ ] Corregir `nginx` (`Host $http_host`) y cerrar los P1 restantes de la
  auditoría en la Fase 1.

---

### 6.2 Estado de la Fase 1 (en curso)

Implementado:

- [x] **Historial append-only de cruces** (`resultados_cruce_historial`,
  migración `0005`) con punto/año, valores A/B/C, resultado, clase consolidada,
  regla, actor y entrega de origen. Verificado en SQLite y PostgreSQL.
- [x] **Validación estadística**: los ponderadores deben ser finitos, no
  negativos y sumar 1; el área total debe ser positiva. El kappa ahora es
  ponderado por área y coherente con la exactitud global.
- [x] **Acciones de la tabla de usuarios en modal**: `<dialog>` nativo con
  trampa de foco, Escape, retorno de foco y fallback sin JavaScript; validado
  en Edge/Playwright (4 formularios, cierre por Escape y por botón).
- [x] **Activos de producción B1–B3**: compose de producción, nginx TLS,
  `.env.production` generado con secretos, preflight y bootstrap de
  administrador. Validados `docker compose config`, `Settings()` y `nginx -t`
  con configuración temporal.
- [x] **B4 — backups cifrados y programados**: `backup-local.sh` cifra con
  AES-256/PBKDF2, verifica el dump y restaura en base temporal; unidad y timer
  systemd diarios; `register-backup.ps1` para Windows. Probado con 8 usuarios y
  60 cruces restaurados.
- [x] **B5 — auditoría real y MFA**: actor `usuario:<id>` en entregas, QA/QC,
  reemplazos, malla y mapa; `entrega_recibida` + `cargada_por_id`; alta de
  roles críticos solo con TOTP.
- [x] **B6 — importador**: IDs decimales, `ID_EV` desconocido sin fallback
  silencioso, fórmulas sin caché y hojas no reconocidas reportadas con celda o
  hoja.
- [x] **B7 — observabilidad**: `/ready` con base, logs con `request_id`,
  healthcheck operativo con HTTP/base/disco/backup/certificado y timer systemd.
- [x] **B8 — CI/CD**: publicación de imagen en GHCR con tags inmutables;
  commit `0601890` publicado en `master`. Verificar la ejecución en GitHub y
  configurar staging.
- [x] **B9 — contenedor**: non-root, read-only, `cap_drop`, límites y
  `no-new-privileges`, verificado con la imagen real.
- [x] Suite: **319 unitarias + 286 integración = 605 pruebas**.

Pendiente de la Fase 1:

- [ ] Tabla de cobertura de entrega (`entrega_alcance`) para separar “no opina”
  de “dijo vacío” sin depender de tombstones.
- [ ] Workflow único `FichaQAQC` + `Reemplazo` (una sola máquina de estados).
- [ ] Dry-run de importación con informe por celda y job en background.
- [ ] Persistir plausibilidad y parámetros de análisis para reproducibilidad
  completa.

## 7. Matriz impacto / esfuerzo

| Mejora | Impacto | Esfuerzo | Prioridad |
|---|---|---|---|
| Transacción única + restricciones | Muy alto | Medio | P0 |
| Importador dry-run + errores por celda | Muy alto | Medio | P0 |
| Pruebas herméticas + concurrencia | Alto | Medio | P0 |
| Workflow único fichas/reemplazos | Alto | Alto | P1 |
| Historial de cruces + reproducibilidad | Alto | Medio | P1 |
| MFA obligatorio en login + rate limit en proceso | Alto | Medio | P1 |
| Observabilidad + backups | Alto | Medio | P1 |
| CI/CD + staging | Alto | Medio | P1 |
| UX de roles + acciones masivas | Medio | Medio | P2 |
| Escalar a varios workers (si se requiere) | Bajo | Medio | P2 |
| Multiproyecto / multi-tenant | **Fuera de alcance** | — | — |

---

## 8. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Migración de datos duplicados | Detectar y consolidar antes de aplicar `UNIQUE`; script de auditoría y rollback. |
| Cambio de workflow de fichas | Implementar en paralelo con feature flag; migrar datos; pruebas de regresión. |
| Rotación de secretos | `EET_FERNET_KEYS` multi-clave; re-cifrar outbox; runbook y ventana. |
| Tests lentos | Base template + transacciones; paralelizar; separar unit/integration/e2e. |
| Falta de tiempo del equipo | Priorizar P0/P1; fases con entregables verificables. |
| Resistencia al cambio de UX | Prototipos con supervisión/asistentes; medir tareas. |

---

## 9. Criterios de aceptación y KPIs

- **Integridad:** 0 entregas aceptadas con rollback parcial; 0 duplicados por
  clave natural; 100% confirmaciones atómicas.
- **Trazabilidad:** toda clase consolidada reconstruible desde interpretaciones,
  versión de regla, ficha y actor.
- **Pruebas:** 100% de tests aislados; concurrencia cubierta; e2e de flujos
  críticos; cobertura de dominio ≥ 80%.
- **Rendimiento:** p95 de vistas < 500 ms; importación de 20 MB < 2 s en modo
  síncrono o job con progreso.
- **Operación:** RPO ≤ 5 min, RTO ≤ 1 h; backup externo al host de datos;
  restore probado; alertas de 5xx, cola y backup.
- **Seguridad:** 0 hallazgos altos en SAST/dependencias; MFA obligatorio en
  roles críticos; secretos fuera del repo.
- **Accesibilidad:** WCAG 2.2 AA; axe sin errores críticos en rutas principales.

---

## 10. Equipo y estimación

| Rol | Dedicación | Foco |
|---|---|---|
| Backend senior | 1.0 | Dominio, transacciones, importador, seguridad |
| Backend/DevOps | 0.5 | CI/CD, observabilidad, backups, staging |
| QA | 0.5 | Pruebas herméticas, concurrencia, e2e, carga |
| UX/Frontend | 0.5 | Importación, roles, dashboards, accesibilidad |

**Estimación:** **8–12 semanas** con ese equipo; **6–8 semanas** con 2 backend a
tiempo completo y QA parcial. La Fase 0 puede iniciarse de inmediato y entrega
valor en 1–2 semanas. LDAP no está incluido en esta estimación.

---

## 11. Decisiones confirmadas y pendientes

### Confirmadas

1. **Un solo proyecto**: sin multi-tenant ni UI multiproyecto.
2. **Mismo servidor**: app, PostgreSQL/PostGIS y archivos en el mismo host;
   backups a disco/NAS externo.
3. **Una sola instancia** por ahora: rate limiting en proceso y sin locks
   distribuidos; se deja la interfaz para Redis si se escala.
4. **LDAP más adelante**: la abstracción de proveedores ya existe; no bloquea
   las fases P0/P1.
5. **Ventana solo si hay duplicados**: auditoría previa; si no hay, las
   restricciones se aplican online.

### Pendientes de definir

1. Destino exacto y retención de los backups (NAS, disco externo, otro servidor).
2. Dominio y certificado TLS para staging/producción.
3. Política ante duplicados: consolidar el valor más reciente o rechazar y
   devolver el archivo al asistente.
4. Momento de la integración LDAP (fase 2 o posterior).
5. ¿Se retienen los archivos originales indefinidamente o con retención por
   periodo?

---

## Anexo A. Archivos clave para la implementación

- `app/services/persist.py`: confirmación, cruces, fichas, reemplazos.
- `app/services/importer.py`: parser de entregas.
- `app/services/store.py`: ruta legacy a eliminar o alinear.
- `app/models/__init__.py`: restricciones, índices e historial.
- `app/domain/qaqc.py`: máquina de estados.
- `app/services/analysis.py`: estimadores y validación de pesos.
- `tests/conftest.py`: base hermética y factories.
- `docker-compose.yml`, `Dockerfile`, `deploy/nginx.conf`: operación.
- `scripts/test-local.*`, `scripts/start-local.*`: entorno local.

## Anexo B. Referencias

- `docs/auditoria-bugs-2026-11-03.md`: hallazgos, evidencias y estado.
- `docs/analisis-autenticacion.md`: modelo de roles, sesiones y MFA.
- `docs/plan-implementacion-autenticacion.md`: fases A0–A6.
- `docs/analiza el modelo y el Protocolo, como acotacion l.md`: reglas EET.
- `docs/qa-accesibilidad.md`: criterios WCAG y auditorías.
