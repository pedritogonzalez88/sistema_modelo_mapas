# Revisión de arquitectura y calidad — 2026-11-06

## Veredicto

El proyecto es un **monolito por capas pragmático** (FastAPI + SQLAlchemy +
Jinja2), no una Clean Architecture estricta. La dirección de dependencias es
correcta en lo esencial: el dominio no conoce infraestructura, los servicios no
conocen routers y `core` no depende de la capa web. Se eliminaron la ruta
legacy `Store` de la aplicación y los esquemas sin uso; el riesgo restante es
que `auth/service.py` y `auth/router.py` siguen siendo grandes.

| Dimensión | Evaluación |
|---|---|
| Dirección de dependencias | **Buena** (domain → core/models; services → domain/models; routers → services/auth) |
| Pureza del dominio | **Muy buena** (`app/domain` sin imports de FastAPI, SQLAlchemy ni settings) |
| Separación de responsabilidades | **Media-alta** (`persist` ya está dividido; `auth/service.py` sigue concentrando MFA/correo/reset) |
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
  services/    persistencia y casos de uso (entregas, cruces, malla, qaqc, dashboard, importer, analysis)
  auth/        slice vertical: login, MFA, sesiones, usuarios, LDAP, correo, auditoría
  routers/     capa HTTP (FastAPI)
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
| `app/auth/users.py` | 604 | Administración de usuarios, roles y auditoría |
| `app/auth/router.py` | 575 | Formularios y flujos de autenticación |
| `app/auth/ldap.py` | 497 | LDAP/AD y diagnóstico |
| `app/auth/mfa.py` | 383 | TOTP, respaldos y challenges |
| `app/auth/sessions.py` | 371 | Sesiones, login y cambio de contraseña |
| `app/cli.py` | 660 | Muchos comandos de operación |

`app/services/persist.py` quedó reducido a una **fachada de 87 líneas**;
`app/auth/service.py` es una **fachada de 85 líneas**; `app/auth/providers.py`
es una **fachada de 21 líneas**. La implementación vive en módulos cohesivos de
74–604 líneas. `app/services/store.py` se movió a
`tests/support/legacy_store.py` y `app/schemas` se eliminó por no tener uso.

Riesgo restante: ya no hay módulos por encima de ~650 líneas. Los siguientes
refactors, opcionales, son dividir `auth/users.py`, `auth/router.py` y
`auth/ldap.py` por áreas funcionales.

### 2. Ruta legacy `Store` — resuelto

`app/services/store.py` se movió a `tests/support/legacy_store.py`. La
aplicación ya no tiene una segunda implementación del motor; las pruebas legacy
la usan de forma explícita y aislada.

### 3. `app/schemas` sin uso — resuelto

Se eliminó el paquete vacío. Los formularios se validan en servicios/routers;
si se añaden endpoints JSON formales, conviene reintroducir contratos Pydantic
con uso real.

### 4. Cobertura desigual

Cobertura global ~81%, pero:

| Módulo | Cobertura |
|---|---:|
| `app/domain/*` | 98–100% |
| `app/auth/sessions.py` | 87% |
| `app/auth/mfa.py` | 87% |
| `app/auth/email.py` | 86% |
| `app/auth/reset.py` | 97% |
| `app/auth/users.py` | 89% |
| `app/auth/router.py` | 88% |
| `app/auth/routes_users.py` | 88% |
| `app/auth/ldap.py` | 84% |
| `app/services/importer.py` | 90% |
| `app/services/cruces.py` | 77% |
| `app/services/entregas.py` | 74% |
| `app/services/malla.py` | 54% |
| `app/services/qaqc.py` | 56% |
| `app/services/reemplazos.py` | 38% |
| `app/routers/qaqc.py` | 50% |
| `app/routers/reemplazos.py` | 51% |
| `app/cli.py` | 56% |

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
- **`persist.py` dividido**: la implementación quedó en `bootstrap.py`,
  `entregas.py`, `cruces.py`, `malla.py`, `reemplazos.py`, `qaqc.py`,
  `dashboard.py` y `common.py`. `persist.py` es una fachada de compatibilidad
  de 87 líneas.
- **Autenticación dividida**: `auth/service.py` pasó de 2139 a una fachada de
  85 líneas. La implementación vive en `auth/sessions.py` (371),
  `auth/mfa.py` (383), `auth/email.py` (285), `auth/reset.py` (252),
  `auth/users.py` (604), `auth/common.py` (136),
  `auth/notifications.py` (74) y `auth/mailer.py`.
- **Routers de autenticación**: `auth/router.py` bajó de 812 a 575 líneas;
  usuarios y auditoría viven en `auth/routes_users.py` (245), con utilidades
  compartidas en `auth/router_common.py`.
- **Proveedores divididos**: `auth/providers.py` es una fachada de 21 líneas;
  el proveedor local y el mapeo están en `auth/providers_common.py` (145) y
  LDAP/AD en `auth/ldap.py` (497).
- **Cobertura en CI**: `pytest-cov` con umbral 80% y configuración en
  `pyproject.toml`. El job de integración ejecuta unitarias + integración y
  falla por debajo del umbral. Cobertura actual: **82%**.
- **Reglas de arquitectura**: `import-linter` con tres contratos:
  dominio sin infraestructura, servicios sin capa web y core sin capas
  superiores. Los tres pasan.
- **Tipado estático**: `mypy` sobre `app/domain`, `app/core` y `app/services`,
  con `check_untyped_defs`; **22 archivos sin errores**.
- **Ruff limpio**: se corrigieron imports, sintaxis moderna y estilos; quedan
  reglas ignoradas justificadas (`B008` de FastAPI, `BLE001`, `DTZ003`,
  `ISC004`).
- **Pruebas de concurrencia**: dos hilos compiten por el mismo código de
  respaldo y por el mismo TOTP; solo uno gana en PostgreSQL.

Pendiente de las recomendaciones: adoptar contratos Pydantic para formularios;
ampliar mypy a `services`/`auth`; añadir pruebas de concurrencia reales y
resolver la deuda de estilo de ruff.

## Plan de mejora recomendado

### Corto plazo (bajo riesgo)

1. [x] Aislar `Store` en pruebas.
2. [x] Eliminar `app/schemas` sin uso.
3. [x] Ruff completo limpio; reglas no aplicables quedan justificadas en
   `pyproject.toml`.
4. [x] Añadir `pytest-cov` con umbral 80% y reporte en CI.
5. [~] Añadir pruebas de `persist` y concurrencia; todavía se puede ampliar la
   cobertura de routers `qaqc`/`reemplazos`.

### Medio plazo (refactor)

1. [x] Dividir `persist.py` en módulos cohesivos con fachada de compatibilidad.
2. [x] Dividir `auth/service.py` en sesiones, MFA, correo, reset, usuarios y
   utilidades compartidas, con fachada de compatibilidad.
3. [ ] Introducir `Protocol` para `Mailer`, `Clock` y repositorios si se quiere
   testabilidad pura.
4. [x] Añadir `mypy` gradual en `domain`, `core` y `services`.
5. [x] Añadir `import-linter` para dependencias prohibidas.

### Largo plazo (si el sistema crece)

- Separar el slice `auth` en un paquete con capa de aplicación propia.
- Evaluar worker de correo/importación en background.
- [x] Añadir pruebas de concurrencia reales para códigos de respaldo y TOTP.

## Conclusión

El proyecto **no es un desastre arquitectónico**: el dominio está limpio, la
dirección de dependencias es sana y la seguridad está por encima de la media
para una aplicación de este tamaño. El problema principal era de
**modularidad**, y ya se corrigió en `services/` y `auth/`. Los siguientes
pasos, opcionales, son dividir `auth/users.py`, `auth/router.py` y
`auth/ldap.py`, subir la cobertura de QA/QC y reemplazos y adoptar contratos
Pydantic. Con eso, la base actual puede considerarse una arquitectura limpia
pragmática y sostenible.
