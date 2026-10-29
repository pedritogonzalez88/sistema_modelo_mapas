# `prefers-reduced-motion: reduce` — Validador EET (AEP)

**Fecha:** 2026-10-29  
**Base:** http://localhost:8000  
**Navegador:** Chrome 156.0.8078.74 vía Playwright (`channel: "chrome"`, headless)  
**Script:** `scripts/a11y-reduced-motion.mjs`

## Método

Se carga cada vista dos veces con el mismo conjunto de elementos auditados
(`a[href]`, `button`, `input`, `select`, `textarea`, `summary`, `[tabindex]`, `.card`,
`.btn`, `.badge`, `.table`, `.table-wrap`, `.shell-*`, `.pagination__page`, `tr`…) y se
lee `getComputedStyle`: `transition-property`, `transition-duration`, `animation-name` y
`animation-duration`. La primera pasada usa `reducedMotion: "no-preference"` (línea base);
la segunda, `reducedMotion: "reduce"`.

Se considera incumplimiento **cualquier duración mayor que 0 s** en un elemento que declare
una transición o animación.

## Resultado por vista y ancho

| Vista | Ancho | Elementos | Con transición (sin preferencia) | Con transición (reduce) | Con animación (reduce) | Duraciones en `reduce` | Estado |
|---|---|---|---|---|---|---|---|
| `/` | 1440 px | 51 | 6 | 51 | 0 | 1e-06s | **51 incumplen** |
| `/cruces` | 1440 px | 391 | 52 | 391 | 0 | 1e-06s | **391 incumplen** |
| `/qaqc/1` | 1440 px | 65 | 4 | 65 | 0 | 1e-06s | **65 incumplen** |
| `/` | 390 px | 51 | 6 | 51 | 0 | 1e-06s | **51 incumplen** |
| `/cruces` | 390 px | 391 | 52 | 391 | 0 | 1e-06s | **391 incumplen** |
| `/qaqc/1` | 390 px | 65 | 4 | 65 | 0 | 1e-06s | **65 incumplen** |

> «Con transición (sin preferencia)» documenta cuántos elementos sí animan cuando el
> usuario no ha pedido movimiento reducido: demuestra que la media query tiene algo que
> anular y que la comparación es significativa.

## Drawer móvil bajo `reduce` (390×844)

| Comprobación | Valor |
|---|---|
| Drawer cerrado — `transition-duration` | `1e-06s` (propiedad `all`) |
| Overlay cerrado — `transition-duration` | `1e-06s` |
| Tras el clic, `data-open` | `true` en 0 ms (1 fotograma) |
| Drawer abierto — `transition-duration` | `1e-06s` |
| Overlay abierto — `transition-duration` | `1e-06s` |
| Eventos `transitionstart` al abrir | 0 |
| Eventos `animationstart` al abrir | 0 |
| Foco tras abrir | `Cerrar menú de navegación` |
| Tras `Escape`: `data-open` | `false` |
| Tras `Escape`: eventos acumulados | 3 transición / 0 animación |
| Tras `Escape`: foco devuelto a | `drawer-toggle` |

## Incidencias

### `/` @ 1440 px — 51 elementos

| Selector | `transition-property` | `transition-duration` | `animation-duration` |
|---|---|---|---|
| `a.sr-only.focus:not-sr-only` | `all` | `1e-06s` | `1e-06s` |
| `div#drawer` | `all` | `1e-06s` | `1e-06s` |
| `button.js-only.items-center` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |

### `/cruces` @ 1440 px — 391 elementos

| Selector | `transition-property` | `transition-duration` | `animation-duration` |
|---|---|---|---|
| `a.sr-only.focus:not-sr-only` | `all` | `1e-06s` | `1e-06s` |
| `div#drawer` | `all` | `1e-06s` | `1e-06s` |
| `button.js-only.items-center` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |

### `/qaqc/1` @ 1440 px — 65 elementos

| Selector | `transition-property` | `transition-duration` | `animation-duration` |
|---|---|---|---|
| `a.sr-only.focus:not-sr-only` | `all` | `1e-06s` | `1e-06s` |
| `div#drawer` | `all` | `1e-06s` | `1e-06s` |
| `button.js-only.items-center` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |

### `/` @ 390 px — 51 elementos

| Selector | `transition-property` | `transition-duration` | `animation-duration` |
|---|---|---|---|
| `a.sr-only.focus:not-sr-only` | `all` | `1e-06s` | `1e-06s` |
| `div#drawer` | `all` | `1e-06s` | `1e-06s` |
| `button.js-only.items-center` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |

### `/cruces` @ 390 px — 391 elementos

| Selector | `transition-property` | `transition-duration` | `animation-duration` |
|---|---|---|---|
| `a.sr-only.focus:not-sr-only` | `all` | `1e-06s` | `1e-06s` |
| `div#drawer` | `all` | `1e-06s` | `1e-06s` |
| `button.js-only.items-center` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |

### `/qaqc/1` @ 390 px — 65 elementos

| Selector | `transition-property` | `transition-duration` | `animation-duration` |
|---|---|---|---|
| `a.sr-only.focus:not-sr-only` | `all` | `1e-06s` | `1e-06s` |
| `div#drawer` | `all` | `1e-06s` | `1e-06s` |
| `button.js-only.items-center` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |
| `a.shell-link` | `all` | `1e-06s` | `1e-06s` |

Artefacto crudo: [`reduced-motion.json`](./reduced-motion.json).
