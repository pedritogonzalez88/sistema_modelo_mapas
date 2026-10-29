# Plan de implementación — Modernización UX/UI del Validador EET

> Complemento operativo de `docs/plan-ux-ui-modernizacion.md`.
> Este documento describe **cómo** se implementa: tareas atómicas, archivos, contratos,
> verificaciones, orden de ejecución y criterios de reversión.

---

## 1. Objetivo y alcance

Convertir las 10 plantillas actuales en una interfaz consistente, accesible y mantenible,
**sin cambiar reglas de negocio ni el modelo de datos**.

### Dentro del alcance

- Sistema de diseño (tokens + componentes Jinja reutilizables).
- Shell de navegación (sidebar/topbar/drawer) y páginas de error.
- Rediseño de las 11 vistas existentes.
- Feedback de acciones (banners), validación inline y estados vacíos accionables.
- Filtros, conteos y paginación en tablas.
- Build local de CSS; eliminación de `cdn.tailwindcss.com`.
- Accesibilidad WCAG 2.2 AA y responsive 360–1440 px.
- Pruebas de contrato de formularios y de render de componentes.

### Fuera del alcance (no tocar en este plan)

- Autenticación, roles y permisos (`Usuario` sigue sin uso).
- Migraciones de esquema (no se agregan columnas ni tablas).
- Cambios en `cross_engine`, `qaqc`, `transition_rules`, `importer`, `analysis`.
- Mapa geográfico interactivo.
- HTMX / SPA / frameworks JS.

### Restricciones no negociables

1. **No alterar los `name=` de los formularios** que ya consume el backend.
2. **No modificar textos normativos** del protocolo (clases, causas, estados, referencias 4.x/5.x).
3. **La app debe funcionar sin JavaScript** (mejoras progresivas, no dependencias).
4. **No introducir dependencias Python nuevas** para esta fase.
5. Todo cambio visual debe ser reversible por commit.

---

## 2. Decisiones de implementación (ADR cortos)

| # | Decisión | Alternativas descartadas | Motivo |
|---|---|---|---|
| D1 | Tailwind **v4** compilado con `@tailwindcss/cli` (npm) y CSS versionado | CDN; Tailwind v3 + config JS; CSS plano | Node 22/npm 10 ya disponibles; v4 es configuración CSS-first; compilado < 40 KB |
| D2 | Componentes como **macros Jinja** en `templates/components/` | Includes; Web Components; macros en `base.html` | Reutilización sin runtime; testeables con `jinja2.Environment` |
| D3 | Feedback con **códigos en query string** (`?ok=...`, `?error=...`) | Sesiones/cookies; flash con `itsdangerous`; HTMX | Cero dependencias nuevas, sin XSS, testeable, sobrevive redirecciones |
| D4 | Errores HTTP renderizados como **HTML** (`error.html`) | Mantener JSON de FastAPI | El JSON crudo es el defecto #1 de UX |
| D5 | Gráficos como **SVG generado en Jinja** | Chart.js; Recharts | Sin JS, accesible, suficiente para una dona y barras |
| D6 | JS propio mínimo (`app.js`, ~120 líneas) | Alpine, HTMX, Stimulus | Solo dropzone, drawer, bloqueo de submit y cambios sin guardar |
| D7 | Iconos en **sprite SVG** local | Font Awesome, Heroicons CDN | Sin red, sin CLS, control total |
| D8 | Tipografía: stack `system-ui` primero, Inter opcional | Inter obligatoria | Evita depender de descarga de fuentes; Inter se suma sin romper nada |
| D9 | Paginación y conteos **en servidor** | Paginar en cliente | Volúmenes reales (cientos/miles de cruces) |
| D10 | `return_to` en query para conservar filtros | Estado en sesión | Stateless, simple, visible en la URL |

---

## 3. Preparación del entorno

### 3.1 Red de seguridad (el proyecto **no es un repositorio Git**)

```powershell
cd C:\laragon\www\sistema_modelo_mapas
git init
# .gitignore mínimo
@"
.venv/
__pycache__/
.pytest_cache/
.idea/
uploads/
node_modules/
app/static/css/app.css
.env
"@ | Set-Content .gitignore
git add .
git commit -m "chore: baseline antes de modernizacion UX/UI"
```

> `app.css` se ignora localmente pero **se versiona en el build de Docker** (se compila dentro de la imagen).
> Si el equipo prefiere no exigir Node en cada despliegue, quitar esa línea y commitear el CSS generado.

### 3.2 Baseline funcional

1. Levantar el stack: `docker compose up -d`.
2. Recorrer las 11 rutas y guardar capturas en `docs/baseline/` (durante la fase 0, fuera de Git si pesan).
3. Ejecutar pruebas actuales:

```powershell
uv run pytest -q
```

Resultado esperado: 6 archivos de pruebas de dominio en verde. **Si algo falla aquí, se detiene la implementación.**

### 3.3 Baseline de usabilidad (30 min)

| Tarea | Métrica a registrar |
|---|---|
| T1. Subir y confirmar una entrega | segundos, clics, errores |
| T2. Encontrar un cruce de un punto/año | segundos, intentos |
| T3. Resolver una ficha QA/QC de principio a fin | segundos, campos omitidos |
| T4. Crear y autorizar un reemplazo | segundos |
| T5. Descargar el consolidado | segundos |

---

## 4. Estructura de archivos objetivo

```
app/
├── static/
│   ├── css/
│   │   ├── input.css            # NUEVO: @theme + @layer components
│   │   └── app.css              # GENERADO por el build
│   ├── js/
│   │   └── app.js               # NUEVO
│   ├── icons.svg                # NUEVO: sprite
│   ├── favicon.svg              # NUEVO
│   ├── plantillas/
│   │   └── malla.csv            # NUEVO: ejemplo de carga
│   └── fonts/                   # OPCIONAL: inter-*.woff2
├── templates/
│   ├── base.html                # REESCRITO
│   ├── error.html               # NUEVO
│   ├── components/
│   │   ├── shell.html           # NUEVO: sidebar + topbar + drawer
│   │   ├── ui.html              # NUEVO: macros
│   │   └── icons.html           # NUEVO: macro icon()
│   └── <9 vistas>.html          # REDISEÑADAS
├── web.py                        # + flash, + registro de globals
└── main.py                       # + handler de errores
package.json                      # NUEVO
tailwind (devDependency)          # NUEVO (vía npm)
Dockerfile                        # + etapa de build CSS
tests/
├── test_ui_components.py         # NUEVO (sin BD)
├── test_flash_messages.py        # NUEVO (sin BD)
└── test_web_routes.py            # NUEVO (integración, requiere Postgres)
```

Nada de esto toca `app/domain/`, `app/services/` (salvo dos consultas nuevas), `app/models/` ni `app/schemas/`.

---

## 5. Fase 0 — Red de seguridad (0.5 día)

| ID | Tarea | Archivo | Verificación |
|---|---|---|---|
| F0.1 | `git init` + `.gitignore` + commit baseline | `.gitignore` | `git log --oneline` muestra 1 commit |
| F0.2 | Capturas y baseline de usabilidad | `docs/baseline/` | 11 capturas + tabla de métricas |
| F0.3 | `uv run pytest -q` en verde | — | 0 fallos |
| F0.4 | Instalar toolchain CSS | `package.json` | `npm install` sin errores |

```json
{
  "name": "eet-ui",
  "private": true,
  "version": "1.0.0",
  "scripts": {
    "css:build": "tailwindcss -i ./app/static/css/input.css -o ./app/static/css/app.css --minify",
    "css:watch": "tailwindcss -i ./app/static/css/input.css -o ./app/static/css/app.css --watch"
  },
  "devDependencies": {
    "tailwindcss": "4.1.14",
    "@tailwindcss/cli": "4.1.14"
  }
}
```

> Fijar versión exacta (sin `^`) para builds reproducibles. Verificar la última estable al momento de ejecutar
> con `npm view @tailwindcss/cli version` y actualizar ambos números.

---

## 6. Fase 1 — Fundaciones (3 días)

### F1.1 Tokens y build CSS

**`app/static/css/input.css`**

```css
@import "tailwindcss";

/* Tailwind v4: escaneo explícito y determinista (también en Docker) */
@source "../../templates";
@source "../js";

@theme {
  --color-brand-900: #0B3B2E;
  --color-brand-700: #14624A;
  --color-brand-500: #1E8A69;
  --color-brand-100: #D6EBE2;
  --color-brand-50:  #EAF5F0;

  --color-ink-900: #0F172A;
  --color-ink-700: #334155;
  --color-ink-600: #475569;
  --color-ink-400: #64748B;

  --color-canvas: #F4F7F6;
  --color-line: #E2E8E7;

  --font-sans: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;

  --radius-card: 12px;
  --shadow-card: 0 1px 2px rgba(15, 23, 42, .06);
}

@layer base {
  html { -webkit-text-size-adjust: 100%; }
  body { @apply bg-canvas text-ink-900 font-sans antialiased; }
  :focus-visible { outline: 2px solid var(--color-brand-500); outline-offset: 2px; }
  h1, h2, h3 { @apply tracking-tight; }
  .tabular { font-variant-numeric: tabular-nums; }
}

@layer components {
  .btn { @apply inline-flex items-center justify-center gap-2 rounded-lg px-4 h-11
                 text-sm font-medium transition-colors disabled:opacity-50
                 disabled:cursor-not-allowed; }
  .btn--primary   { @apply bg-brand-700 text-white hover:bg-brand-900; }
  .btn--secondary { @apply bg-white text-ink-900 border border-line hover:bg-canvas; }
  .btn--ghost     { @apply text-brand-700 hover:bg-brand-50; }
  .btn--danger    { @apply bg-white text-red-700 border border-red-300 hover:bg-red-50; }
  .btn--sm        { @apply h-9 px-3 text-xs; }

  .card { @apply bg-white border border-line rounded-xl shadow-[var(--shadow-card)]; }
  .card__header { @apply px-5 pt-5 pb-0; }
  .card__title { @apply text-base font-semibold; }
  .card__subtitle { @apply text-sm text-ink-600 mt-1; }
  .card__body { @apply p-5; }

  .badge { @apply inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium; }
  .badge--ok      { @apply bg-emerald-100 text-emerald-800; }
  .badge--warn    { @apply bg-amber-100 text-amber-800; }
  .badge--danger  { @apply bg-red-100 text-red-800; }
  .badge--info    { @apply bg-sky-100 text-sky-800; }
  .badge--neutral { @apply bg-slate-100 text-slate-700; }

  .stat { @apply card p-5 flex flex-col gap-1; }
  .stat__label { @apply text-xs font-medium uppercase tracking-wide text-ink-600; }
  .stat__value { @apply text-3xl font-semibold tabular; }
  .stat__hint  { @apply text-xs text-ink-400; }

  .field { @apply flex flex-col gap-1.5; }
  .field__label { @apply text-sm font-medium text-ink-900; }
  .field__hint  { @apply text-xs text-ink-600; }
  .field__error { @apply text-xs text-red-700; }
  .input, .select, .textarea {
    @apply w-full rounded-lg border border-line bg-white px-3 py-2 text-sm
           placeholder:text-ink-400 focus:border-brand-500 focus:ring-2
           focus:ring-brand-100 focus:outline-none;
  }

  .table-wrap { @apply overflow-x-auto rounded-xl border border-line bg-white; }
  .table { @apply w-full text-sm border-collapse; }
  .table thead th { @apply sticky top-0 z-10 bg-canvas/95 backdrop-blur px-3 py-2.5
                            text-left text-xs font-semibold uppercase tracking-wide
                            text-ink-600 border-b border-line whitespace-nowrap; }
  .table tbody td { @apply px-3 py-3 border-b border-line align-middle; }
  .table tbody tr:hover { @apply bg-canvas/60; }

  /* Clases de cobertura 1–9: color + código, nunca solo color */
  .clase { @apply inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium
                  border border-transparent; }
  .clase-1 { @apply bg-emerald-50 text-emerald-900 border-emerald-200; }
  .clase-2 { @apply bg-lime-50 text-lime-900 border-lime-200; }
  .clase-3 { @apply bg-yellow-50 text-yellow-900 border-yellow-200; }
  .clase-4 { @apply bg-green-50 text-green-900 border-green-200; }
  .clase-5 { @apply bg-amber-50 text-amber-900 border-amber-200; }
  .clase-6 { @apply bg-orange-50 text-orange-900 border-orange-200; }
  .clase-7 { @apply bg-cyan-50 text-cyan-900 border-cyan-200; }
  .clase-8 { @apply bg-violet-50 text-violet-900 border-violet-200; }
  .clase-9 { @apply bg-slate-100 text-slate-800 border-slate-300; }
}
```

**Verificación:** `npm run css:build` genera `app/static/css/app.css`; `Get-Item` muestra 15–40 KB;
`base.html` ya no referencia el CDN y no hay scroll horizontal en `/`.

### F1.2 Componentes

**`app/templates/components/ui.html`** — macros obligatorias (contrato interno):

| Macro | Firma | Notas |
|---|---|---|
| `btn` | `(label, href=None, variant="primary", size="md", icon=None, type="button")` | Si `href`, renderiza `<a>`; si no, `<button>` |
| `badge` | `(text, tone="neutral", icon=None)` | `tone` ∈ ok/warn/danger/info/neutral |
| `card` | `(title=None, subtitle=None, class="")` con `{% call %}` | — |
| `stat` | `(label, value, hint=None, tone="neutral")` | `value` ya formateado |
| `empty` | `(title, text, action_label=None, action_href=None)` | — |
| `banner` | `(tone, text, dismissible=True)` | `role="status"` o `alert` |
| `field` | `(label, name, type="text", value="", hint=None, error=None, required=False, **attrs)` | Emite `label[for]`, `aria-describedby`, `aria-invalid` |
| `page_header` | `(title, description=None, breadcrumb=None)` con `{% call %}` para acciones | Un solo `h1` |
| `chip_clase` | `(codigo)` | 1–9 o `"SC"` → `.clase-*` + nombre corto |
| `table_wrap` | `(caption)` con `{% call %}` | `role="region"`, `tabindex="0"`, `aria-label` |
| `pagination` | `(page, pages, request)` | Usa `request.url.include_query_params(page=...)` |
| `stepper` | `(pasos, actual)` | — |
| `timeline` | `(items)` | `items = [{fecha, texto, tono}]` |
| `donut` | `(segments, size=140, stroke=16)` | SVG accesible con `<title>` |
| `progress` | `(value, max=100, label=None)` | `role="progressbar"` |

**`app/templates/components/icons.html`**

```jinja
{% macro icon(name, class="w-5 h-5", title=None) -%}
<svg class="{{ class }}" aria-hidden="{{ 'false' if title else 'true' }}" focusable="false">
  {% if title %}<title>{{ title }}</title>{% endif %}
  <use href="/static/icons.svg#i-{{ name }}"></use>
</svg>
{%- endmacro %}
```

Iconos mínimos del sprite: `home, upload, table, clipboard-check, refresh, map, download, alert, check, x, chevron-right, chevron-down, menu, search, filter, calendar, user, file, info`.

**Verificación:** `tests/test_ui_components.py` renderiza cada macro con datos mínimos y comprueba que
el HTML contiene las clases y atributos esperados (ver §13).

### F1.3 Shell

**`app/templates/base.html`** (estructura; el contenido completo se escribe en la tarea):

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}{{ proyecto.nombre if proyecto is defined else "EET" }} · Validador EET{% endblock %}</title>
  <meta name="description" content="Validación de exactitud temática (EET) — AEP">
  <meta name="theme-color" content="#0B3B2E">
  <link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="{{ url_for('static', path='css/app.css') }}">
  {% block head %}{% endblock %}
</head>
<body class="min-h-screen">
  <a href="#contenido" class="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:p-3 focus:bg-white">Saltar al contenido</a>
  {% include "components/shell.html" %}
  <main id="contenido" class="lg:pl-64">
    <div class="mx-auto max-w-[1400px] px-4 lg:px-6 py-6">
      {% set f = flash(request) %}{% if f %}{{ banner(f.tone, f.text) }}{% endif %}
      {% block breadcrumb %}{% endblock %}
      {% block contenido %}{% endblock %}
    </div>
  </main>
  <script src="{{ url_for('static', path='js/app.js') }}" defer></script>
</body>
</html>
```

**`components/shell.html`**: sidebar (248 px, `hidden lg:flex`), topbar con botón menú, drawer
(`<dialog>` nativo o `div` + `aria-expanded`), footer de trazabilidad, marcado `aria-current="page"`.

Menú (orden definitivo):

```jinja
{% set NAV = [
  ("Inicio", [("/", "Resumen", "home")]),
  ("Captura", [("/entregas", "Entregas", "upload"), ("/malla", "Malla oficial", "map"), ("/mapa", "Mapa MADES", "layers")]),
  ("Análisis", [("/cruces", "Cruces de terna", "table"), ("/qaqc", "Fichas QA/QC", "clipboard-check")]),
  ("Cierre", [("/reemplazos", "Reemplazos", "refresh"), ("/exportar", "Exportar", "download")]),
] %}
```

Helper de estado activo:

```jinja
{% set activo = (path == href) or (href != "/" and path.startswith(href)) %}
```

> `path` se inyecta en un `context_processor` manual: en `web.py`, registrar
> `templates.env.globals["path_of"] = lambda request: request.url.path` o pasar `path` desde cada router.
> **Decisión:** usar `request.url.path` directamente dentro de `shell.html`
> (`{% set path = request.url.path %}`) para no tocar routers.

### F1.4 Errores HTML

**`app/templates/error.html`**: código, título humano, mensaje, CTA "Volver" (`history.back()` con
fallback a `/`) y navegación. Sin stack traces.

**`app/main.py`**

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.web import templates

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith(("/salud", "/api")) or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request, "error.html",
        {"codigo": exc.status_code, "mensaje": exc.detail, "proyecto": None},
        status_code=exc.status_code,
    )
```

**Verificación:** `GET /cruces/999999/2000` devuelve HTML con estado 404 y enlace de vuelta;
`GET /salud` sigue devolviendo JSON.

### F1.5 Flash

**`app/web.py`**

```python
FLASH_OK = {
    "entrega_creada":     ("Entrega recibida. Revise la vista previa antes de confirmar.", "info"),
    "entrega_confirmada": ("Entrega confirmada y cruces recalculados.", "ok"),
    "entrega_rechazada":  ("Entrega rechazada. El archivo se conserva como evidencia.", "warn"),
    "ficha_guardada":     ("Dictamen guardado.", "ok"),
    "ficha_transicion":   ("Estado de la ficha actualizado.", "ok"),
    "reemplazo_creado":   ("Reemplazo propuesto registrado.", "ok"),
    "reserva_propuesta":  ("Reserva asignada al punto de origen.", "ok"),
    "reemplazo_estado":   ("Estado del reemplazo actualizado.", "ok"),
    "malla_cargada":      ("Metadatos de la malla actualizados.", "ok"),
    "mapa_cargado":       ("Clases del mapa MADES cargadas.", "ok"),
}
FLASH_ERROR = {
    "archivo_invalido": "El archivo no tiene un formato válido.",
    "duplicado":        "El archivo ya fue cargado (misma huella SHA-256).",
    "clase_requerida":  "Para cerrar por consenso se exige una clase definitiva 1–9.",
}

def _flash(request):
    ok = request.query_params.get("ok")
    if ok in FLASH_OK:
        text, tone = FLASH_OK[ok]
        return {"text": text, "tone": tone}
    err = request.query_params.get("error")
    if err in FLASH_ERROR:
        return {"text": FLASH_ERROR[err], "tone": "danger"}
    return None

templates.env.globals["flash"] = _flash
```

Uso en cada POST:

```python
return RedirectResponse("/entregas?ok=entrega_confirmada", status_code=303)
```

**Regla:** los parámetros se agregan a la URL de destino respetando filtros existentes cuando aplique
(`/cruces?resultado=discrepancia&ok=ficha_transicion`).

**Verificación:** subir una entrega muestra banner verde; la URL con `?ok=inventado` no muestra nada
(no refleja texto arbitrario → sin XSS).

---

## 7. Fase 2 — Dashboard (1 día)

### Cambios de backend

**`app/services/persist.py` → `resumen_dashboard`** (agregar 3 claves, sin romper las existentes):

```python
entregas_pendientes = db.scalar(select(func.count()).select_from(Entrega).where(
    Entrega.proyecto_id == proyecto_id, Entrega.estado == "pendiente")) or 0
tiene_malla = (db.scalar(select(func.count()).select_from(PuntoMalla).where(
    PuntoMalla.proyecto_id == proyecto_id)) or 0) > 0
ultima_mapa = db.scalar(select(func.max(ClaseMapa.cargada_en)).where(
    ClaseMapa.proyecto_id == proyecto_id))
```

Devolver además `entregas_pendientes`, `tiene_malla`, `ultima_mapa`.
No se toca ninguna firma pública ni el cálculo de porcentajes.

### Plantilla

| Bloque | Implementación |
|---|---|
| Encabezado | `page_header` con proyecto, protocolo, período y accción "Nueva entrega" |
| KPIs | 4 `stat`: cruces, evaluaciones aceptadas, fichas abiertas, incompletos |
| Concordancia | `donut([{label:"Unanimidad",value:..,color:"var(--color-emerald-600)"}, ...])` + leyenda con conteo/% |
| Estado del flujo | 5 ítems con `badge` ok/warn + CTA contextual |
| Entregas recientes | `table_wrap` con badge de estado, versión y acción "Revisar" |
| Estado vacío | si `total_puntos == 0`: `empty` con CTA a `/entregas` |

Checklist del flujo (lógica en plantilla, sin backend extra):

1. Malla cargada → `tiene_malla`
2. Mapa MADES cargado → `mapa_puntos > 0`
3. Entregas pendientes de revisión → `entregas_pendientes`
4. Fichas QA/QC abiertas → `pendientes_qaqc`
5. Exportación disponible → `total_puntos > 0`

**Verificación:** con datos semilla, los 4 KPIs coinciden con los valores previos (comparar contra
captura baseline); la dona tiene `<title>` y texto alternativo; sin JS el layout es correcto.

---

## 8. Fase 3 — Entregas y vista previa (2 días)

### Backend

**`app/routers/entregas.py` → `listar`**: aceptar `asistente`, `estado`, `semana`.

```python
q = select(Entrega).where(Entrega.proyecto_id == proyecto.id)
if asistente: q = q.where(Entrega.asistente_letra == asistente)
if estado:    q = q.where(Entrega.estado == estado)
if semana:    q = q.where(Entrega.semana == semana)
```

Conteos por estado (una consulta `group_by`) y lista de semanas distintas para el filtro.

**`preview`**: agregar al contexto `tamano = Path(entrega.archivo_path).stat().st_size` y
`sha_corto = entrega.sha256[:16]`. Sin cambios de lógica.

**POSTs**: agregar `?ok=` a las redirecciones (`entrega_creada`, `entrega_confirmada`, `entrega_rechazada`).

> El rechazo exige motivo: hoy `motivo: str = Form("")`. Mantener el default en backend pero
> marcarlo `required` en HTML y validar en `app.js`; el backend sigue tolerando vacío (no se rompe API).

### Plantillas

`entregas.html`:

- Tarjeta de carga con zona drag & drop (`label for` + `input[type=file]` visualmente oculto),
  texto "`.xlsx` hasta 10 MB · hojas Evaluadora_A/B/C", validación cliente.
- Asistente como 3 radio-cards; semana con `pattern="\d{4}-W\d{2}"` y ayuda; tipo segmentado.
- Tabla con filtros (form GET), badges y "Revisar" solo en pendientes.
- Vacío: `empty("Aún no hay entregas", "...", "Subir la primera", "/entregas")`.

`entrega_preview.html`:

- `stepper(["Archivo", "Validación", "Confirmar"], 2)`.
- Tarjeta "Archivo": nombre, tamaño legible (KB/MB), SHA-256 con botón copiar, fecha, tipo, versión.
- 4 `stat`: detectados, nuevos, actualizados (`n_total - n_nuevos`), errores.
- Errores en tabla (tipo, mensaje) con contador y scroll; si no hay, banner ok "Sin errores de validación".
- Barra sticky inferior con confirmar/rechazar; `app.js` pide confirmación en rechazo y bloquea doble envío.

**Verificación:**
- Subir el mismo archivo dos veces → error de duplicado mostrado como banner en `/entregas` (no JSON).
- Confirmar → banner en `/cruces` y contadores del dashboard actualizados.
- La vista previa muestra el tamaño real del archivo.

---

## 9. Fase 4 — Cruces (2 días)

### Backend

**`persist.listar_cruces`** — nueva firma compatible:

```python
def listar_cruces(db, proyecto_id, resultado=None, punto=None, anio=None,
                  page=1, per_page=50) -> tuple[list, int]:
    cond = [ResultadoCruce.proyecto_id == proyecto_id]
    if resultado: cond.append(ResultadoCruce.resultado == resultado)
    if punto:     cond.append(ResultadoCruce.punto_id == punto)
    if anio:      cond.append(ResultadoCruce.anio == anio)

    total = db.scalar(
        select(func.count()).select_from(ResultadoCruce).where(*cond)
    ) or 0

    filas = db.execute(
        select(ResultadoCruce, FichaQAQC.id)
        .outerjoin(FichaQAQC, (FichaQAQC.proyecto_id == ResultadoCruce.proyecto_id)
                   & (FichaQAQC.punto_id == ResultadoCruce.punto_id)
                   & (FichaQAQC.anio == ResultadoCruce.anio))
        .where(*cond)
        .order_by(ResultadoCruce.punto_id, ResultadoCruce.anio)
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return filas, total
```

> El join con `FichaQAQC` puede duplicar filas si existieran dos fichas para el mismo punto-año.
> Antes de implementar, verificar con `SELECT punto_id, anio, count(*) FROM fichas_qaqc GROUP BY 1,2 HAVING count(*)>1`.
> Si hay duplicados, usar subconsulta `scalar_subquery()` para el id de ficha.

**Nueva función** `conteo_resultados(db, proyecto_id) -> dict[str, int]`.

**`detalle_cruce`**: sin cambios funcionales; el router agrega `return_to` validado:

```python
return_to = request.query_params.get("return_to") or "/cruces"
if not return_to.startswith("/") or return_to.startswith("//"):
    return_to = "/cruces"
```

**Router `listar`**: `page`, `per_page`, `counts`, `total`, `pages`.

### Plantillas

`cruces.html`:

- Filtros: chips con conteo (`Unanimidad (128)`, …) enlazados a `?resultado=…`; búsqueda por punto;
  selector de año (rango disponible calculado o `1990–2024`); botón "Limpiar".
- Tabla: `chip_clase` para A/B/C y clase final; resultado con icono + texto; enlace a detalle con
  `return_to={{ request.url|urlencode }}`.
- `pagination(page, pages, request)`.
- Vista móvil: tarjetas (misma info) bajo `md`.
- Vacío: si no hay filtros → "Sin cruces. Cargue entregas aceptadas" (CTA `/entregas`);
  si hay filtros → "Ningún cruce coincide" + "Limpiar filtros".

`cruce_detalle.html`:

- Breadcrumb + banner de resultado + 3 tarjetas de origen (clase, entrega, semana/versión, hash).
- Ficha lateral con estado y CTA; si no hay, texto informativo.
- Enlace "Volver a cruces" usando `return_to`.

**Verificación:**
- Filtrar por `resultado=discrepancia`, entrar a un punto y volver → el filtro sigue aplicado.
- `?page=2` muestra los siguientes 50 y el contador dice "51–100 de N".
- `?return_to=https://evil.com` se ignora y vuelve a `/cruces`.

---

## 10. Fase 5 — QA/QC (3 días)

Es la fase de mayor riesgo: **hay que conservar el contrato del POST**.

### 10.1 Cambios de backend

**`app/routers/qaqc.py`**

1. `listar`: agregar `conteos` por estado y orden por antigüedad (`FichaQAQC.creada_en.asc()` opción).
2. `detalle`: agregar `cruce` (vía `persist.detalle_cruce`) y `clases = list(ClaseCobertura)` para la leyenda.
3. `actualizar`:
   - **Evidencias múltiples** (compatible hacia atrás):

```python
evidencias = [e.strip() for e in form.getlist("evidencias") if e.strip()]
if "evidencias_present" in form:
    datos["evidencias"] = evidencias          # permite vaciar la lista
```

   - **Sensores vaciables**: si `"sensores_present" in form`, `datos["sensores"] = form.getlist("sensores")`.
   - **Errores en la misma pantalla**: envolver en `try/except ValueError` y re-renderizar la ficha
     con `error=str(exc)` y los valores enviados, en lugar de lanzar `HTTPException`.
   - Éxito → `RedirectResponse(f"/qaqc/{ficha_id}?ok=ficha_guardada", 303)`.
4. `evento`: capturar `ValueError` y redirigir con `?error=clase_requerida` cuando corresponda;
   caso general `?error=transicion_invalida` (agregar al diccionario).

> **No se toca** `persist.actualizar_ficha`, `persist.transicionar_ficha` ni `domain/qaqc.py`.

### 10.2 Plantilla `qaqc_detalle.html`

Estructura de dos columnas (`lg:grid-cols-3`): formulario en `lg:col-span-2`, rail en 1.

**Secciones del formulario**

| # | Sección | Campos (`name` intactos) |
|---|---|---|
| 1 | Contexto | solo lectura: punto, año, resultado, `chip_clase` de A/B/C |
| 2 | Fundamento | `debate` (textarea con contador) |
| 3 | Dictamen | `dictamen` (radio-cards consenso / sin_consenso) |
| 4 | Clase definitiva | `clase_definitiva` (9 radio-cards con `chip_clase` y nombre) |
| 5 | Sensores (4.12.1) | `sensores[]` + `sensores_present` |
| 6 | Causa de descarte | `causa_descarte` (radio-cards, visible solo si dictamen = sin_consenso) |
| 7 | Evidencias | filas `evidencias[]` + `evidencias_present`, botón "Agregar evidencia" |
| 8 | Sesión y firmas | `fecha_sesion`, `firma_a`, `firma_b`, `firma_c` |

**Rail**

- Resumen del cruce + enlace al origen.
- Checklist dinámico (Jinja): falta clase / falta fecha / faltan firmas.
- Timeline de estados (usa `creada_en` y `actualizada_en`; sin nueva tabla).
- Referencias del protocolo en texto plano.

**Acciones**

- Barra sticky `bottom-0` con "Guardar dictamen" (primario) y aviso "cambios sin guardar".
- Panel "Cerrar ficha" separado, con descripción del efecto de cada transición y confirmación
  para `Cerrar sin consenso`.

**Leyenda de clases:** panel colapsable con las 9 clases, color + código + nombre, generado
desde `ClaseCobertura` (mismo orden que `_CLASES`: código = índice + 1).

### 10.3 Plantilla `qaqc_list.html`

- Chips de estado con conteo.
- Columnas: ficha, punto/año, terna inicial como `chip_clase` (parsear `valores_iniciales` si es dict;
  si es string, mostrar tal cual), estado, dictamen, antigüedad (`creada_en` → "hace N días"), acción.
- Vacío explicativo + CTA a `/cruces`.

**Verificación (obligatoria antes de cerrar la fase):**

1. Enviar el formulario con todos los campos → 303 + banner; la BD conserva los 9 valores.
2. Enviar sin clase con dictamen consenso → mensaje en pantalla, sin perder lo escrito.
3. Agregar 3 evidencias y guardar → `ficha.evidencias` con 3 elementos (probar con `psql` o script).
4. Vaciar todas las evidencias → lista vacía (no se conservan las anteriores).
5. Pulsar "Cerrar por consenso" sin clase → banner de error, estado sin cambios.
6. Cerrar por consenso con clase → `cruce.clase_consolidada` actualizada y `regla_version="qaqc-consenso"`.

---

## 11. Fase 6 — Resto de vistas (2 días)

### 11.1 Reemplazos

- Formulario: punto (input numérico con `list` de puntos si se expone) + causas como radio-cards
  con icono y descripción (N/B/T) + nota de regla 4.7.
- Tabla: badge de estado, antigüedad, acciones como `btn--sm`; "proponer reserva" solo cuando aplique.
- Opcional (si sobra tiempo): vista kanban de 3 columnas, sin backend nuevo.
- POSTs con `?ok=` (`reemplazo_creado`, `reserva_propuesta`, `reemplazo_estado`).

### 11.2 Malla

**Backend** (`routers/malla.py` + `persist`): `q: str | None`, `page`, `per_page=50`.
Filtro: si `q` es dígito → `punto_id == int(q)`; si no → `estrato.ilike(f"%{q}%")`.
Respuesta con `total`, `pages`, `muestra` de la página.

**Plantilla:** KPIs, tarjeta de carga con esquema de columnas y enlace a
`/static/plantillas/malla.csv` (archivo de ejemplo nuevo, 3 filas), tabla con búsqueda,
paginación, badges "reserva"/"inactivo".

### 11.3 Mapa

- Estado actual + última carga (`ultima_mapa` ya calculada en F2).
- Dos pestañas de formato con ejemplo de encabezados (sin JS: dos `<details>` o radios + CSS).
- Banner de bloqueo si no hay cruces: "La matriz de confusión (5.1) requiere cruces y mapa cargados".

### 11.4 Exportar

- Tarjeta con alcance: nº de cruces, hojas `Consolidado` y `Ternas`, columnas A1990–A2024.
- Botón primario con estado "Generando…" al enviar (JS) y texto de auditoría.
- Nota: el endpoint no cambia.

**Verificación:** las 4 rutas mantienen sus POST originales; subir CSV/XLSX funciona igual;
la paginación de malla no rompe el límite de 100 (ahora configurable).

---

## 12. Fase 7 — Calidad (2 días)

| ID | Tarea | Criterio |
|---|---|---|
| F7.1 | Auditoría de contraste | Todos los pares texto/fondo ≥ 4.5:1 (3:1 en componentes). Revisar `ink-400` sobre blanco |
| F7.2 | Teclado | Recorrer 11 vistas solo con `Tab`/`Enter`/`Esc`; drawer cierra con `Esc`; foco vuelve al botón |
| F7.3 | Responsive | 360, 768, 1024, 1440: sin scroll horizontal de página; tablas con scroll propio |
| F7.4 | Zoom 200% | Sin pérdida de contenido ni solapamientos |
| F7.5 | Reduced motion | `@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important } }` |
| F7.6 | Lighthouse | Accessibility ≥ 95 y Performance ≥ 90 en `/` y `/qaqc/{id}` |
| F7.7 | Semántica | Un `h1` por página, `scope` en th, `caption`, landmarks, `lang="es"` |
| F7.8 | Estados | Verificar los 4 estados por pantalla: normal, vacío, error, carga |

**Herramienta opcional:** `npx @axe-core/cli http://localhost:8000/` (requiere Chrome). Si no está
disponible, documentar la revisión manual en `docs/qa-accesibilidad.md`.

---

## 13. Pruebas

### 13.1 Sin base de datos (rápidas, siempre en CI)

**`tests/test_ui_components.py`**

```python
from jinja2 import Environment, FileSystemLoader, select_autoescape

env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)

def _render(path, macro, **ctx):
    tpl = env.from_string("{% import '" + path + "' as ui %}{{ ui." + macro + "(**ctx) }}")
    return tpl.render(**ctx)

def test_badge_tone_danger():
    html = _render("components/ui.html", "badge", text="Discrepancia", tone="danger")
    assert "badge--danger" in html and "Discrepancia" in html

def test_chip_clase_9():
    html = _render("components/ui.html", "chip_clase", codigo=9)
    assert "clase-9" in html and "Otras tierras" in html

def test_chip_clase_sc():
    html = _render("components/ui.html", "chip_clase", codigo="SC")
    assert "SC" in html
```

Cobertura mínima: `badge`, `chip_clase` (1, 9, SC), `btn` (href vs button), `field` (label/for,
`aria-invalid`), `banner` (`role`), `donut` (`<title>`), `pagination` (preserva query).

**`tests/test_flash_messages.py`**: cada código produce texto y tono; código desconocido → `None`;
ningún texto contiene HTML.

### 13.2 Integración (requiere Postgres del compose)

**`tests/test_web_routes.py`**

```python
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c

@pytest.mark.parametrize("ruta", ["/", "/entregas", "/cruces", "/qaqc", "/reemplazos",
                                  "/malla", "/mapa", "/exportar"])
def test_rutas_200(client, ruta):
    r = client.get(ruta)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]

def test_error_404_es_html(client):
    r = client.get("/cruces/999999/2000")
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]

def test_return_to_invalido_se_ignora(client):
    r = client.get("/cruces/1/2000?return_to=https://evil.com")
    assert b"evil.com" not in r.content
```

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = ["integration: requiere PostgreSQL (docker compose up -d)"]
addopts = "-m 'not integration'"
```

Ejecución completa: `uv run pytest -m integration -q`.

### 13.3 Pruebas de contrato de formularios

Para QA/QC (el más crítico), un test de integración que:

1. Cree una ficha semilla (o use la del seed).
2. Envíe `POST /qaqc/{id}` con `data=[("evidencias","a"),("evidencias","b"),("evidencias_present","1"), ...]`.
3. Verifique redirección 303 y que `GET /qaqc/{id}` contenga "a" y "b".

Esto protege exactamente el punto donde el rediseño puede romper el backend.

### 13.4 Regresión visual

Sin herramienta automatizada: capturas "después" con el mismo ancho que el baseline y revisión lado a lado
con la supervisión. Guardar en `docs/baseline/after/`.

---

## 14. Docker y despliegue

### 14.1 Multi-stage

```dockerfile
FROM node:22-alpine AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY app/static/css/input.css ./app/static/css/input.css
COPY app/templates ./app/templates
COPY app/static/js ./app/static/js
RUN npm run css:build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache/uv
COPY app ./app
COPY --from=css /build/app/static/css/app.css ./app/static/css/app.css
RUN mkdir -p uploads
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "--frozen", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 14.2 `.dockerignore` (agregar)

```
node_modules/
app/static/css/app.css
docs/
tests/
```

> `app/static/css/app.css` se excluye del contexto porque se genera en la etapa `css`.

### 14.3 Flujo de desarrollo

```powershell
npm install
npm run css:watch      # terminal 1
uv run uvicorn app.main:app --reload   # terminal 2
```

### 14.4 Checklist de despliegue

- [ ] `docker compose build --no-cache app`
- [ ] `docker compose up -d`
- [ ] `curl http://localhost:8000/salud` → `{"estado":"ok"}`
- [ ] Revisar que `/` cargue `app.css` con 200 y `content-type: text/css`
- [ ] Confirmar que no existe ninguna referencia a `cdn.tailwindcss.com` (`Select-String -Path app/templates/*.html -Pattern "cdn.tailwind"`)

---

## 15. Cronograma y secuencia de commits

| Semana | Días | Entregable |
|---|---|---|
| 1 | 1–4 | F0 + F1 (fundaciones, shell, errores, flash) |
| 2 | 5–9 | F2 (dashboard) + F3 (entregas) + inicio F4 |
| 3 | 10–14 | F4 (cruces) + F5 (QA/QC) |
| 4 | 15–17 | F6 (resto) + F7 (calidad) + cierre |

### Secuencia de commits sugerida

```
chore: baseline antes de modernizacion UX/UI
build: tailwind v4 compilado y tokens de diseno
feat(ui): macros de componentes y sprite de iconos
feat(ui): shell con sidebar, drawer y pagina de error
feat(ui): feedback por codigos de estado (flash)
feat(dashboard): KPIs, dona de concordancia y estado del flujo
feat(entregas): dropzone, filtros y preview con stepper
feat(cruces): conteos, paginacion y conservacion de filtros
feat(qaqc): lista con conteos y detalle en dos columnas
fix(qaqc): evidencias multiples compatibles con el POST actual
feat(reemplazos): estados y acciones como botones
feat(malla): busqueda y paginacion
feat(mapa): estado de carga y formatos
feat(exportar): alcance y estado de descarga
test(ui): componentes, flash y rutas HTML
docs: plan de QA de accesibilidad
chore(docker): build de CSS en etapa multi-stage
```

Cada commit debe dejar la app arrancando y `/` renderizando.

### Dependencias entre tareas

```
F0 ──► F1.1 ──► F1.2 ──► F1.3 ──► F1.4 ──► F1.5
                                   │
        ┌──────────────┬───────────┼───────────┬──────────────┐
        ▼              ▼           ▼           ▼              ▼
     F2 Dashboard   F3 Entregas  F4 Cruces  F5 QA/QC      F6 Resto
        └──────────────┴───────────┴───────────┴──────────────┘
                                   ▼
                              F7 Calidad ──► cierre
```

F2–F6 pueden paralelizarse entre 2 desarrolladores una vez cerrada F1
(F5 no debe paralelizarse con F4 si comparten `persist.py`).

---

## 16. Riesgos y reversión

| Riesgo | Prob. | Impacto | Mitigación | Reversión |
|---|---|---|---|---|
| El rediseño del formulario rompe el POST de QA/QC | Media | Alto | Mantener `name=`, test de contrato §13.3, revisar router antes de tocar HTML | `git revert` del commit `feat(qaqc)` |
| Versión de Tailwind cambia y rompe el build | Baja | Medio | Versión fijada + lockfile | Volver a la versión anterior del lockfile |
| El CSS compilado no incluye clases dinámicas | Media | Medio | `@source` explícito y evitar clases construidas con concatenación en Jinja (usar mapa en Python/macro) | Agregar la clase al safelist o al macro |
| Migración de plantilla altera textos normativos | Media | Alto | Diff de textos contra baseline; revisión de supervisión | Revertir el archivo |
| `listar_cruces` con join duplica filas | Media | Medio | Verificar duplicados de fichas antes; usar subconsulta si aplica | Revertir solo `persist.listar_cruces` |
| Node no disponible en el servidor de build | Baja | Medio | Etapa Docker dedicada; alternativa: commitear `app.css` | Publicar imagen con CSS precompilado |
| Sin Git, se pierde el baseline | Baja | Alto | F0.1 obligatorio antes de cualquier edición | Restaurar copia de `docs/baseline` |

**Estrategia general de reversión:** una fase = uno o más commits independientes; las plantillas no
comparten estado, por lo que revertir una pantalla no afecta a las demás. `persist.py` se toca solo en
F2, F4 y F6.1, con commits separados por función.

---

## 17. Checklist final de aceptación

**Visual y consistencia**
- [ ] Ninguna plantilla usa `cdn.tailwindcss.com`.
- [ ] No hay colores hardcodeados fuera de `input.css` (buscar `bg-emerald-`, `bg-slate-800`, `text-sky-`).
- [ ] Los 4 estados (ok/warn/danger/info) se ven iguales en todas las vistas.
- [ ] Un solo estilo de tarjeta, botón y tabla.

**Funcional**
- [ ] Toda acción POST muestra banner de éxito o error en pantalla.
- [ ] Los filtros sobreviven al ir y volver de un detalle.
- [ ] Todas las tablas muestran "X–Y de N" y paginan.
- [ ] La ficha QA/QC no imprime JSON crudo.
- [ ] Se pueden agregar y vaciar evidencias y sensores.

**Accesibilidad**
- [ ] Skip link funciona en las 11 vistas.
- [ ] Foco visible en todos los interactivos.
- [ ] Contraste AA verificado.
- [ ] Tablas con `caption`, `scope` y `aria-sort`.
- [ ] Sin errores de consola; sin dependencia de JS para leer datos.

**Responsive**
- [ ] Sin scroll horizontal de página a 360 px.
- [ ] Drawer abre/cierra con teclado.
- [ ] Acciones primarias visibles sin scroll horizontal.

**Técnico**
- [ ] `uv run pytest -q` en verde (unitarias).
- [ ] `uv run pytest -m integration -q` en verde con Postgres.
- [ ] `docker compose build` produce CSS en la imagen.
- [ ] `app/static/css/app.css` > 10 KB y < 60 KB.
- [ ] `docs/qa-accesibilidad.md` completado.
