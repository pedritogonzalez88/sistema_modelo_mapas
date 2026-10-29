# Auditoría de accesibilidad y responsive — Validador EET (AEP)

**Fecha de ejecución:** 29/10/2026
**Base auditada:** http://127.0.0.1:8017
**Motor:** axe-core 4.14.0 sobre Chrome 156.0.8078.74 (Playwright, `channel: "chrome"`), headless
**Anchos comprobados:** 1440 px · 1024 px · 768 px · 390 px · 360 px · 320 px
**Rutas:** 11 (ver tabla)

**Scripts reproducibles:**

```bash
node scripts/a11y-audit.mjs        # axe + capturas + desbordamiento + consola
node scripts/a11y-interaction.mjs  # teclado, drawer y zoom
node scripts/a11y-report.mjs       # este informe
```

> **Estado vigente:** las secciones 1–10 documentan la primera pasada. Las
> cuatro reglas incumplidas y la contención del foco del drawer se corrigieron
> el mismo 29/10/2026: ver **§11 Estado tras correcciones** (0 violaciones
> `critical`/`serious`/`moderate` a 1440 y 390 px).

**Evidencia cruda:** `docs/qa-visual/audit-data.json`, `docs/qa-visual/interaction-data.json`,
`docs/qa-visual/axe/*.json` (violaciones e incompletos íntegros), `docs/qa-visual/axe/resumen.md`.

## 1. Resultado global

| Impacto | Reglas (1440) | Reglas (390) |
|---|---|---|
| critical | 0 | 0 |
| serious | 2 | 3 |
| moderate | 2 | 2 |
| minor | 0 | 0 |
| **Total reglas** | **4** | **5** |
| **Nodos afectados** | **15** | **65** |

Las 11 rutas responden 200 y el HTML carga sin errores de JavaScript. No hay **ninguna** violación de impacto `critical` ni desbordamiento horizontal de página en ninguno de los 6 anchos (1440, 1024, 768, 390, 360, 320 px).

Reparto: en 1440 px solo 3 de 11 rutas presentan hallazgos; en 390 px son 3 de 11 (dos coinciden).

## 2. Violaciones por ruta e impacto

| Ruta | Estado | 1440: critical | serious | moderate | minor | Reglas | Nodos | 390: critical | serious | moderate | minor | Reglas | Nodos |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `/` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `/entregas` | 200 | 0 | 0 | 1 | 0 | 1 | 1 | 0 | 0 | 1 | 0 | 1 | 1 |
| `/entregas/1/preview` | 200 | 0 | 2 | 1 | 0 | 3 | 14 | 0 | 2 | 1 | 0 | 3 | 14 |
| `/cruces` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 50 |
| `/cruces/81/2008` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `/qaqc` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `/qaqc/1` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `/reemplazos` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `/malla` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `/mapa` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `/exportar` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Rutas sin ningún hallazgo en ambos anchos: `/`, `/cruces/81/2008`, `/qaqc`, `/qaqc/1`, `/reemplazos`, `/malla`, `/mapa`, `/exportar`.

## 3. Reglas incumplidas (4 en total)

| # | id | Impacto | Ayuda | Rutas afectadas | Nodos | 2 selectores de ejemplo |
|---|---|---|---|---|---|---|
| 1 | `link-in-text-block` | serious | Links must be distinguishable without relying on color | /cruces @390 | 50 | `.card:nth-child(1) > .card__body.gap-3.flex-col > .items-start.justify-between.flex-wrap > .text-sm.font-semibold > .text-brand-700.underline-offset-2.hover\:underline`<br>`.card:nth-child(2) > .card__body.gap-3.flex-col > .items-start.justify-between.flex-wrap > .text-sm.font-semibold > .text-brand-700.underline-offset-2.hover\:underline` |
| 2 | `dlitem` | serious | <dt> and <dd> elements must be contained by a <dl> | /entregas/1/preview @1440<br>/entregas/1/preview @390 | 24 | `.grid-cols-2.grid.gap-3:nth-child(2) > div:nth-child(1) > dt`<br>`.grid-cols-2.grid.gap-3:nth-child(2) > div:nth-child(1) > .tabular.mt-0\.5` |
| 3 | `definition-list` | serious | <dl> elements must only directly contain properly-ordered <dt> and <dd> groups, <script>, <template> or <div> elements | /entregas/1/preview @1440<br>/entregas/1/preview @390 | 2 | `dl` |
| 4 | `landmark-unique` | moderate | Landmarks should have a unique role or role/label/title (i.e. accessible name) combination | /entregas @1440<br>/entregas @390<br>/entregas/1/preview @1440<br>/entregas/1/preview @390 | 4 | `section[aria-labelledby="listado-entregas"]`<br>`section[aria-labelledby="errores-validacion"]` |

### Detalle por regla

#### `link-in-text-block` — serious

Links must be distinguishable without relying on color

**Causa detectada:** En las tarjetas móviles de `/cruces` el número de punto es un enlace inline `text-brand-700` junto a texto `ink-900`: 2.44:1 de contraste entre ambos y sin subrayado (`hover:underline` solo al pasar el ratón), por debajo del 3:1 que exige 1.4.1 cuando el color es el único distintivo. En 1440 px el mismo enlace vive en una celda de tabla sin texto contiguo, por eso la regla no se activa.

- Documentación: https://dequeuniversity.com/rules/axe/4.14/link-in-text-block?application=axeAPI
- Rutas: /cruces @390 (50 nodos)
- Selectores observados:
  - `.card:nth-child(1) > .card__body.gap-3.flex-col > .items-start.justify-between.flex-wrap > .text-sm.font-semibold > .text-brand-700.underline-offset-2.hover\:underline`
  - `.card:nth-child(2) > .card__body.gap-3.flex-col > .items-start.justify-between.flex-wrap > .text-sm.font-semibold > .text-brand-700.underline-offset-2.hover\:underline`
  - `.card:nth-child(3) > .card__body.gap-3.flex-col > .items-start.justify-between.flex-wrap > .text-sm.font-semibold > .text-brand-700.underline-offset-2.hover\:underline`

#### `dlitem` — serious

<dt> and <dd> elements must be contained by a <dl>

**Causa detectada:** El `<dl>` de la tarjeta «Archivo» en `entrega_preview.html` agrupa los pares con un `<div>` de rejilla que a su vez contiene un `<div>` por dato. HTML solo permite **un** nivel de `<div>` entre `<dl>` y `<dt>/<dd>`, así que los 12 pares quedan sin `<dl>` padre válido y se pierde la semántica de lista de descripciones.

- Documentación: https://dequeuniversity.com/rules/axe/4.14/dlitem?application=axeAPI
- Rutas: /entregas/1/preview @1440 (12 nodos); /entregas/1/preview @390 (12 nodos)
- Selectores observados:
  - `.grid-cols-2.grid.gap-3:nth-child(2) > div:nth-child(1) > dt`
  - `.grid-cols-2.grid.gap-3:nth-child(2) > div:nth-child(1) > .tabular.mt-0\.5`
  - `.grid-cols-2.grid.gap-3:nth-child(2) > div:nth-child(2) > dt`

#### `definition-list` — serious

<dl> elements must only directly contain properly-ordered <dt> and <dd> groups, <script>, <template> or <div> elements

**Causa detectada:** Misma causa que `dlitem`: el hijo directo del `<dl>` es un `<div>` que envuelve otros `<div>` con los `dt`/`dd`.

- Documentación: https://dequeuniversity.com/rules/axe/4.14/definition-list?application=axeAPI
- Rutas: /entregas/1/preview @1440 (1 nodo); /entregas/1/preview @390 (1 nodo)
- Selectores observados:
  - `dl`

#### `landmark-unique` — moderate

Landmarks should have a unique role or role/label/title (i.e. accessible name) combination

**Causa detectada:** En `/entregas` y `/entregas/1/preview`, el `<section aria-labelledby="…">` recibe nombre accesible y se convierte en landmark `region`; su nombre coincide con el del `div.table-wrap role="region"` (`aria-label` de la tabla que contiene, p. ej. «Entregas registradas»). Resultado: dos regiones con el mismo nombre. Las demás vistas no nombran la sección que envuelve la tabla, por eso no aparecen.

- Documentación: https://dequeuniversity.com/rules/axe/4.14/landmark-unique?application=axeAPI
- Rutas: /entregas @1440 (1 nodo); /entregas @390 (1 nodo); /entregas/1/preview @1440 (1 nodo); /entregas/1/preview @390 (1 nodo)
- Selectores observados:
  - `section[aria-labelledby="listado-entregas"]`
  - `section[aria-labelledby="errores-validacion"]`

### 3.1 Resultados incompletos de axe (2 reglas, 7 casos)

axe no pudo decidir estos casos; **no son violaciones**, pero tampoco están verificados y
requieren revisión manual:

#### `color-contrast` — serious

Elements must meet minimum color contrast ratio thresholds

- Rutas: / @1440; /entregas/1/preview @1440; /entregas/1/preview @390
- `text` — Element's background color could not be determined because it is overlapped by another element
  ```html
  <text x="70.0" y="70.0" text-anchor="middle" dominant-baseline="central" class="fill-ink-900 tabular" font-size="28" font-weight="600">15400</text>
  ```
- `.max-w-sm` — Element's background color could not be determined because it's partially obscured by another element
  ```html
  <p class="max-w-sm text-xs text-ink-600">Al confirmar, la entrega queda aceptada y se recalculan los cruces de la terna. Las interpretaciones originales no se s
  ```
- `.max-w-sm` — Element's background color could not be determined because it partially overlaps other elements
  ```html
  <p class="max-w-sm text-xs text-ink-600">Al confirmar, la entrega queda aceptada y se recalculan los cruces de la terna. Las interpretaciones originales no se s
  ```
- `.mt-2` — Element's background color could not be determined because it partially overlaps other elements
  ```html
  <p class="mt-2 text-xs text-ink-600">Una entrega rechazada conserva su archivo y su huella SHA-256 como evidencia; no se elimina del proyecto.</p>
  ```

#### `label-content-name-mismatch` — serious

Elements must have their visible text as part of their accessible name

- Rutas: /cruces @1440; /qaqc @1440; /malla @1440
- `a[rel="next"]` — Unable to determine if the visible text is part of the accessible name
  ```html
  <a class="pagination__page" href="http://127.0.0.1:8017/cruces?page=2" rel="next" aria-label="Página siguiente">›</a>
  ```
- `a[rel="next"]` — Unable to determine if the visible text is part of the accessible name
  ```html
  <a class="pagination__page" href="http://127.0.0.1:8017/qaqc?page=2" rel="next" aria-label="Página siguiente">›</a>
  ```
- `a[rel="next"]` — Unable to determine if the visible text is part of the accessible name
  ```html
  <a class="pagination__page" href="http://127.0.0.1:8017/malla?page=2" rel="next" aria-label="Página siguiente">›</a>
  ```

## 4. Desbordamiento horizontal

Ninguna de las 11 rutas desborda horizontalmente en 1440, 1024, 768, 390, 360, 320 px (`document.documentElement.scrollWidth <= window.innerWidth + 1` en todos los casos).

Las tablas anchas quedan contenidas en su propio contenedor con scroll (`role="region"` +
`tabindex="0"`), que es el comportamiento previsto: el documento no gana scroll lateral.

## 5. Errores de consola y de carga

Ninguna ruta registró errores de consola (`console` tipo `error`), excepciones no capturadas
(`pageerror`), peticiones fallidas ni respuestas HTTP ≥ 400 en ninguno de los anchos probados.

## 6. Prueba de teclado — skip link (390x844, `/`)

**Resultado: PASA** (todos los pasos)

| Paso | Resultado | Evidencia |
|---|---|---|
| Primer Tab desde el inicio de la página | PASA | Foco en <a> href="#contenido" texto="Saltar al contenido" |
| El skip link es visible al recibir foco | PASA | rect=151x36 en (16,16); clip-path=none; position=absolute; display=block |
| El destino #contenido existe | PASA | <main> con id="contenido" (skippable) |
| Enter activa el skip link (#contenido) | PASA | hash="#contenido" foco=<body#null> scrollY=57 |
| El Tab posterior entra directamente en #contenido (no recorre la navegación) | PASA | foco=<a> "Nueva entrega" href="/entregas" dentroDeContenido=true enNavegacion=false |

Orden de tabulación observado (primeras 6 paradas, sin haber usado el ratón):

| # | Elemento | Texto / etiqueta | y (px) |
|---|---|---|---|
| 1 | `<a>` | Saltar al contenido | 16 |
| 2 | `<button#drawer-toggle>` | Abrir menú de navegación | 9 |
| 3 | `<a>` | Nueva entrega | 149 |
| 4 | `<a>` | Ver malla | 404 |
| 5 | `<a>` | Abrir mapa | 529 |
| 6 | `<a>` | Ver entregas | 654 |

## 7. Drawer móvil (390x844, `/`)

**Resultado: PASA** (todos los pasos) — apertura con clic real (locator.click)

| Paso | Resultado | Evidencia |
|---|---|---|
| El botón hamburguesa es visible a 390 px | PASA | aria-label="Abrir menú de navegación" aria-controls="drawer" |
| Al abrir, aria-expanded pasa a "true" | PASA | aria-expanded="true", data-open="true", overlay="true" |
| El drawer queda visible como diálogo | PASA | display=flex rect={"w":280,"h":844,"x":0} scroll-bloqueado=true |
| El foco entra en el drawer (botón cerrar) | PASA | foco=<button> aria-label="Cerrar menú de navegación" |
| Escape cierra el drawer y aria-expanded vuelve a "false" | PASA | aria-expanded="false", data-open="false", overlay="false" |
| El foco regresa al botón de apertura | PASA | foco=<button#drawer-toggle> aria-label="Abrir menú de navegación" |

axe-core sobre el drawer abierto (`docs/qa-visual/axe/drawer-390.json`): **0 violaciones / 0 nodos**.

### Sonda adicional: contención del foco en el modal

Con el drawer abierto (`aria-modal="true"`), la pulsación **9** de `Tab` saca el foco del diálogo y lo lleva al resto de la página:

- 8. `<a>` Exportar → `/exportar` — dentro del drawer: sí
- 9. `<button>` Abrir menú de navegación — dentro del drawer: **no**
- 10. `<a>` Nueva entrega → `/entregas` — dentro del drawer: **no**
- 11. `<a>` Ver malla → `/malla` — dentro del drawer: **no**

No lo detecta ninguna regla automática de axe: es una desviación del patrón APG para diálogos modales (el fondo debería quedar inerte mientras el diálogo está abierto). Queda registrado como hallazgo menor.

> **Corregido el 29/10/2026** con una trampa de foco en `app/static/js/app.js`:
> la sonda se repitió y **el foco no sale del drawer en 12 pulsaciones**
> (`fugaEn: null`), `Shift+Tab` cicla hacia atrás y, al cerrar, el trap se
> libera. Ver §11.

## 8. Zoom 200 % y reflow

| Ruta | CSS `zoom:2` (1440) | Zoom de navegador 200 % (720×450 @ DPR 2) | Reflow 720 px |
|---|---|---|---|
| `/` | **desborda** (1633/1440) | sin desbordamiento (720/720) | sin desbordamiento |
| `/qaqc/1` | sin desbordamiento (1440/1440) | sin desbordamiento (720/720) | sin desbordamiento |

### Interpretación del resultado con `zoom` CSS

En `/`, aplicar `document.documentElement.style.zoom = "2"` produce un scroll horizontal de 193 px (`scrollWidth` 1633 frente a `innerWidth` 1440). La causa está en el propio método: **`zoom` CSS no reduce el viewport de maquetación, así que las media queries de escritorio siguen activas** (`(min-width: 1024px)` = true, `aside` = `flex`) mientras el contenido se dibuja al doble de tamaño. Chrome real no hace eso: al ampliar al 200 % reduce el viewport CSS a 720 px y la barra lateral desaparece.

Por eso se añadieron dos comprobaciones fieles: **zoom de navegador emulado** (720×450 con `deviceScaleFactor: 2`, donde `(min-width: 1024px)` = false y el `aside` = `none`) y **reflow a 720 px**. Ambas pasan en `/` y `/qaqc/1`.

Conclusión: **no se incumple 1.4.4 (Redimensionar texto) ni 1.4.10 (Reflow)** por zoom; el desbordamiento observado con `zoom` CSS es un artefacto del método. Como nota de robustez, el contenido de la tarjeta de concordancia mide ~816 px de ancho intrínseco, así que **si en el futuro se fuerza el layout de escritorio en un espacio menor a ese ancho, desbordaría**; conviene revisarlo si se añade un modo de sidebar fijo en tablet.

## 9. Capturas generadas

33 capturas `fullPage` en `docs/qa-visual/capturas/` (1440×900, 768×1024 y 390×844).

| Ruta | Capturas |
|---|---|
| `/` | [`inicio-1440.png`](./capturas/inicio-1440.png) · [`inicio-768.png`](./capturas/inicio-768.png) · [`inicio-390.png`](./capturas/inicio-390.png) |
| `/entregas` | [`entregas-1440.png`](./capturas/entregas-1440.png) · [`entregas-768.png`](./capturas/entregas-768.png) · [`entregas-390.png`](./capturas/entregas-390.png) |
| `/entregas/1/preview` | [`entrega-preview-1440.png`](./capturas/entrega-preview-1440.png) · [`entrega-preview-768.png`](./capturas/entrega-preview-768.png) · [`entrega-preview-390.png`](./capturas/entrega-preview-390.png) |
| `/cruces` | [`cruces-1440.png`](./capturas/cruces-1440.png) · [`cruces-768.png`](./capturas/cruces-768.png) · [`cruces-390.png`](./capturas/cruces-390.png) |
| `/cruces/81/2008` | [`cruce-detalle-1440.png`](./capturas/cruce-detalle-1440.png) · [`cruce-detalle-768.png`](./capturas/cruce-detalle-768.png) · [`cruce-detalle-390.png`](./capturas/cruce-detalle-390.png) |
| `/qaqc` | [`qaqc-lista-1440.png`](./capturas/qaqc-lista-1440.png) · [`qaqc-lista-768.png`](./capturas/qaqc-lista-768.png) · [`qaqc-lista-390.png`](./capturas/qaqc-lista-390.png) |
| `/qaqc/1` | [`qaqc-ficha-1440.png`](./capturas/qaqc-ficha-1440.png) · [`qaqc-ficha-768.png`](./capturas/qaqc-ficha-768.png) · [`qaqc-ficha-390.png`](./capturas/qaqc-ficha-390.png) |
| `/reemplazos` | [`reemplazos-1440.png`](./capturas/reemplazos-1440.png) · [`reemplazos-768.png`](./capturas/reemplazos-768.png) · [`reemplazos-390.png`](./capturas/reemplazos-390.png) |
| `/malla` | [`malla-1440.png`](./capturas/malla-1440.png) · [`malla-768.png`](./capturas/malla-768.png) · [`malla-390.png`](./capturas/malla-390.png) |
| `/mapa` | [`mapa-1440.png`](./capturas/mapa-1440.png) · [`mapa-768.png`](./capturas/mapa-768.png) · [`mapa-390.png`](./capturas/mapa-390.png) |
| `/exportar` | [`exportar-1440.png`](./capturas/exportar-1440.png) · [`exportar-768.png`](./capturas/exportar-768.png) · [`exportar-390.png`](./capturas/exportar-390.png) |

## 10. Cobertura y limitaciones

**Cubierto con evidencia de navegador real:**

- axe-core con las etiquetas `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` y `best-practice` en las 11 rutas, a 1440 px y a 390 px.
- Desbordamiento horizontal de documento en 6 anchos (1440, 1024, 768, 390, 360, 320 px).
- Errores de consola, excepciones de página, peticiones fallidas y respuestas ≥ 400 por ruta y ancho.
- Navegación por teclado del skip link (foco, visibilidad y salto real al contenido).
- Drawer móvil: `aria-expanded`, apertura, `Escape` y retorno del foco, más axe sobre el drawer abierto.
- Zoom 200 % (emulación fiel y `zoom` CSS) y reflow a 720 px en `/` y `/qaqc/1`.

**No cubierto (sigue pendiente de revisión manual/AT):**

- Recorrido completo con teclado de las 11 vistas (aquí solo el arranque en `/` y el drawer).
- Lectores de pantalla reales (NVDA/JAWS + Firefox/Chrome, VoiceOver + Safari).
- Contraste: `color-contrast` sin violaciones; 2 casos quedaron **incompletos** y se listan en 3.1 (texto SVG de la dona y un párrafo parcialmente tapado por la barra sticky). Los cuatro valores se midieron y superan AA (§11.4).
- `prefers-reduced-motion: reduce`.
- Envío de formularios sin ratón, incluida la carga de archivo.
- Lighthouse (puntaje numérico); esta auditoría usa axe-core directamente, que es el motor de su categoría de accesibilidad.
- Contenido dentro de estados no alcanzables en esta BD de muestra (p. ej. fichas cerradas, errores de validación vacíos).

---

## 11. Estado tras correcciones

**Fecha de la corrección y nueva pasada:** 29/10/2026 (misma jornada que la auditoría).
**Base:** `http://127.0.0.1:8017` (uvicorn). Se repitió además la pasada completa contra el
contenedor `http://localhost:8000` (imagen reconstruida) con **idéntico resultado**.

```bash
uv run uvicorn app.main:app --port 8017      # base auditada
node scripts/a11y-audit.mjs                  # axe 1440 y 390, 11 rutas
node scripts/a11y-interaction.mjs            # teclado, drawer, zoom
npm run css:build                            # CSS regenerado
```

### 11.1 Antes / después por impacto

| Impacto | 1440 antes | 1440 después | 390 antes | 390 después |
|---|---|---|---|---|
| critical | 0 | **0** | 0 | **0** |
| serious | 2 | **0** | 3 | **0** |
| moderate | 2 | **0** | 2 | **0** |
| minor | 0 | **0** | 0 | **0** |
| **Reglas** | **4** | **0** | **5** | **0** |
| **Nodos** | **15** | **0** | **65** | **0** |

Las 11 rutas siguen respondiendo 200, sin errores de consola, sin `pageerror` y sin
desbordamiento horizontal en 1440, 1024, 768, 390, 360 y 320 px.

### 11.2 Qué se cambió (por regla)

| Regla (antes) | Corrección | Archivos |
|---|---|---|
| `link-in-text-block` (serious, `/cruces` @390, 50 nodos) | El número de punto pasa de `hover:underline` a `underline underline-offset-2` (subrayado permanente, no depende del color) y el hover cambia a `text-brand-900` en vez de añadir el subrayado. Se aplicó el mismo criterio a los demás enlaces inline del proyecto. | `cruces.html`, `qaqc_list.html`, `qaqc_detalle.html`, `cruce_detalle.html` |
| `dlitem` (serious, `/entregas/1/preview`, 12 nodos) | El `<dl>` de la tarjeta «Archivo» pasa a `grid grid-cols-2 gap-3`; cada hijo directo es ahora un `<div>` con **un** `<dt>` y su `<dd>` (un solo nivel). Los datos de ancho completo usan `col-span-2`, así que la rejilla visual se conserva. | `entrega_preview.html` |
| `definition-list` (serious, `/entregas/1/preview`, 1 nodo) | Misma corrección: el `<dl>` ya solo contiene `<div>` válidos. | `entrega_preview.html` |
| `landmark-unique` (moderate, `/entregas` y `/entregas/1/preview`) | Nombres únicos: la región de la tabla pasa a «Entregas registradas **por semana**» y «Errores de validación **detectados**», distintos de los títulos de sección («Entregas registradas», «Errores de validación»). | `entregas.html`, `entrega_preview.html` |
| `label-content-name-mismatch` (incomplete, paginación) | El glifo «‹»/«›» se oculta al árbol accesible (`<span aria-hidden="true">`) y el nombre accesible del enlace llega por un `sr-only` («Página anterior» / «Página siguiente»). Se probaron cuatro variantes con axe (§11.3) y esta es la única que deja la regla limpia sin cambiar el aspecto. | `components/ui.html` |
| Foco del drawer (hallazgo no automatizado, patrón APG) | Trampa de foco en `app.js`: `Tab`/`Shift+Tab` ciclan dentro del drawer, `Escape` cierra y devuelve el foco a `#drawer-toggle`, y al cerrar el trap se libera (el `Tab` siguiente vuelve al orden normal de la página). Sin JS, el drawer sigue mostrándose como bloque estático. | `app/static/js/app.js` |

### 11.3 Evidencia de `label-content-name-mismatch`

Se midieron cuatro variantes del enlace `rel="next"` con `axe.run` sobre el `<nav>` de
paginación de `/cruces`:

| Variante | violaciones | incomplete | passes |
|---|---|---|---|
| `aria-label` + glifo visible | 0 | **1** | 0 |
| `aria-label` incluyendo el glifo («Página siguiente ›») | 0 | **1** | 0 |
| **elegida:** glifo `aria-hidden` + texto `sr-only` (sin `aria-label`) | **0** | **0** | 0 |
| texto visible «Siguiente» + `aria-label` | 0 | 0 | 1 |

Las dos primeras siguen sin ser decidibles para axe; la cuarta obliga a cambiar el
glifo por texto y ensancha el paginador (riesgo de desbordamiento a 320 px). La tercera
deja el nombre accesible calculado desde el contenido, no depende de un carácter
decorativo y no altera el diseño.

### 11.4 Los dos `color-contrast` incompletos, medidos

axe no puede decidir estos casos porque la dona son círculos SVG que se solapan y
porque la barra inferior usa `backdrop-blur` (capa de composición). Se resolvieron los
colores reales con el motor del navegador (canvas 2D sobre los valores calculados) y se
comprobó con *hit-testing* que el texto no está tapado:

| Elemento | Texto | Fondo efectivo | Ratio | AA (4.5:1) |
|---|---|---|---|---|
| `<text>` del total de la dona (`/`, 1440 y 390) | `rgb(15, 23, 42)` (`ink-900`) | `#FFFFFF` (tarjeta) | **17.85:1** | ✅ |
| `p.max-w-sm` de la barra sticky (`/entregas/1/preview`) | `rgb(71, 85, 105)` (`ink-600`) | blanco al 95 % sobre `#F4F7F6` → `rgb(254, 255, 255)` | **7.56:1** | ✅ |
| `p.mt-2` de la barra sticky (390) | `rgb(71, 85, 105)` (`ink-600`) | ídem | **7.56:1** | ✅ |

`document.elementFromPoint` en el centro de cada párrafo devuelve el propio párrafo y
`scrollHeight === clientHeight`: el texto no está recortado ni cubierto. **No se cambió
ningún color de serie de la dona.** Quedan como `incomplete` (5 nodos) y esta tabla es
la evidencia manual que los cierra.

### 11.5 Teclado y drawer tras la corrección

`node scripts/a11y-interaction.mjs` → **PASA** (6/6 pasos del skip link y 6/6 del drawer).

| Comprobación extra | Resultado |
|---|---|
| `Shift+Tab` desde el primer enfocable (botón cerrar) | va al último del drawer («Exportar») |
| `Tab` desde el último enfocable | vuelve al primero (botón cerrar) |
| 20 pulsaciones de `Tab` con el drawer abierto | **0 salidas** del drawer (`fugaEn: null` en 12) |
| `Escape` | cierra, `aria-expanded="false"`, foco en `button#drawer-toggle`, `overflow` restaurado |
| `Tab` después de cerrar | sale del drawer y sigue el orden normal («Nueva entrega») |
| Sin JavaScript (`javaScriptEnabled: false`) | drawer visible como bloque con sus 8 enlaces, hamburguesa oculta, skip link presente |

axe sobre el drawer abierto: **0 violaciones / 0 nodos** (`axe/drawer-390.json`).

### 11.6 Artefactos regenerados

- `docs/qa-visual/` completo: `audit-data.json`, `interaction-data.json`,
  `axe/*.json` (22 archivos), `axe/resumen.md` y las **33 capturas** `fullPage`
  (1440, 768 y 390 px).
- `app/static/css/app.css`: 51 964 bytes (la imagen Docker sirve 47 343 bytes).

### 11.7 Lo que sigue abierto

- Lectores de pantalla reales (NVDA/JAWS, VoiceOver): el anuncio efectivo no es
  verificable con axe.
- Lighthouse (puntaje numérico).
- Regresión visual manual lado a lado de las capturas.
- Recorrido de teclado completo de las 11 vistas (envío de formularios y carga de
  archivo incluidas) y `prefers-reduced-motion`.
- Los 5 nodos `color-contrast` marcados como `incomplete` seguirán apareciendo en
  cada pasada: son limitaciones de axe, no incumplimientos (ratios en §11.4).
