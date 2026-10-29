# Plan de modernización UX/UI — Validador EET (AEP)

> Documento de trabajo. Base: análisis del código en `app/` (FastAPI + Jinja2 + Tailwind CDN).
> Objetivo: pasar de un MVP funcional a una herramienta operativa moderna, clara y accesible,
> sin alterar la lógica de negocio ni la trazabilidad de tres capas.

---

## 1. Diagnóstico del proyecto

### 1.1 Qué es el sistema

Aplicación web interna para la **Evaluación de Exactitud Temática (EET)** que ejecuta la AEP sobre
los mapas del MADES. El flujo real es:

1. Tres asistentes (A/B/C) fotointerpretan y entregan un Excel semanal.
2. Un responsable carga, valida y confirma cada entrega (se conserva el archivo + SHA-256).
3. El sistema cruza la terna por punto-año (unanimidad / mayoría / discrepancia / incompleto).
4. La supervisión resuelve discrepancias absolutas con fichas QA/QC.
5. Se gestionan reemplazos de puntos, se carga el mapa MADES y se exporta el consolidado.

La regla de oro es de **trazabilidad**: interpretación original ≠ cruce calculado ≠ dictamen.
La UI debe hacer visible esa separación en todo momento.

### 1.2 Stack y estructura actual

| Capa | Implementación |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0, Pydantic Settings |
| Plantillas | Jinja2 (`app/templates`, 10 archivos, `base.html` + 9 vistas) |
| Estilos | Tailwind **vía CDN** (`cdn.tailwindcss.com`), clases inline en cada plantilla |
| Assets | `app/static/` existe pero **está vacío** (sin CSS, sin JS, sin iconos) |
| Gráficos | Ninguno (solo números y bloques de color) |
| Interacción | Recarga completa de página; sin feedback de éxito/error |
| Datos | PostgreSQL + PostGIS (`puntos_malla.geom`), `ResultadoCruce`, `FichaQAQC`, `Reemplazo`, `ClaseMapa` |

### 1.3 Inventario de pantallas

| Ruta | Plantilla | Propósito | Estado UX |
|---|---|---|---|
| `/` | `dashboard.html` | KPIs de terna y últimas entregas | Aceptable, sin visualización ni guía |
| `/entregas` | `entregas.html` | Subida y listado de entregas | Formulario plano, sin dropzone ni filtros |
| `/entregas/{id}/preview` | `entrega_preview.html` | Validación previa a confirmar | Sin stepper, sin contexto del archivo |
| `/cruces` | `cruces.html` | Tabla comparativa A/B/C | Tabla densa, sin orden, sin conteos |
| `/cruces/{p}/{a}` | `cruce_detalle.html` | Origen + ficha asociada | Correcta pero pobre jerarquía |
| `/qaqc` | `qaqc_list.html` | Bandeja de fichas | Sin priorización ni edad |
| `/qaqc/{id}` | `qaqc_detalle.html` | Formulario de dictamen | El punto más débil: JSON crudo, sin leyenda de clases |
| `/reemplazos` | `reemplazos.html` | Ciclo propuesto→autorizado→completado | Acciones como enlaces de texto |
| `/malla` | `malla.html` | Carga de metadatos muestrales | Solo primeros 100, sin búsqueda |
| `/mapa` | `mapa.html` | Carga de clases MADES | Solo un formulario |
| `/exportar` | `exportar.html` | Descarga del consolidado | Minimalista, sin previsualización |

### 1.4 Hallazgos priorizados

**Críticos (rompen la experiencia)**

1. **Errores de negocio muestran JSON crudo.** Todos los routers lanzan `HTTPException(400, detail=...)`;
   al enviar un formulario el usuario ve `{"detail":"..."}` en pantalla, sin navegación ni contexto.
2. **No hay feedback tras una acción.** Los POST redirigen en silencio: subir, confirmar, rechazar,
   guardar dictamen o transicionar no confirman nada. El usuario no sabe si funcionó.
3. **Tailwind por CDN.** No es apto para producción: compila en el navegador, sin tokens, sin purga,
   sin control de versiones y dependiente de internet. Además impide usar `@apply`/tema propio.
4. **Cero componentes reutilizables.** Tarjetas, badges, tablas y formularios se repiten copiados en
   10 plantillas. Cualquier cambio visual exige editar todos los archivos y ya hay divergencias
   (botones `emerald-700` vs `slate-800`, tarjetas `shadow` vs `shadow-sm`).

**Altos (fricción diaria)**

5. **Navegación plana y no responsive.** 8 enlaces en una fila sin estado activo, sin agrupación,
   sin iconos. En móvil desborda.
6. **Filtros que se pierden.** Al abrir un cruce y volver, se pierden resultado/punto/año; igual en QA/QC.
7. **Tablas sin herramientas.** Sin orden, sin paginación real (límites silenciosos: 500 cruces, 100 puntos),
   sin scroll horizontal controlado en móvil, sin contadores por estado.
8. **Ficha QA/QC poco usable.** `valores_iniciales` se imprime como dict Python (`{'A': 3, ...}`),
   la clase se elige con un `<input type=number>`, no hay leyenda de las 9 clases, ni campos obligatorios
   visibles, ni resumen del cruce al lado del formulario.
9. **Dashboard no guía.** Métricas sin comparación visual, sin "qué hacer ahora" y sin estado del flujo
   (malla/mapa cargados, entregas pendientes, fichas abiertas).
10. **Sin estados vacíos accionables.** Varios mensajes dicen "Sin X" sin un CTA que resuelva.

**Medios (calidad y accesibilidad)**

11. Contraste insuficiente en textos `slate-400` sobre blanco y en badges claros.
12. Sin `focus-visible`, sin skip link, sin `aria-live`, tablas sin `scope`/`caption`, labels sin `for`.
13. Significado transmitido solo por color (verde/rojo/ámbar) sin texto o icono redundante.
14. Sin metadatos (favicon, description), sin tipografía propia, sin números tabulares en métricas.
15. Sin estados de carga en formularios largos (doble submit posible).
16. `preview` y `malla` no muestran el archivo (tamaño, hash, fecha, nombre) ni validan en cliente.

---

## 2. Dirección de diseño

### 2.1 Principios

1. **Institucional y sobrio, no genérico.** Verde forestal profundo como marca AEP, neutros fríos,
   acento ámbar para "pendiente" y rojo para "discrepancia". Nada de gradientes ni glassmorphism.
2. **Densidad con aire.** Es una herramienta de datos: tablas legibles, 8pt de espaciado, jerarquía
   tipográfica clara, mucho menos ruido de bordes y sombras.
3. **El flujo se ve.** Cada pantalla responde: ¿dónde estoy?, ¿qué falta?, ¿cuál es la siguiente acción?
4. **La trazabilidad es visual.** Las tres capas (interpretación / cruce / dictamen) se distinguen
   con color, etiqueta y microcopy constante.
5. **Accesible por defecto.** WCAG 2.2 AA como criterio de aceptación, no como fase final.

### 2.2 Identidad visual

| Token | Valor | Uso |
|---|---|---|
| `--brand-900` | `#0B3B2E` | Sidebar, cabeceras institucionales |
| `--brand-700` | `#14624A` | Botón primario, enlaces |
| `--brand-500` | `#1E8A69` | Acentos, foco |
| `--brand-50` | `#EAF5F0` | Fondos de selección suave |
| `--ink-900` | `#0F172A` | Texto principal |
| `--ink-600` | `#475569` | Texto secundario |
| `--ink-400` | `#64748B` | Metadatos (mínimo AA sobre blanco) |
| `--surface` | `#FFFFFF` | Tarjetas |
| `--canvas` | `#F4F7F6` | Fondo de app |
| `--line` | `#E2E8E7` | Bordes 1px |
| `--warn` | `#B45309` / `#FEF3C7` | Pendiente |
| `--danger` | `#B91C1C` / `#FEE2E2` | Discrepancia / rechazo |
| `--info` | `#0369A1` / `#E0F2FE` | Informativo |
| `--ok` | `#047857` / `#D1FAE5` | Aceptada / consenso |

- **Tipografía:** `Inter` self-hosted (woff2, 400/500/600/700) con fallback a system-ui.
  Métricas con `font-variant-numeric: tabular-nums`.
- **Escala:** 12 / 13 / 14 / 16 / 20 / 24 / 30 px. Interlineado 1.5 en cuerpo, 1.2 en títulos.
- **Radios:** 8px inputs, 12px tarjetas, 999px chips.
- **Sombras:** una sola elevación (`0 1px 2px rgba(15,23,42,.06)`), sin `shadow-lg` decorativos.
- **Espaciado:** múltiplos de 4; contenedor `max-w-[1400px]`, gutter 24px desktop / 16px móvil.
- **Iconografía:** sprite SVG propio (`app/static/icons.svg`) con ~18 iconos outline 24px.
  Sin librería externa.

### 2.3 Arquitectura de información

Sidebar agrupado por el ciclo de trabajo (reemplaza la barra plana):

```
INICIO
  Resumen

CAPTURA
  Entregas
  Malla oficial
  Mapa MADES

ANÁLISIS
  Cruces de terna
  Fichas QA/QC

CIERRE
  Reemplazos
  Exportar
```

- Sidebar 248px fijo en ≥1024px, colapsable a 72px (solo iconos).
- En móvil: topbar + drawer lateral con overlay y cierre con `Esc`.
- Topbar: breadcrumb, selector de proyecto (hoy único), chip de rol (informativo), menú de usuario (futuro).
- Estado activo con fondo `brand-50`, barra izquierda 3px y `aria-current="page"`.

---

## 3. Sistema de componentes (Jinja macros)

Crear `app/templates/components/ui.html` y `.../icons.html`, importados con `{% from ... import ... %}`.
Esto elimina la duplicación y estandariza el look sin tocar la lógica.

```jinja
{# app/templates/components/ui.html #}
{% macro badge(text, tone="neutral") -%}
  <span class="badge badge--{{ tone }}">{{ text }}</span>
{%- endmacro %}

{% macro stat(label, value, hint=None, tone="neutral") -%}
  <div class="stat stat--{{ tone }}">
    <p class="stat__label">{{ label }}</p>
    <p class="stat__value">{{ value }}</p>
    {% if hint %}<p class="stat__hint">{{ hint }}</p>{% endif %}
  </div>
{%- endmacro %}

{% macro card(title=None, subtitle=None, class="") -%}
  <section class="card {{ class }}">
    {% if title %}
      <header class="card__header">
        <h2 class="card__title">{{ title }}</h2>
        {% if subtitle %}<p class="card__subtitle">{{ subtitle }}</p>{% endif %}
      </header>
    {% endif %}
    <div class="card__body">{{ caller() }}</div>
  </section>
{%- endmacro %}

{% macro empty(title, text, action_label=None, action_href=None) -%}
  <div class="empty">
    <p class="empty__title">{{ title }}</p>
    <p class="empty__text">{{ text }}</p>
    {% if action_label %}
      <a class="btn btn--primary" href="{{ action_href }}">{{ action_label }}</a>
    {% endif %}
  </div>
{%- endmacro %}
```

Componentes mínimos:

| Componente | Variantes |
|---|---|
| `btn` | primary, secondary, ghost, danger, sm, icon-only |
| `badge` | ok, warn, danger, info, neutral |
| `card` | con/sin header, con acciones |
| `stat` | con tendencia y hint |
| `table` | wrapper con scroll, sticky header, `scope`, caption |
| `empty` | con CTA |
| `field` | label + hint + error + required |
| `chips` | filtro segmentado con contador |
| `banner` | success, warning, danger, info (incluye `role="status"`) |
| `stepper` | pasos con estado |
| `chip-clase` | código 1–9 + nombre corto |
| `timeline` | historial de estados |
| `breadcrumb` | navegación de contexto |
| `page_header` | h1 + descripción + acciones |

Regla: **las plantillas no escriben clases de color ni sombras sueltas**; solo componentes y utilidades
de layout (`grid`, `flex`, `gap`). Esto se verifica en revisión.

---

## 4. Cambios por pantalla

### 4.1 Shell (`base.html`)

- Skip link "Saltar al contenido".
- `lang="es"`, `meta description`, favicon, `theme-color`, fuentes self-hosted con `preload`.
- CSS local `app/static/css/app.css` (compilado) — **se elimina el CDN**.
- Sidebar + topbar + `<main id="contenido">` + footer de trazabilidad.
- Contenedor de toasts con `aria-live="polite"` alimentado por el servidor (ver 5.3).
- Bloque `{% block title %}`, `{% block breadcrumb %}`, `{% block actions %}`.

### 4.2 Dashboard

- `page_header`: nombre del proyecto, protocolo, período, botón primario "Nueva entrega".
- 4 KPI cards con icono, hint y tendencia textual (sin inventar series históricas).
- **Concordancia de la terna**: dona SVG accesible (`role="img"` + `<title>`) con centro "ternas
  completas", y leyenda con conteo, porcentaje y barra. Nunca solo color: código + etiqueta.
- **Estado del flujo**: checklist accionable (malla cargada · mapa cargado · entregas pendientes ·
  fichas abiertas · exportación disponible) con CTA por ítem. Sustituye el párrafo condicional actual.
- **Actividad reciente**: tabla de entregas con badge de estado, versión, fecha relativa
  ("hace 2 h") y acción "Revisar".

### 4.3 Entregas

- Zona de carga con **drag & drop** + selector, validación cliente (extensión, tamaño ≤ 10 MB) y
  texto de formato esperado; el submit se deshabilita y muestra spinner.
- Campos agrupados: archivo, asistente (radio A/B/C tipo tarjetas), semana (con máscara `2026-W41` y
  validación ISO), tipo (segmented). Ayuda contextual bajo cada campo.
- Tabla con filtros (asistente, semana, estado), contador por estado y `aria-sort`.
- Móvil: cada entrega como tarjeta (ID, semana, asistente, estado, acción).

### 4.4 Vista previa de entrega

- **Stepper** `1 Archivo → 2 Validación → 3 Confirmar`.
- Tarjeta "Archivo": nombre, tamaño, SHA-256 truncado con copiar, fecha, quién cargó.
- Resumen: cruces detectados / nuevos / actualizados / errores, con iconos y color+texto.
- Errores en tabla (tipo, punto, año, columna, mensaje) con filtro y contador; hoy es una lista de `<li>`.
- Acciones en barra inferior sticky: "Confirmar y cruzar" (primario) y "Rechazar" (danger, con motivo
  obligatorio y confirmación). Indicar explícitamente que el archivo se conserva como evidencia.

### 4.5 Cruces

- Filtros como chips de resultado con conteos (`Unanimidad 128`, `Mayoría 40`, ...) + búsqueda por punto
  y selector de año. Botón "Limpiar filtros" cuando haya alguno activo.
- Tabla: terna A/B/C como `chip-clase` con color por clase; resultado con icono; clase final destacada.
- Cabecera sticky, `scope="col"`, orden por punto/año/resultado.
- Contador "mostrando X de Y" y paginación (25/50/100) en lugar del límite silencioso de 500.
- Responsive: tarjetas con la misma información bajo 768px.
- El detalle conserva el `return_to` con los filtros aplicados.

### 4.6 Detalle de cruce

- Breadcrumb `Cruces / Punto 123 / 2007`.
- Banner de resultado (tono según resultado) con clase consolidada y regla aplicada.
- Tres tarjetas A/B/C en grid: clase, entrega, semana/versión, hash corto, enlace.
- Si hay ficha: tarjeta lateral con estado, dictamen y CTA "Abrir ficha".

### 4.7 Fichas QA/QC (lista)

- Chips de estado con conteo; orden por antigüedad (SLA visual: "abierta hace 6 días").
- Tarjetas en móvil, tabla en desktop. Columnas: ficha, punto/año, terna inicial (chips), estado,
  dictamen, antigüedad, acción.
- Estado vacío explicativo: "Se crean automáticamente ante ternas con 3 clases distintas" + enlace a cruces.

### 4.8 Ficha QA/QC (detalle) — rediseño principal

Layout de dos columnas (main 2/3, rail 1/3). En móvil, el rail pasa arriba como resumen colapsable.

**Main — formulario por secciones numeradas**

1. **Contexto del cruce**: punto, año, resultado, terna inicial mostrada como chips legibles
   (reemplaza `{{ ficha.valores_iniciales }}` crudo). Edad y estado.
2. **Fundamento técnico**: textarea con contador y guía.
3. **Dictamen**: radios tipo tarjeta (`Consenso técnico` / `Sin consenso`), con consecuencia descrita.
4. **Clase definitiva**: grid de 9 radios con muestrario de color + nombre completo (leyenda visible,
   no un `number`). Solo habilitado si el dictamen es consenso; si no, se explica por qué.
5. **Sensores evaluados** (4.12.1): checkboxes tipo tarjeta con icono.
6. **Causa de descarte** (solo aplica a SC): radio cards con las dos causas.
7. **Evidencias**: lista editable (agregar/eliminar filas), no un textarea que se parsea por líneas.
8. **Sesión y firmas**: fecha + 3 firmas en grid.

**Rail**

- Resumen del cruce, enlace a origen.
- Checklist "Para cerrar la ficha falta: clase definitiva, fecha, firmas".
- Historial de transiciones (timeline con fecha).
- Ayuda del protocolo (referencias 4.12.1, 5.1).

**Acciones**

- Barra sticky inferior: "Guardar borrador" (secundario) / "Guardar dictamen" (primario), con indicador
  "cambios sin guardar" y bloqueo de doble envío.
- Panel separado "Transiciones de estado" con botones que explican el efecto
  (`En revisión`, `Cerrar por consenso`, `Cerrar sin consenso`) y confirmación para las irreversibles.
- Validación en servidor devuelta **en el mismo formulario** con resumen de errores enlazado a cada campo
  (`role="alert"` + foco en el primer error).

### 4.9 Reemplazos

- Formulario "Reportar píxel inválido": punto con autocompletado desde la malla, causa como radio cards
  con icono (N nubosidad / B bandeado / T transición oculta) y nota de la regla 4.7.
- Tabla con badges de estado y acciones como botones reales, no enlaces subrayados.
- Opción de vista **kanban** de 3 columnas (propuesto / autorizado / completado) como mejora opcional.

### 4.10 Malla

- KPIs (total, reservas, con peso) + tarjeta de carga con esquema de columnas esperadas y descarga de
  plantilla CSV.
- Tabla con búsqueda por punto/estrato, paginación real, badge "reserva" y "activo".
- Estado vacío con CTA.

### 4.11 Mapa MADES

- Estado actual (registros, puntos, última carga, fuente XLSX/CSV).
- Dos pestañas de formato con ejemplo visual de columnas (`ID_PUNTO + A1990…A2024` vs `punto,anio,clase`).
- Bloqueo explicado: "La matriz de confusión se habilita al cargar el mapa" con enlace a Cruces.
- (Opcional, fuera de alcance inicial) vista de mapa con PostGIS + teselas.

### 4.12 Exportar

- Tarjeta con alcance de la exportación: nº de cruces, hojas incluidas (Consolidado, Ternas), columnas.
- Casillas opcionales (incluir ternas, incluir fichas) si el backend las soporta después.
- Botón primario de descarga con estado de carga y nota de auditoría.

---

## 5. Cambios técnicos habilitantes

### 5.1 Build de CSS (eliminar CDN)

Opción recomendada — **Tailwind v4 con CLI standalone** (sin Node en el proyecto):

```
app/static/css/input.css      ->  @import "tailwindcss"; + @theme + @layer components
app/static/css/app.css        <-  salida generada (versionada)
scripts/build-css.ps1         ->  tailwindcss -i ... -o ... --minify
```

- Tokens de color/tipografía en `@theme`.
- Componentes en `@layer components` (`.btn`, `.card`, `.badge`, `.stat`, `.field`, `.table`).
- En desarrollo: script `--watch`. En Docker: etapa de build que compila y copia el CSS.
- Alternativa si no se quiere toolchain: CSS plano con variables + `@layer` (~300 líneas) y utilidades
  mínimas propias. Menos potente, cero dependencias.

### 5.2 Handlers de error y feedback

```python
# app/main.py
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    if request.url.path.startswith(("/salud", "/api")):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request, "error.html",
        {"codigo": exc.status_code, "mensaje": exc.detail, "proyecto": None},
        status_code=exc.status_code,
    )
```

- `error.html`: código, mensaje humano, CTA "Volver", navegación disponible.
- Evitar `detail` técnicos crudos; mapear a mensajes en español.

### 5.3 Mensajes flash sin dependencias nuevas

Redirecciones con códigos semánticos: `/entregas?ok=entrega_confirmada`,
`/qaqc/12?ok=dictamen_guardado`, `?error=clase_requerida`. Un diccionario en el backend traduce el
código a texto + tono y `base.html` renderiza el `banner` con `role="status"` y autocierre.
Ventaja: sin sesiones ni cookies, sin XSS, testeable.

### 5.4 Filtros y contexto preservado

- Incluir `return_to` (URL relativa validada) en los enlaces de detalle.
- En formularios de filtro, `method="get"` + `hx-get` opcional en fase 3.

### 5.5 Progresistas (fase 6, opcional)

- **HTMX** (14 KB vendorizado) para: filtros sin recarga, guardado de campos con debounce,
  transiciones de estado y toasts.
- `app/static/js/app.js`: dropzone, validación de formularios, confirmaciones, copiar hash,
  indicador de cambios sin guardar, drawer móvil. Sin frameworks.
- Gráficos: SVG generado en Jinja (dona/barras) para no añadir dependencias. Chart.js solo si se
  agregan series temporales.

### 5.6 Accesibilidad (criterios WCAG 2.2 AA)

- Contraste ≥ 4.5:1 en texto normal y ≥ 3:1 en componentes; revisar `slate-400` y badges claros.
- Foco visible (`outline: 2px solid var(--brand-500); offset: 2px`) en todo interactivo.
- Objetivos táctiles ≥ 44×44 px.
- Formularios: `<label for>`, `aria-describedby` para ayudas/errores, `aria-invalid`, resumen de errores.
- Tablas: `<caption>` (puede ser `sr-only`), `scope`, `aria-sort`, contenedor con `tabindex="0"` y
  `role="region"` + `aria-label` para scroll por teclado.
- Estado nunca solo por color: icono + texto.
- `prefers-reduced-motion` respetado; sin animaciones esenciales.
- Un solo `h1` por página, jerarquía sin saltos, landmarks (`nav`, `main`, `header`, `footer`).

### 5.7 Rendimiento

- CSS < 40 KB minificado, sin CDN, `preload` de fuente, `font-display: swap`.
- Sin JS bloqueante; scripts con `defer`.
- Imágenes/íconos SVG inline o sprite cacheado.

---

## 6. Roadmap por fases

| Fase | Alcance | Entregable | Estimación |
|---|---|---|---|
| **0. Preparación** | Inventario de textos, capturas "antes", baseline de tareas y SUS | Documento de baseline | 0.5 d |
| **1. Fundaciones** | Tokens, build CSS, shell (sidebar/topbar/drawer), `base.html`, macros, `error.html`, flash, página 404/400 | Sistema visual navegable | 3 d |
| **2. Núcleo operativo** | Dashboard, Entregas + preview con stepper, Cruces + detalle | Flujo semanal completo modernizado | 4 d |
| **3. QA/QC** | Lista con conteos y antigüedad, detalle en dos columnas, leyenda de clases, evidencias dinámicas, validación inline | Ficha usable y auditable | 3 d |
| **4. Cierre** | Reemplazos, Malla (búsqueda/paginación), Mapa, Exportar | Consistencia total | 2 d |
| **5. Calidad** | Auditoría a11y, responsive 360/768/1440, contraste, teclado, pruebas de plantillas | Informe + correcciones | 2 d |
| **6. Mejoras** (opcional) | HTMX, kanban de reemplazos, gráficos históricos, modo oscuro | Extra | 2–3 d |

**Total estimado:** 14–17 días de desarrollo, sin contar contenido/microcopy validado por la AEP.

### Dependencias y orden

1. Fase 1 bloquea todo lo demás (macros y tokens).
2. Fase 2 puede paralelizarse por pantalla una vez existan macros.
3. Fase 3 requiere decidir antes el modelo de evidencias (lista JSON) y la regla "consenso exige clase".
4. Fase 4 es de bajo riesgo, buen candidato para cerrar rápido y demostrar consistencia.
5. Fase 5 no es opcional: es el criterio de aceptación.

---

## 7. Criterios de aceptación

**Visuales**
- Ninguna plantilla contiene colores/sombras hardcodeadas fuera de componentes.
- Una sola fuente de verdad para tokens (`input.css`/`@theme`).
- Consistencia de badges: mismo estado ⇒ mismo color y texto en las 8 vistas.

**Funcionales/UX**
- Toda acción POST produce confirmación visible o error en la misma pantalla.
- Los filtros sobreviven al entrar y volver de un detalle.
- Tablas con contador "X de Y" y paginación; sin truncamientos silenciosos.
- Ficha QA/QC: no se muestra JSON crudo; clase se elige con leyenda; campos obligatorios visibles.
- Formularios bloquean doble envío y muestran estado de carga.

**Accesibilidad**
- Lighthouse Accessibility ≥ 95 en las 8 vistas.
- Navegación completa por teclado, sin trampas de foco, con foco visible.
- Contraste AA verificado en textos, badges y botones.
- `prefers-reduced-motion` y zoom 200% sin pérdida de contenido.

**Responsive**
- Sin scroll horizontal de página a 360 px (solo tablas en contenedor propio).
- Navegación utilizable con una mano; acciones primarias alcanzables.

**Técnicos**
- `app/static/css/app.css` generado y versionado; CDN eliminado.
- `error.html` cubre 400/404/500 con navegación.
- Pruebas mínimas: 200 en todas las rutas + snapshot de macros + test de render de ficha.

---

## 8. Medición de éxito

| Métrica | Método | Objetivo |
|---|---|---|
| Tiempo en cargar y confirmar una entrega | Prueba moderada con 3 usuarios | −40% |
| Tiempo en resolver una ficha QA/QC | Prueba moderada | −35% |
| Errores de operación por sesión | Observación + logs | −50% |
| SUS (System Usability Scale) | Encuesta post-fase 5 | ≥ 80 |
| Accesibilidad | axe/Lighthouse | 0 violaciones críticas |
| Percepción visual | Comparativa antes/después | Aprobación de supervisión |

---

## 9. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| El build de CSS complica Docker | Medio | CLI standalone en etapa de build + CSS versionado como fallback |
| Cambios de copy alteran el significado del protocolo | Alto | Microcopy revisado por supervisión; no tocar reglas ni etiquetas normativas |
| Rediseño del formulario QA/QC rompe el POST actual | Alto | Mantener nombres de campos (`name=`) y contrato del router; solo cambia la presentación |
| Fase 6 (HTMX) introduce regresiones | Medio | Tratar como mejora progresiva: la app debe funcionar sin JS |
| Alcance se expande a mapa geográfico / auth / roles | Medio | Declarar fuera de alcance; registrar como backlog separado |
| Falta de datos reales para gráficos históricos | Bajo | Visualizaciones descriptivas del estado actual, no series inventadas |

---

## 10. Backlog fuera de alcance (explícito)

- Autenticación y permisos por rol (`Usuario` existe en el modelo pero no se usa).
- Vista geográfica real de la malla (requiere teselas/PostGIS en frontend).
- Edición de interpretaciones originales (prohibido por diseño).
- Historial completo de recálculos (hoy en auditoría, sin UI).
- Aplicación móvil nativa.

---

## Anexo A — Árbol de archivos propuesto

```
app/
├── static/
│   ├── css/
│   │   ├── input.css          # tokens + componentes (fuente)
│   │   └── app.css            # generado, versionado
│   ├── js/
│   │   └── app.js             # dropzone, validación, drawer, toasts
│   ├── fonts/
│   │   └── inter-*.woff2
│   └── icons.svg              # sprite
├── templates/
│   ├── base.html              # shell nuevo
│   ├── error.html             # nuevo
│   ├── components/
│   │   ├── ui.html            # macros
│   │   └── icons.html         # macro icon(name)
│   ├── dashboard.html
│   ├── entregas.html
│   ├── entrega_preview.html
│   ├── cruces.html
│   ├── cruce_detalle.html
│   ├── qaqc_list.html
│   ├── qaqc_detalle.html
│   ├── reemplazos.html
│   ├── malla.html
│   ├── mapa.html
│   └── exportar.html
scripts/
└── build-css.ps1
```

## Anexo B — Checklist de arranque de la Fase 1

- [ ] Congelar capturas "antes" y baseline de tareas.
- [ ] Definir tokens finales y validar contraste.
- [ ] Montar `input.css` + build y reemplazar el CDN en `base.html`.
- [ ] Crear `ui.html` con 6 macros iniciales (`btn`, `badge`, `card`, `stat`, `empty`, `banner`).
- [ ] Shell con sidebar, topbar, drawer y skip link.
- [ ] `error.html` + handler en `main.py`.
- [ ] Flash por query param en `base.html` + helper de redirección.
- [ ] Migrar `dashboard.html` como pantalla piloto y revisar con la supervisión.
