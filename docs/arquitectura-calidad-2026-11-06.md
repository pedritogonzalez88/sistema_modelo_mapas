# Revisión de arquitectura y calidad — 2026-11-06

## Veredicto

El proyecto es un **monolito por capas pragmático** (FastAPI + SQLAlchemy +
Jinja2), no una Clean Architecture estricta. La dirección de dependencias es
correcta en lo esencial: el dominio no conoce infraestructura, los servicios no
conocen routers y `core` no depende de la capa web. Sin embargo, hay **módulos
demasiado grandes** y una ruta legacy (`Store`) que duplica responsabilidades.

| Dimensión | Evaluación |
|---|---|
| Dirección de dependencias | **Buena** (domain → core/models; services → domain/models; routers → services/auth) |
| Pureza del dominio | **Muy buena** (`app/domain` sin imports de FastAPI, SQLAlchemy ni settings) |
| Separación de responsabilidades | **Media** (`auth/service.py` y `services/persist.py` concentran demasiado) |
| Seguridad | **Buena** (CSRF, CSP, MFA, rate limiting, auditoría, cookies `__Host-`) |
| Pruebas | **Buena** (603 pruebas, ~81% cobertura; huecos en flujos complejos) |
| Tipado estático | **Baja** (sin mypy/pyright; muchos `dict`/`Any`) |
| Deuda de estilo | **Media** (187 avisos ruff; CI solo bloquea F/E9) |
| Documentación operativa | **Muy buena** (runbooks, auditoría, despliegue, backups) |

## Estructura actual

```text
app/
  core/        configuración, seguridad criptográfica, base de datos
  domain/      reglas puras: clases, cruce, QA/QC, transiciones
  models/      ORM SQLAlchemy
  services/    persistencia y casos de uso (persist, importer, analysis, store legacy)
  auth/        slice vertical: login, MFA, sesiones, usuarios, LDAP, correo, auditoría
  routers/     capa HTTP (FastAPI)
  schemas/     contratos Pydantic (mayormente sin uso)
  templates/   Jinja2
  static/      CSS/JS
```

Flujo típico: `router → service → model/domain`. `main.py` compone la app,
middlewares y routers. `auth` es un slice vertical que no depende de
`app/services`, lo cual evita acoplamiento cruzado.

## Fortalezas

1. **Dominio independiente**: `app/domain` solo importa entre sus módulos; es
   reutilizable y testeable sin base de datos.
2. **Configuración centralizada** en `app/core/config.py`, con validación de
   producción y secretos fuera del código.
3. **Seguridad bien resuelta para el tamaño del proyecto**: CSRF, CSP con
   nonce, cookies `__Host-`, TOTP, códigos de respaldo, rate limiting, bloqueo
   de cuenta/IP, auditoría con actor real.
4. **Migraciones Alembic** con cadena `0001–0005` y verificación `alembic check`.
5. **Pruebas separadas** unitarias/integración, con fixtures y smoke HTTP.
6. **Operación documentada**: compose TLS/HTTP, Easypanel, backups cifrados,
   healthcheck, systemd, preflight.
7. **Contenedor endurecido**: non-root, read-only, `cap_drop`, límites.
8. **Sin errores de correctitud** en ruff (`F`, `E9`, `B904` limpios).

## Hallazgos principales

### 1. Módulos “God” (mantenibilidad)

| Archivo | Líneas | Problema |
|---|---:|---|
| `app/auth/service.py` | 2139 | Login, sesiones, MFA, correo, recuperación, usuarios, LDAP, auditoría y exportación |
| `app/services/persist.py` | 983 | Entregas, cruces, malla, mapa, QA/QC, dashboard, seed y paginación |
| `app/auth/router.py` | 812 | Formularios, POST y flujos de autenticación |
| `app/cli.py` | 660 | Muchos comandos de operación |
| `app/auth/providers.py` | 658 | LDAP y sincronización |

Riesgo: cambios locales con efectos laterales, tests lentos, revisiones
difíciles y alta probabilidad de duplicación.

### 2. Ruta legacy `Store`

`app/services/store.py` (220 líneas) mantiene una segunda implementación del
motor de cruces/QA-QC. Solo la usan pruebas; la aplicación usa
`services/persist.py` y `domain/*`. Es deuda de diseño: dos fuentes de verdad.

### 3. `app/schemas` sin uso

`EntregaCreate`, `EntregaOut`, `FichaUpdate`, `QaqcEvento` y `DashboardOut` no
se usan en routers (0% cobertura). Los formularios validan a mano en routers y
servicios. O se adoptan como contratos, o se eliminan.

### 4. Cobertura desigual

Cobertura global ~81%, pero:

| Módulo | Cobertura |
|---|---:|
| `app/domain/*` | 98–100% |
| `app/auth/service.py` | 89% |
| `app/auth/router.py` | 88% |
| `app/services/importer.py` | 90% |
| `app/services/persist.py` | 56% |
| `app/routers/qaqc.py` | 50% |
| `app/routers/reemplazos.py` | 51% |
| `app/services/store.py` | 50% |
| `app/cli.py` | 56% |
| `app/schemas/__init__.py` | 0% |

Los flujos de mayor valor (confirmación, QA/QC, reemplazos, CLI) son justamente
los que menos cobertura tienen.

### 5. Lint y tipado

- 187 avisos ruff, concentrados en:
  - `B008` (46): `Depends()` en defaults, idiomático en FastAPI.
  - `I001` (19): orden de imports.
  - `UP007` (15): `Union` vs `|`.
  - `ISC004` (15): strings implícitamente concatenados.
  - `DTZ003` (15): `datetime.utcnow()` (ya corregido en los puntos funcionales).
  - `BLE001` (11): `except Exception` intencionales en integraciones.
- El CI solo ejecuta `ruff --select F,E9`; el resto no bloquea.
- No hay `mypy`/`pyright` ni anotaciones estrictas; muchos `dict` sin tipar.

### 6. Acoplamiento a infraestructura

Los servicios reciben una `Session` de SQLAlchemy y usan el ORM directamente.
Es aceptable para este tamaño, pero no es Clean Architecture estricta: no hay
puertos/interfaces de repositorio, ni capa de casos de uso independiente, ni
inyección de reloj/mailer. `settings` y `engine` son globales.

### 7. Pruebas de integración con estado global

Algunas pruebas usan la base de desarrollo y cuentan filas globales. Se mejoró
con filtros por usuario y fixtures, pero aún hay riesgo de interferencia si se
ejecutan en paralelo o contra datos reales.

## ¿Es “arquitectura limpia”?

**No en el sentido estricto**, pero sí un **monolito bien estratificado** con
un dominio limpio. La dirección de dependencias es correcta y no hay ciclos
graves entre dominio, servicios, routers y infraestructura. Lo que falta para
Clean Architecture estricta:

- Puertos/interfaces para repositorios, correo y reloj.
- Casos de uso separados de la persistencia.
- Módulos más pequeños y cohesivos.
- Contratos de entrada/salida consistentes (Pydantic).
- Reglas de dependencia verificadas automáticamente.

## Aplicado en esta iteración

- **`Store` aislado**: `app/services/store.py` se movió a
  `tests/support/legacy_store.py`. La aplicación ya no contiene la segunda
  implementación del motor; solo las pruebas legacy la usan.
- **`app/schemas` eliminado**: no tenía imports ni uso; los contratos de
  entrada se validan en servicios/routers. Se puede reintroducir cuando haya
  endpoints JSON formales.
- **Cobertura en CI**: `pytest-cov` con umbral 80% y configuración en
  `pyproject.toml`. El job de integración ejecuta unitarias + integración y
  falla por debajo del umbral. Cobertura actual: **82%**.
- **Reglas de arquitectura**: `import-linter` con tres contratos:
  dominio sin infraestructura, servicios sin capa web y core sin capas
  superiores. Los tres pasan.
- **Tipado estático inicial**: `mypy` sobre `app/domain` y `app/core`, con
  `check_untyped_defs`; pasa sin errores. Se puede ampliar por módulos.

Pendiente de las recomendaciones: dividir `persist.py` y `auth/service.py`,
adoptar contratos Pydantic para formularios, ampliar mypy al resto y añadir
pruebas de concurrencia reales.

## Plan de mejora recomendado

### Corto plazo (bajo riesgo)

1. Eliminar o aislar `Store`; migrar sus pruebas a `domain`/`persist`.
2. Adoptar o borrar `app/schemas`; validar formularios con Pydantic donde aporte.
3. Activar `ruff` completo en modo advisory y arreglar `I001`, `UP007`,
   `ISC004`, `DTZ003`.
4. Añadir `pytest-cov` con umbral mínimo (por ejemplo 80%) y reporte en CI.
5. Añadir pruebas de `persist.py` (confirmación, reemplazos, malla) y de los
   routers `qaqc`/`reemplazos`.

### Medio plazo (refactor)

1. Dividir `persist.py` en `entregas.py`, `cruces.py`, `malla.py`, `qaqc.py`,
   `dashboard.py` y `bootstrap.py`.
2. Dividir `auth/service.py` en `login.py`, `mfa.py`, `usuarios.py`,
   `tokens.py` y `auditoria.py`.
3. Introducir `Protocol` para `Mailer`, `Clock` y repositorios si se quiere
   testabilidad pura.
4. Añadir `mypy`/`pyright` gradual (primero `domain` y `core`).
5. Añadir `import-linter` para prohibir dependencias domain→infra y
   services→routers.

### Largo plazo (si el sistema crece)

- Separar el slice `auth` en un paquete con capa de aplicación propia.
- Evaluar worker de correo/importación en background.
- Añadir pruebas de concurrencia reales para MFA, reset y confirmación.

## Conclusión

El proyecto **no es un desastre arquitectónico**: el dominio está limpio, la
dirección de dependencias es sana y la seguridad está por encima de la media
para una aplicación de este tamaño. El problema principal es de **modularidad**:
dos archivos concentran la mayor parte de la lógica y hay una implementación
legacy duplicada. Con la división de `persist.py` y `auth/service.py`, la
eliminación de `Store`/schemas muertos y un mínimo de tipado/cobertura en los
flujos críticos, la base actual puede considerarse una arquitectura limpia
pragmática y sostenible.
