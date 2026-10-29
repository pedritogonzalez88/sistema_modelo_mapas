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

- `app/static/css/app.css` local: 51 964 bytes (entre 10 KB y 60 KB).
- CSS servido por el contenedor: 47 343 bytes, `200` con `text/css`.
- Presentes las clases muestreadas: `.md\:hidden`, `.lg\:col-span-2`,
  `.lg\:grid-cols-3`, `.sticky`, `.clase-9`, `.banner`, `.pagination`,
  `.dirty-indicator`, `.badge--warn`.
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

> Estado actualizado tras la auditoría con navegador del 29/10/2026 (sección 6):
> ✅ cubierto · ◐ cobertura parcial · ⬜ sigue pendiente.

### 4.1 Teclado

- ⬜ Recorrer las 11 vistas solo con `Tab`, `Shift+Tab`, `Enter`, `Espacio` y `Esc`.
  Solo se verificó el arranque en `/` (6 primeras paradas) y el drawer.
- ✅ El drawer móvil abre con el botón, cierra con `Esc` y devuelve el foco al
  botón de apertura. 6/6 pasos verdes (6.3).
- ✅ El drawer **contiene el foco** mientras está abierto: `Tab` y `Shift+Tab`
  ciclan dentro, 20 pulsaciones sin salir, y al cerrar el trap se libera (6.3).
- ◐ El foco visible (anillo `brand-500`) se percibe en enlaces, botones, campos,
  radios, checkboxes y contenedores de tabla. Comprobado `outline: solid 2px
  #1E8A69` (offset 2px) con teclado en el skip link, el botón hamburguesa y un
  `input[type=file]`; falta el barrido del resto de controles en las 11 vistas.
- ◐ El orden de tabulación sigue el orden visual. Las 6 primeras paradas de `/`
  avanzan en `y` creciente y el drawer ya no deja escapar el foco, pero no se
  recorrió cada vista completa.
- ⬜ Los formularios se pueden enviar sin ratón, incluida la carga de archivo.

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

- ⬜ `prefers-reduced-motion: reduce` desactiva transiciones y animaciones.
- ✅ Sin errores en consola del navegador: 0 errores de `console`, 0 `pageerror`,
  0 peticiones fallidas y 0 respuestas ≥ 400 en 11 rutas × 6 anchos.
- ◐ Lighthouse (o `npx @axe-core/cli`) ≥ 95 en accesibilidad sobre `/` y
  `/qaqc/{id}`. No se ejecutó Lighthouse (no está instalado en el entorno); se
  usó axe-core 4.14 —su motor de la categoría de accesibilidad— sin violaciones
  `critical` ni `serious` en **11 de las 11** rutas (1440 y 390 px).

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
| Recorrido completo por teclado de las 11 vistas | ⬜ | solo `/` y el drawer |
| Drawer: abre, `Esc`, foco devuelto | ✅ | 6.3 |
| Drawer: el foco no escapa (`Tab`/`Shift+Tab`) | ✅ | 6.3.1 |
| Foco visible en todos los controles | ◐ | anillo `brand-500` verificado en 3 controles |
| Sin trampas de foco / orden visual | ◐ | trap solo en el drawer; falta el barrido por vista |
| Envío de formularios sin ratón | ⬜ | requiere prueba manual |
| Zoom 200 % sin pérdida | ✅ | 6.4 |
| Anchos 360/768/1024/1440 sin scroll horizontal | ✅ | + 390 y 320 px |
| Reflow a 320 px | ✅ | 6 anchos, 11 rutas |
| Contraste texto/fondo AA | ✅ | axe `color-contrast` sin fallos; incompletos medidos (17.85:1 y 7.56:1) |
| `ink-400` / `brand-100` en particular | ◐ | texto cubierto; iconos (1.4.11) no |
| Estado no solo por color en badges | ⬜ | juicio humano |
| NVDA/VoiceOver, `role="status"`, dona SVG | ⬜ | requiere AT |
| `prefers-reduced-motion` | ⬜ | no probado |
| Sin errores de consola | ✅ | 0 en 11 rutas × 6 anchos |
| Lighthouse ≥ 95 | ◐ | no ejecutado; axe como motor equivalente |

### 6.6 Limitaciones de esta pasada

- axe-core detecta problemas deterministas de marcado y contraste, no la
  experiencia con lector de pantalla ni la semántica de negocio.
- La BD de muestra limita los estados alcanzables (fichas cerradas, errores de
  validación, tablas vacías) y, con ello, la superficie auditada.
- No se ejecutó Lighthouse; el puntaje numérico queda pendiente.
- Las capturas corresponden a datos de la BD local, no a un juego de datos
  representativo de producción.

