# QA de accesibilidad — Validador EET (AEP)

**Fecha:** 29/10/2026
**Fase:** 7 (calidad y cierre) del plan de modernización UX/UI
**Autor:** cierre técnico de la fase

---

## 1. Alcance

Se auditó el HTML renderizado por la aplicación completa (Jinja2 + FastAPI) en las
siguientes vistas:

| # | Ruta | Tipo |
|---|---|---|
| 1 | `/` | Resumen |
| 2 | `/entregas` | Listado y carga de entregas |
| 3 | `/cruces` | Listado paginado de ternas |
| 4 | `/qaqc` | Listado de fichas QA/QC |
| 5 | `/reemplazos` | Reemplazos |
| 6 | `/malla` | Malla oficial |
| 7 | `/mapa` | Mapa MADES |
| 8 | `/exportar` | Exportación |
| 9 | `/qaqc/1` | Ficha QA/QC (formulario completo) |
| 10 | `/cruces/81/2008` | Detalle de cruce |
| 11 | `/entregas/1/preview` | Vista previa de entrega |

Las rutas 9–11 se agregaron al alcance porque concentran formularios y enlaces que
las ocho vistas de navegación no ejercitan.

Referencias normativas usadas como criterio: WCAG 2.2 nivel AA y las secciones 5.6
y 7 de `docs/plan-ux-ui-modernizacion.md` y 12, 13 y 17 de
`docs/plan-implementacion-ux-ui.md`.

---

## 2. Pruebas automatizadas ejecutadas

### 2.1 Auditoría estática de accesibilidad

Archivo: `tests/test_a11y_static.py` (marcado `integration` porque renderiza la app
con `TestClient` y PostgreSQL).

Criterios verificados en las 11 rutas:

- `lang="es"` en `<html>`.
- `<meta name="viewport">` con `width=device-width` y sin `user-scalable=no`.
- Exactamente un `<h1>` por página.
- Skip link hacia `#contenido` y existencia de `<main id="contenido">`; el atajo es
  el primer enlace del documento.
- Todo `<input>` visible tiene `id` y un `<label for>` que lo referencia; los
  `checkbox` y `radio` admiten etiqueta envolvente, `for` o `aria-label`; se
  excluyen `hidden`, `submit`, `button`, `reset` e `image`.
- Todo `<label for="…">` apunta a un `id` existente.
- Toda `<table>` tiene `<caption>`.
- Todo `<button>` declara `type`.
- No hay `href="#"` ni `href="javascript:"`.
- Las ocho vistas de navegación usan `ui.page_header` (un solo `h1` con
  `page-header__title`).

Resultado: **108 pruebas de integración en verde** (incluye las de rutas y las de
accesibilidad).

### 2.2 Pruebas de rutas y contrato

- `tests/test_web_routes.py`: 200 y `text/html` en las ocho vistas; `/salud` JSON
  `{"estado": "ok"}`; 404 HTML (no JSON) en cruce inexistente; `return_to` externo
  descartado; paginación con contador "Mostrando"; contrato JSON con
  `Accept: application/json`.
- `tests/test_form_contracts.py`: los `name` que consume el backend se conservan
  (`qaqc_detalle.html`, `entregas.html`, `malla.html`, `mapa.html` y el `motivo` de
  `entrega_preview.html`).

Comandos y resultados:

```text
uv run pytest -q                 -> 129 passed, 108 deselected
uv run pytest -m integration -q  -> 108 passed, 129 deselected
```

### 2.3 Verificación de CSS servido

- `app/static/css/app.css` local: 51 964 bytes en la primera pasada y **48 687
  bytes** tras el cierre de la sección 7 (entre 10 KB y 60 KB en ambos casos).
- CSS servido por el contenedor: 47 343 bytes, `200` con `text/css`.
- Presentes las clases muestreadas: `.md\:hidden`, `.lg\:col-span-2`,
  `.lg\:grid-cols-3`, `.sticky`, `.clase-9`, `.banner`, `.pagination`,
  `.dirty-indicator`, `.badge--warn` (reverificado sobre el CSS de la sección 7).
- Sin ninguna referencia a `cdn.tailwindcss.com` en `app/` (búsqueda recursiva).

> `md:grid-cols-4` no se genera porque ninguna plantilla la usa: la búsqueda
> recursiva en `app/` no encuentra esa clase. Las rejillas de cuatro columnas del
> rediseño usan `xl:grid-cols-4` y `lg:grid-cols-4`. Se verificó en su lugar
> `md:grid-cols-3`, que sí está en uso.

---

## 3. Hallazgos y correcciones

| Hallazgo | Criterio | Corrección |
|---|---|---|
| Los campos de evidencia de la ficha QA/QC eran `<input type="text" name="evidencias">` sin `id` ni `<label for>` (solo `aria-label`). | 1.3.1 / 3.3.2 / 4.1.2 | Se añadió `<label class="sr-only" for="evidencia-N">` y `id="evidencia-N"` por fila; el JS que clona filas recalcula un `id` único y actualiza el `for`. |
| El indicador "Cambios sin guardar" usaba clases de paleta sueltas (`bg-amber-100`, `text-amber-800`) fuera del sistema. | Consistencia de tokens | Se sustituyó por `badge badge--warn`; el color queda definido en `input.css`. |
| No había comprobación automatizada de etiquetas, `caption`, `type` de botones ni jerarquía de encabezados. | 1.3.1 / 1.3.2 / 4.1.2 | Nueva batería `test_a11y_static.py`. |
| El CSS de la imagen Docker no se validaba por contenido. | Despliegue | Se comprobó tamaño, `content-type` y clases presentes sobre el contenedor en ejecución. |

No se modificaron textos normativos, reglas de negocio ni la estructura de las
vistas.

### 3.1 Hallazgos aceptados (sin cambio)

- **Colores de la dona de concordancia** en `dashboard.html`: `#059669`,
  `#0284C7` y `#DC2626`. Coinciden con `emerald-600`, `sky-600` y `red-600` de la
  paleta de Tailwind y se pasan como datos a la macro `donut`; convertirlos a
  `var(--color-…)` obligaría a depender de que Tailwind emita esas variables, por
  lo que se mantienen como valores explícitos de la serie.
- **`aria-sort`** no se usa: ninguna tabla se ordena por columna desde la interfaz.
- **Contenedor de tabla con `role="region"` y `tabindex="0"`**: se aplica mediante
  `ui.table_wrap` en todas las tablas renderizadas.

### 3.2 Hallazgos de la auditoría con navegador (corregidos el 29/10/2026)

La pasada con Chrome + axe-core de la sección 6 dejó 4 reglas incumplidas
(2 `serious` + 2 `moderate` a 1440 px; 3 `serious` + 2 `moderate` a 390 px) más
una desviación del patrón APG que axe no detecta. Todas están corregidas:

| Hallazgo | Criterio | Corrección |
|---|---|---|
| `link-in-text-block` en las tarjetas de `/cruces` a 390 px (50 nodos): el número de punto era un enlace `text-brand-700` junto a texto `ink-900` (2.44:1) sin subrayado. | 1.4.1 | Subrayado permanente (`underline underline-offset-2`) y hover de color `text-brand-900` en lugar de `hover:underline`. Se aplicó el mismo criterio a los enlaces inline de `qaqc_list.html`, `qaqc_detalle.html` y `cruce_detalle.html`. |
| `dlitem` (12 nodos) y `definition-list` (1 nodo) en la tarjeta «Archivo» de `/entregas/1/preview`: dos niveles de `<div>` entre `<dl>` y los `<dt>/<dd>`. | 1.3.1 | El `<dl>` es ahora la rejilla (`grid grid-cols-2 gap-3`) y cada hijo directo es un `<div>` con un `<dt>` y su `<dd>`; los datos de ancho completo usan `col-span-2`. Diseño visual intacto. |
| `landmark-unique` en `/entregas` y `/entregas/1/preview`: la `<section aria-labelledby>` y el `div.table-wrap role="region"` compartían nombre accesible. | 1.3.6 / ARIA | Nombres únicos: «Entregas registradas **por semana**» y «Errores de validación **detectados**». |
| `label-content-name-mismatch` (incompleto) en la paginación: el glifo visible «›» no estaba contenido en el `aria-label`. | 2.5.3 | Glifo con `aria-hidden="true"` y nombre accesible por texto `sr-only` («Página anterior»/«Página siguiente»). Verificado con axe: la regla deja de aparecer. |
| Drawer con `aria-modal="true"` sin contener el foco (salía en la 9.ª pulsación de `Tab`). | Patrón APG | Trampa de foco en `app.js`: ciclo `Tab`/`Shift+Tab`, `Escape` cierra y devuelve el foco a `#drawer-toggle`, liberación del trap al cerrar. Degrada sin JS. |

Resultado: **0 violaciones `critical`/`serious`/`moderate`** en los 11 recorridos a
1440 y 390 px, tanto en `uvicorn` como en la imagen Docker.

---

## 4. Revisión manual pendiente

> Estado actualizado tras la auditoría con navegador del 29/10/2026 (sección 6) y el
> cierre de pendientes automatizables (sección 7):
> ✅ cubierto · ◐ cobertura parcial · ⬜ sigue pendiente.

### 4.1 Teclado

- ✅ Recorrer las 11 vistas solo con `Tab`, `Shift+Tab`, `Enter`, `Espacio` y `Esc`.
  Barrido completo a 1440 y 390 px: 22 recorridos, 0 incidencias (sección 7.3 y
  `docs/qa-visual/teclado-11-vistas.md`).
- ✅ El drawer móvil abre con el botón, cierra con `Esc` y devuelve el foco al
  botón de apertura. 6/6 pasos verdes (6.3).
- ✅ El drawer **contiene el foco** mientras está abierto: `Tab` y `Shift+Tab`
  ciclan dentro, 20 pulsaciones sin salir, y al cerrar el trap se libera (6.3).
- ✅ El foco visible (anillo `brand-500`) se percibe en enlaces, botones, campos,
  radios, checkboxes y contenedores de tabla. Comprobado `outline: solid 2px
  #1E8A69` (offset 2px) con teclado en el skip link, el botón hamburguesa y un
  `input[type=file]`; el barrido de la sección 7.3 confirma que no se enfoca
  ningún elemento oculto en las 11 vistas.
- ✅ El orden de tabulación sigue el orden del documento en las 11 vistas a 1440 y
  390 px (sin retrocesos en 22 recorridos) y el primer `Tab` llega siempre al skip
  link.
- ✅ Los formularios se pueden enviar sin ratón: `Enter` sobre el botón de envío
  dispara el `submit` (interceptado y cancelado en la prueba, sin tocar la base) y
  `Espacio` alterna los checkboxes; las flechas recorren los grupos de radio
  (sección 7.3).

### 4.2 Zoom y responsive

- ✅ Zoom 200 % sin pérdida de contenido ni solapamientos (WCAG 1.4.4 y 1.4.10).
  Sin desbordamiento en `/` y `/qaqc/1` con emulación fiel de zoom de navegador
  (720×450 @ DPR 2) y con reflow a 720 px (6.4).
- ✅ Anchos 360, 768, 1024 y 1440 px: sin scroll horizontal de página; solo las
  tablas tienen scroll propio. Se añadieron 390 y 320 px.
- ✅ Texto reflow a 320 px de ancho equivalente: sin desbordamiento de documento
  en las 11 rutas.

### 4.3 Contraste

- ✅ Pares texto/fondo ≥ 4.5:1 (texto normal) y ≥ 3:1 (componentes y texto
  grande). `color-contrast` de axe sin violaciones en 11 rutas × 2 anchos
  (1440 y 390 px). Los 2 casos **incompletos** (texto SVG de la dona y los dos
  párrafos de la barra sticky) están medidos y cumplen: **17.85:1** (ink-900
  sobre blanco) y **7.56:1** (ink-600 sobre blanco al 95 % + `backdrop-blur`).
  Comprobado además que el texto no está tapado (`elementFromPoint` devuelve el
  propio párrafo y `scrollHeight === clientHeight`). Ver 6.1 y el informe §11.4.
- ◐ Revisar en particular `ink-400` sobre blanco y sobre `canvas`, y el texto
  `brand-100` sobre `brand-900` de la barra lateral. axe evalúa el texto
  realmente renderizado con esos tokens y no reporta fallos; los iconos sueltos
  en `ink-400` son contraste de elementos no textuales (1.4.11), que axe no
  cubre.
- ⬜ Badges y chips de clase: el estado no se comunica solo por color. Requiere
  revisión humana del conjunto; ninguna regla automática lo decide.

### 4.4 Lectores de pantalla

- ⬜ NVDA + Firefox y VoiceOver + Safari: landmarks, encabezados, tablas
  (`caption` + `scope`) y formularios.
- ⬜ Los mensajes `role="status"` / `role="alert"` se anuncian tras guardar el
  dictamen o al fallar una validación.
- ⬜ La dona SVG anuncia su `<title>` y la leyenda aporta los valores.

> axe-core sí valida la estructura que consumen los lectores (nombres
> accesibles, roles, jerarquía y `caption`), pero no el anuncio real.

### 4.5 Movimiento y otros

- ✅ `prefers-reduced-motion: reduce` desactiva transiciones y animaciones: 0
  elementos con duración > 0 s en 3 vistas × 2 anchos, y el drawer móvil abre y
  cierra con 0 eventos `transitionstart` (sección 7.2).
- ✅ Sin errores en consola del navegador: 0 errores de `console`, 0 `pageerror`,
  0 peticiones fallidas y 0 respuestas ≥ 400 en 11 rutas × 6 anchos.
- ✅ Lighthouse ≥ 95 en accesibilidad: **100** en `/`, `/cruces` y `/qaqc/1` en
  móvil y en `/` con `--preset=desktop` (sección 7.1).

---

## 5. Limitaciones de la auditoría automatizada (estática)

- El análisis usa `html.parser` de la biblioteca estándar: valida atributos y
  estructura, no estilos calculados ni contraste real.
- No ejecuta JavaScript: los estados dinámicos (drawer abierto, filas de
  evidencia agregadas, indicador de cambios sin guardar) no se auditan.
- No cubre páginas de error 400/403/405/500; `error.html` mantiene un `h1`, skip
  link y navegación por herencia de `base.html`.
- La auditoría de `md:grid-cols-4` se resolvió por búsqueda de uso, no por
  presencia en el CSS compilado (Tailwind v4 genera solo clases utilizadas).

> Corresponden a la auditoría **estática** de las secciones 2 y 3. La auditoría
> con navegador de la sección 6 sí ejecuta JavaScript, mide estilos calculados y
> evalúa contraste real; sus limitaciones están en 6.6.

---

## 6. Auditoría automatizada con navegador (29/10/2026)

Se ejecutó la auditoría pendiente de la sección 4 con **Chrome real**
(156.0.8078.74, headless vía Playwright) y **axe-core 4.14.0**, sobre las 11
rutas del alcance.

**Informe completo:** [`docs/qa-visual/resumen-auditoria.md`](qa-visual/resumen-auditoria.md)
(incluye las capturas y los selectores exactos de cada hallazgo).

**Evidencia:**

| Artefacto | Contenido |
|---|---|
| `docs/qa-visual/resumen-auditoria.md` | Informe consolidado |
| `docs/qa-visual/axe/<slug>.json` | Resultado axe íntegro por ruta (1440 px) |
| `docs/qa-visual/axe/<slug>-390.json` | Pasada móvil por ruta (390 px) |
| `docs/qa-visual/axe/drawer-390.json` | axe sobre el drawer abierto |
| `docs/qa-visual/axe/resumen.md` | Resumen legible por ruta |
| `docs/qa-visual/capturas/` | 33 capturas `fullPage` (1440, 768 y 390 px) |
| `docs/qa-visual/audit-data.json` | Datos crudos: violaciones, desbordes, consola |
| `docs/qa-visual/interaction-data.json` | Teclado, drawer y zoom |

**Scripts:** `scripts/a11y-audit.mjs`, `scripts/a11y-interaction.mjs` y
`scripts/a11y-report.mjs` (dependencias `playwright` y `axe-core` como
devDependencies; se usa el Chrome del sistema, sin descargar navegadores).

### 6.1 Resultado global

| Impacto | 1440 px | 390 px |
|---|---|---|
| critical | 0 | 0 |
| serious | 2 | 3 |
| moderate | 2 | 2 |
| minor | 0 | 0 |
| **Reglas / nodos** | **4 / 15** | **5 / 65** |

> Cifras de la **primera** pasada. Tras las correcciones de la sección 3.2 la
> pasada se repitió con el mismo comando y el resultado es **0 reglas / 0 nodos**
> en ambos anchos (también contra el contenedor Docker en `:8000`).

Las 11 rutas responden `200`. **No hay violaciones `critical` ni desbordamiento
horizontal de página en 1440, 1024, 768, 390, 360 ni 320 px.** Ocho rutas no
presentan ningún hallazgo en ninguno de los dos anchos auditados con axe.

Además de las 4 violaciones, axe devolvió **2 reglas con 7 casos incompletos**
(no decidibles automáticamente), que se listan con su HTML en la sección 3.1 del
informe: `color-contrast` sobre el texto SVG de la dona y sobre dos párrafos de
`/entregas/1/preview` dentro de la barra sticky, y `label-content-name-mismatch`
en el enlace `rel="next"` de las paginaciones.

Tras las correcciones: `label-content-name-mismatch` desaparece y solo quedan
**5 nodos `color-contrast` incompletos**, medidos y conformes (**17.85:1** y
**7.56:1**; ver 3.2 y `resumen-auditoria.md` §11.4).

### 6.2 Hallazgos nuevos (4 reglas) — corregidos

> **Estado:** las cuatro reglas y el `label-content-name-mismatch` incompleto de
> la paginación están corregidos (sección 3.2). Se conserva la tabla como
> registro de la primera pasada.

| Regla | Impacto | Rutas | Nodos | Criterio | Detalle |
|---|---|---|---|---|---|
| `link-in-text-block` | serious | `/cruces` (solo 390 px) | 50 | 1.4.1 | En las tarjetas móviles el número de punto es un enlace `text-brand-700` inline junto a texto `ink-900`: 2.44:1 entre ambos y sin subrayado (solo `hover:underline`). |
| `dlitem` | serious | `/entregas/1/preview` | 12 | 1.3.1 | El `<dl>` de la tarjeta «Archivo» mete dos niveles de `<div>` entre `<dl>` y los `dt`/`dd`; los 12 pares quedan sin `<dl>` padre válido. |
| `definition-list` | serious | `/entregas/1/preview` | 1 | 1.3.1 | Misma causa: el hijo directo del `<dl>` es un `<div>` que envuelve otros `<div>`. |
| `landmark-unique` | moderate | `/entregas`, `/entregas/1/preview` | 2 | 1.3.6 / ARIA | La `<section aria-labelledby>` se convierte en `region` con el mismo nombre que el `div.table-wrap role="region"` de su tabla (dos regiones homónimas). |

Ninguno era `critical`. Los tres primeros son de estructura/estilos y se
corrigieron sin tocar lógica de negocio (detalle en 3.2):

- `link-in-text-block`: subrayado permanente en el enlace de las tarjetas.
- `definition-list` / `dlitem`: un único `<div>` entre `<dl>` y cada par
  `<dt>/<dd>`.
- `landmark-unique`: nombre distinto para la región de la tabla.

**Observación adicional (no detectada por axe):** el drawer móvil declaraba
`aria-modal="true"` sin contener el foco: la 9.ª pulsación de `Tab` lo sacaba al
fondo de la página. No incumplía 2.1.2 (no había trampa), pero se apartaba del
patrón APG de diálogo modal. **Corregido**: ahora el foco cicla dentro del drawer
(6.3.1).

### 6.3 Teclado y drawer (390×844 sobre `/`)

| Comprobación | Resultado | Evidencia |
|---|---|---|
| Primer `Tab` = skip link | ✅ | `<a href="#contenido">Saltar al contenido</a>` |
| El skip link se hace visible al enfocar | ✅ | `151×36` en (16,16), `clip-path: none` |
| `Enter` salta a `#contenido` | ✅ | `hash="#contenido"`, `scrollY=57` |
| El `Tab` siguiente entra en el contenido | ✅ | foco en «Nueva entrega», dentro de `<main>` |
| Abrir drawer: `aria-expanded="true"` | ✅ | `data-open="true"`, overlay activo, scroll bloqueado |
| Foco al abrir | ✅ | botón «Cerrar menú de navegación» |
| `Escape` cierra y `aria-expanded="false"` | ✅ | `data-open="false"` |
| El foco vuelve al botón de apertura | ✅ | `button#drawer-toggle` |
| axe sobre el drawer abierto | ✅ | 0 violaciones |
| Contención del foco en el modal | ✅ | 12 y 20 pulsaciones de `Tab` sin salir; `Shift+Tab` cicla hacia atrás; trap liberado al cerrar (corregido, 3.2 y 6.3.1) |

Orden de tabulación inicial: skip link → hamburguesa → «Nueva entrega» →
«Ver malla» → «Abrir mapa» → «Ver entregas» (coherente con el orden visual).

#### 6.3.1 Contención del foco (tras la corrección)

| Comprobación | Resultado |
|---|---|
| `Tab` con el drawer abierto (20 pulsaciones) | 0 salidas; ciclo botón cerrar → 8 enlaces → botón cerrar |
| `Shift+Tab` desde el primer enfocable | salta al último («Exportar») |
| `Escape` | cierra, `aria-expanded="false"`, foco en `button#drawer-toggle`, `overflow` restaurado |
| `Tab` tras cerrar | el trap está liberado: el foco sigue el orden normal de la página |
| Sin JavaScript | el drawer se muestra como bloque estático con sus 8 enlaces; la hamburguesa (`.js-only`/`.shell-toggle`) queda oculta y el skip link sigue siendo el primer enlace |

### 6.4 Zoom y reflow

| Ruta | `zoom:2` CSS (1440) | Zoom de navegador 200 % (720×450 @ DPR 2) | Reflow 720 px |
|---|---|---|---|
| `/` | desborda (1633/1440) | sin desbordamiento | sin desbordamiento |
| `/qaqc/1` | sin desbordamiento | sin desbordamiento | sin desbordamiento |

El desbordamiento con `zoom` CSS en `/` es un **artefacto del método**: `zoom`
CSS no reduce el viewport de maquetación, así que `(min-width: 1024px)` sigue
activo y el `aside` permanece visible (`display: flex`) mientras el contenido se
dibuja al doble. Al emular el zoom real del navegador (viewport CSS de 720 px y
`devicePixelRatio` 2) la media query se desactiva y no hay desbordamiento. Por
tanto **1.4.4 y 1.4.10 no se incumplen**, aunque conviene revisar el ancho
intrínseco (~816 px) de la tarjeta de concordancia si algún día se fuerza el
layout de escritorio en tablet.

### 6.5 Cobertura del checklist manual

| Ítem (sección 4) | Estado | Evidencia |
|---|---|---|
| Recorrido completo por teclado de las 11 vistas | ✅ | 7.3: 22 recorridos (1440 y 390 px), 0 incidencias |
| Drawer: abre, `Esc`, foco devuelto | ✅ | 6.3 |
| Drawer: el foco no escapa (`Tab`/`Shift+Tab`) | ✅ | 6.3.1 |
| Foco visible en todos los controles | ✅ | 7.3: `outline`/`box-shadow` en cada parada; única excepción medida, el botón nativo del calendario de `input[type=date]`, que dibuja su propio anillo |
| Sin trampas de foco / orden visual | ✅ | 7.3: 0 ciclos y 0 retrocesos de orden en 22 recorridos |
| Envío de formularios sin ratón | ✅ | 7.3: `Enter` dispara `submit`, `Espacio` alterna checkbox, flechas recorren grupos de radio |
| Zoom 200 % sin pérdida | ✅ | 6.4 |
| Anchos 360/768/1024/1440 sin scroll horizontal | ✅ | + 390 y 320 px |
| Reflow a 320 px | ✅ | 6 anchos, 11 rutas |
| Contraste texto/fondo AA | ✅ | axe `color-contrast` sin fallos; incompletos medidos (17.85:1 y 7.56:1) |
| `ink-400` / `brand-100` en particular | ◐ | texto cubierto; iconos (1.4.11) requieren revisión visual humana |
| Estado no solo por color en badges | ⬜ | juicio humano (revisión visual) |
| NVDA/VoiceOver, `role="status"`, dona SVG | ⬜ | requiere lector de pantalla real |
| `prefers-reduced-motion` | ✅ | 7.2: 0 duraciones > 0 s, 0 eventos de transición |
| Sin errores de consola | ✅ | 0 en 11 rutas × 6 anchos |
| Lighthouse ≥ 95 | ✅ | 7.1: accesibilidad **100** en las 4 corridas |

### 6.6 Limitaciones de esta pasada

- axe-core detecta problemas deterministas de marcado y contraste, no la
  experiencia con lector de pantalla ni la semántica de negocio.
- La BD de muestra limita los estados alcanzables (fichas cerradas, errores de
  validación, tablas vacías) y, con ello, la superficie auditada.
- Las capturas corresponden a datos de la BD local, no a un juego de datos
  representativo de producción.

> Lighthouse, `prefers-reduced-motion` y el barrido de teclado, que quedaron
> abiertos en esta pasada, se cerraron en la sección 7.

---

## 7. Cierre de los pendientes automatizables (29/10/2026)

Tres comprobaciones que la sección 6 dejó abiertas se automatizaron y ejecutaron
sobre Chrome 156 real:

| Pendiente | Herramienta | Artefacto |
|---|---|---|
| Lighthouse (puntaje numérico) | `lighthouse` 13.6.0 como devDependency | `docs/qa-visual/lighthouse/` |
| `prefers-reduced-motion: reduce` | Playwright con `reducedMotion: "reduce"` | `docs/qa-visual/reduced-motion.md` |
| Barrido de teclado de las 11 vistas | Playwright + `Tab` real | `docs/qa-visual/teclado-11-vistas.md` |

Scripts nuevos: `scripts/lighthouse-audit.mjs`, `scripts/a11y-reduced-motion.mjs` y
`scripts/a11y-keyboard-sweep.mjs`.

> **Base de medición:** la imagen Docker de `:8000` no se reinició ni se
> reconstruyó. Como el cierre exigió dos cambios de CSS, las comprobaciones
> finales se hicieron sobre `uvicorn` en `http://127.0.0.1:8018`, que sirve el CSS
> recompilado del repositorio. Se conserva la pasada contra `:8000`
> (`teclado-8000-docker.*` y `reduced-motion-8000-docker.*`) como evidencia del
> estado anterior; **la imagen Docker debe reconstruirse para servir el CSS
> corregido**.

### 7.1 Lighthouse

Cuatro corridas con el Chrome del sistema (`--chrome-path`, `--headless=new
--no-sandbox`), categorías `accessibility,performance,best-practices,seo`, JSON y
HTML por corrida:

| Ruta | Forma | Rendimiento | Accesibilidad | Buenas prácticas | SEO |
|---|---|---|---|---|---|
| `/` | móvil (por defecto) | 100 | **100** | 100 | 100 |
| `/cruces` | móvil | 99 | **100** | 100 | 100 |
| `/qaqc/1` | móvil | 100 | **100** | 100 | 100 |
| `/` | `--preset=desktop` | 100 | **100** | 100 | 100 |

El 99 de `/cruces` proviene de `first-contentful-paint` (0.93) y
`largest-contentful-paint` (0.99), no de accesibilidad. Las métricas de laboratorio
quedan en `docs/qa-visual/lighthouse/resumen.md` (FCP/LCP 1.1–1.7 s en móvil y
0.3 s en escritorio, TBT 0 ms, CLS 0).

Al ser **100** en accesibilidad en las cuatro corridas no hubo audits que corregir
en plantillas ni CSS por este motivo. Los cuatro recorridos se repitieron contra el
CSS recompilado (`lighthouse/uvicorn-8018/`) con puntajes idénticos.

### 7.2 `prefers-reduced-motion: reduce`

Se comparó cada vista consigo misma, con y sin la preferencia, sobre el mismo
conjunto de elementos (interactivos, tarjetas, botones, badges, tablas, drawer):

| Vista | Ancho | Elementos | Con transición sin la preferencia | Con transición en `reduce` | Duraciones en `reduce` |
|---|---|---|---|---|---|
| `/` | 1440 | 51 | 6 | 0 | `0s` |
| `/cruces` | 1440 | 391 | 52 | 0 | `0s` |
| `/qaqc/1` | 1440 | 65 | 4 | 0 | `0s` |
| `/` | 390 | 51 | 6 | 0 | `0s` |
| `/cruces` | 390 | 391 | 52 | 0 | `0s` |
| `/qaqc/1` | 390 | 65 | 4 | 0 | `0s` |

Drawer móvil (390×844) con `reduce`: `transition-duration` `0s` en drawer y velo,
apertura en el mismo fotograma, **0 eventos `transitionstart`** al abrir y al
cerrar, y el foco sigue entrando en «Cerrar menú de navegación» y volviendo a
`#drawer-toggle`.

**Corrección aplicada.** La regla de `input.css` usaba `.001ms !important`, que el
navegador resuelve como `1e-06s`: no es `0s` y además dispara `transitionstart`.
Se cambió a `0s` (con `transition-delay` y `animation-delay` a `0s`). Nada en
`app.js` depende de `transitionend`, así que no hay regresión funcional.

### 7.3 Barrido de teclado de las 11 vistas

22 recorridos (11 rutas × 1440 y 390 px) con `Tab` real, hasta cerrar el ciclo
(mínimo 80 pulsaciones, ampliado cuando la vista tiene más enfocables: `/cruces`
móvil necesita 115). Resultado: **0 incidencias**.

| Ruta | Interactivos visibles | Alcanzados | Pulsaciones (1440 / 390) |
|---|---|---|---|
| `/` | 15 / 8 | 15 / 8 | 16 / 9 |
| `/entregas` | 19 / 12 | 19 / 12 | 20 / 13 |
| `/entregas/1/preview` | 13 / 6 | 13 / 6 | 14 / 7 |
| `/cruces` | 72 / 115 | 72 / 115 | 73 / 115 |
| `/cruces/81/2008` | 14 / 7 | 14 / 7 | 14 / 7 |
| `/qaqc` | 123 / 116 | 123 / 116 | 124 / 117 |
| `/qaqc/1` | 27 / 20 | 27 / 20 | 33 / 26 |
| `/reemplazos` | 14 / 7 | 14 / 7 | 14 / 7 |
| `/malla` | 19 / 12 | 19 / 12 | 20 / 13 |
| `/mapa` | 11 / 4 | 11 / 4 | 13 / 6 |
| `/exportar` | 10 / 3 | 10 / 3 | 10 / 3 |

Comprobaciones por recorrido:

- El primer `Tab` llega al skip link en las 11 vistas y los dos anchos.
- Cero elementos ocultos enfocados y cero ciclos de foco fuera del drawer.
- Cero retrocesos en el orden del documento.
- Todos los interactivos visibles y habilitados son alcanzables. Los radios de un
  mismo `name` cuentan como una parada (semántica nativa): se verificó que la
  parada del grupo es alcanzable y que `ArrowDown` recorre el grupo, en los 12
  grupos de las cuatro vistas que los usan.
- `Enter` sobre el botón de envío dispara el `submit` (interceptado y cancelado en
  la prueba, sin tocar la base) en las vistas con formulario válido; `Espacio`
  alterna el checkbox de `/qaqc/1`.

**Incidencia corregida.** El barrido detectó que la hamburguesa del menú móvil era
visible y enfocable a 1440 px en **las 11 vistas**: con la barra lateral en
pantalla, abrirla dejaba `aria-expanded="true"` y el velo sobre la página, pero el
drawer seguía en `display:none` (la media query lo oculta a partir de 1024 px). Era
un control sin efecto que además oscurecía la interfaz hasta pulsar `Esc`.
Corrección en `app/static/css/input.css`:

```css
@media (min-width: 1024px) {
  .shell-drawer { display: none !important; }
  .shell-toggle { display: none !important; }  /* nuevo */
}
```

Tras recompilar el CSS, la comprobación de cromo del barrido pasa de 11 avisos a
0 y la hamburguesa sigue disponible por debajo de 1024 px.

**Nota de medición.** El cuarto paso de tabulación de `input[type=date]` pertenece
al botón nativo del calendario dentro del shadow DOM del control; ahí el anillo lo
dibuja Chrome. Está registrado en el informe con su captura
(`docs/qa-visual/capturas/foco-fecha-nativo.png`).

### 7.4 Regresión posterior a los cambios

Los dos ajustes de CSS se aplicaron con axe-core 4.14 como red de seguridad
(`scripts/a11y-axe-recheck.mjs`, mismas etiquetas WCAG que `a11y-audit.mjs`):

```text
node scripts/a11y-axe-recheck.mjs --base http://127.0.0.1:8018
[axe] 0 violaciones en 11 rutas × 1440/390 px
```

Evidencia: `docs/qa-visual/axe-recheck.json`. **0 violaciones** en los 22
recorridos, igual que antes de los cambios.

### 7.5 Build de CSS determinista

Al añadir los informes de Lighthouse (`docs/qa-visual/lighthouse/*.report.html`) el
escaneo automático de Tailwind empezó a leer ese HTML y a generar utilidades
sueltas (`.container`, `.invert`, `[xml:lang]`, `.shadow-lg`…): 14 reglas y unos
2.4 KB que no usa ninguna plantilla. Se excluyó la carpeta de documentación:

```css
@source not "../../../docs";
```

Así `app.css` pasa de 51 964 a **48 687 bytes** y el tamaño deja de depender de
los artefactos generados. Se verificó que no se perdió ninguna clase en uso: de
las 37 que desaparecen, ninguna está referenciada en `app/templates`,
`app/static/js` ni `app/*.py`; y la comparación píxel a píxel de las 11 vistas
antes/después del cambio da **idénticas a 390 px** y, a 1440 px, solo los 265 px
de la cabecera donde estaba la hamburguesa eliminada.

---

## 8. Pantallas de autenticación (30/10/2026)

Fase A6.10 del plan de autenticación: axe-core sobre las pantallas nuevas a
**1440 y 390 px**, más comprobaciones de teclado. Informe completo en
[`docs/qa-visual/resumen-auth.md`](qa-visual/resumen-auth.md); el E2E del flujo
está en el mismo documento (§3).

### 8.1 Alcance

| Pantalla | Estado auditado |
|---|---|
| `/auth/login` | anónimo |
| `/auth/recuperar` | anónimo |
| `/auth/restablecer?token=invalido` | enlace vencido/inválido |
| `/auth/seguridad` | autenticador sin activar, códigos de respaldo recién generados y enrolado |
| `/auth/mfa` | challenge pendiente (método TOTP) |
| `/auth/usuarios` | administración con tabla y acciones por fila |
| `/auth/auditoria` | eventos con detalle y exportación |

`/auth/cambiar-password` no estaba en el alcance de A6.10 y queda pendiente.

### 8.2 Resultado

| Impacto | Antes (1440) | Después (1440 y 390) |
|---|---|---|
| critical | 0 | 0 |
| serious | 3 (`color-contrast` en el pie de las tres pantallas anónimas) | **0** |
| moderate | 2 (`landmark-unique` en `/auth/usuarios` y `/auth/auditoria`) | **0** |
| minor | 0 | 0 |
| **Reglas / nodos** | **5 / 6** | **0 / 0** (16 auditorías) |

Evidencia: `docs/qa-visual/axe-auth/<slug>-<ancho>.json`,
`docs/qa-visual/axe-auth/resumen.md`, capturas en
`docs/qa-visual/capturas-auth/` y las salidas de las corridas en
`docs/qa-visual/logs-auth/` (antes: `a11y-run-2.log` y `a11y-run-3.log`;
después: `a11y-run-final.log`).

Queda **1 nodo incompleto** de `color-contrast` en `/auth/seguridad` a 390 px
(celda del agente de usuario recortada por `truncate`); medido sobre el estilo
calculado da **17.85:1** (`#0f172a` sobre blanco), conforme a 1.4.3.

### 8.3 Correcciones

| Hallazgo | Criterio | Corrección |
|---|---|---|
| Pie del layout de auth (`#64748b` sobre `#f4f7f6`, 4.41:1). | 1.4.3 | `.auth-footer` pasa a `text-ink-600` (7.04:1). |
| Dos breadcrumbs idénticos en `/auth/usuarios` y `/auth/auditoria` (el de la topbar, alimentado por `migas`, y el de `ui.page_header`). | 1.3.6 / ARIA | Se quita `breadcrumb=` de `ui.page_header` en esas dos vistas; queda la miga de la topbar, igual que en el resto de la aplicación. |
| La región de la tabla (`role="region" aria-label="Cuentas registradas"`) compartía nombre con la `<section aria-labelledby="listado-usuarios">`. | 1.3.6 / ARIA | La región pasa a llamarse «Listado de cuentas registradas». |

No se modificaron textos normativos ni lógica de negocio.

### 8.4 Teclado

Seis recorridos (`/auth/login`, `/auth/mfa` y `/auth/usuarios` × 1440 y 390 px):
**0 trampas**, **0 elementos ocultos enfocados**, el skip link es el primer
tabulable del documento y se hace visible al enfocarlo, el primer `Tab` deja un
anillo de foco visible y `Enter` dispara el `submit` del formulario válido
(capturado y cancelado en la prueba, sin tocar la base). Detalle en
`docs/qa-visual/axe-auth/teclado-auth.json`.

> El login y el challenge llevan `autofocus` en el primer campo: el primer `Tab`
> avanza al control siguiente —visible y con anillo— en lugar del skip link, que
> sigue siendo alcanzable con `Shift+Tab`. Es una decisión de producto
> documentada, no una trampa de foco.

### 8.5 Pendientes de estas pantallas

1. **Lector de pantalla** (NVDA + Firefox, VoiceOver + Safari): anuncio de
   `role="status"`/`role="alert"` tras un error de credenciales o de código, y
   lectura de la clave manual del autenticador y de los códigos de respaldo.
2. **Revisión visual humana** de los badges de estado para confirmar que el
   estado no se comunica solo por color.
3. Auditoría de `/auth/cambiar-password` y de la variante `?metodo=email` de
   `/auth/mfa` en la próxima pasada.


