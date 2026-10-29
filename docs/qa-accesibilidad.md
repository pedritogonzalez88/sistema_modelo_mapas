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

Resultado: **104 pruebas de integración en verde** (incluye las de rutas y las de
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
uv run pytest -q                 -> 129 passed, 104 deselected
uv run pytest -m integration -q  -> 104 passed, 129 deselected
```

### 2.3 Verificación de CSS servido

- `app/static/css/app.css` local: 52 050 bytes (entre 10 KB y 60 KB).
- CSS servido por el contenedor: 47 177 bytes, `200` con `text/css`.
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

---

## 4. Revisión manual pendiente

Las comprobaciones siguientes requieren navegador, dispositivo o lector de
pantalla y **no** se pudieron automatizar en este cierre. Quedan pendientes y
deben registrarse aquí al completarse:

### 4.1 Teclado

- [ ] Recorrer las 11 vistas solo con `Tab`, `Shift+Tab`, `Enter`, `Espacio` y `Esc`.
- [ ] El drawer móvil abre con el botón, cierra con `Esc` y devuelve el foco al
      botón de apertura.
- [ ] El foco visible (anillo `brand-500`) se percibe en enlaces, botones, campos,
      radios, checkboxes y contenedores de tabla.
- [ ] No hay trampas de foco; el orden de tabulación sigue el orden visual.
- [ ] Los formularios se pueden enviar sin ratón, incluida la carga de archivo.

### 4.2 Zoom y responsive

- [ ] Zoom 200 % sin pérdida de contenido ni solapamientos (WCAG 1.4.4 y 1.4.10).
- [ ] Anchos 360, 768, 1024 y 1440 px: sin scroll horizontal de página; solo las
      tablas tienen scroll propio.
- [ ] Texto reflow a 320 px de ancho equivalente.

### 4.3 Contraste

- [ ] Pares texto/fondo ≥ 4.5:1 (texto normal) y ≥ 3:1 (componentes y texto grande).
- [ ] Revisar en particular `ink-400` sobre blanco y sobre `canvas`, y el texto
      `brand-100` sobre `brand-900` de la barra lateral.
- [ ] Badges y chips de clase: el estado no se comunica solo por color.

### 4.4 Lectores de pantalla

- [ ] NVDA + Firefox y VoiceOver + Safari: landmarks, encabezados, tablas
      (`caption` + `scope`) y formularios.
- [ ] Los mensajes `role="status"` / `role="alert"` se anuncian tras guardar el
      dictamen o al fallar una validación.
- [ ] La dona SVG anuncia su `<title>` y la leyenda aporta los valores.

### 4.5 Movimiento y otros

- [ ] `prefers-reduced-motion: reduce` desactiva transiciones y animaciones.
- [ ] Sin errores en consola del navegador.
- [ ] Lighthouse (o `npx @axe-core/cli`) ≥ 95 en accesibilidad sobre `/` y
      `/qaqc/{id}`; no se ejecutó por no disponer de Chrome en el entorno de
      cierre.

---

## 5. Limitaciones de la auditoría automatizada

- El análisis usa `html.parser` de la biblioteca estándar: valida atributos y
  estructura, no estilos calculados ni contraste real.
- No ejecuta JavaScript: los estados dinámicos (drawer abierto, filas de
  evidencia agregadas, indicador de cambios sin guardar) no se auditan.
- No cubre páginas de error 400/403/405/500; `error.html` mantiene un `h1`, skip
  link y navegación por herencia de `base.html`.
- La auditoría de `md:grid-cols-4` se resolvió por búsqueda de uso, no por
  presencia en el CSS compilado (Tailwind v4 genera solo clases utilizadas).
