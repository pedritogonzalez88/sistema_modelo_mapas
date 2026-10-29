# `prefers-reduced-motion: reduce` — Validador EET (AEP)

**Fecha:** 2026-10-29  
**Base:** http://127.0.0.1:8018  

> Medición sobre `uvicorn` en el puerto 8018 con el CSS recompilado. La imagen Docker de `:8000` sigue sirviendo el CSS anterior (`transition-duration: 1e-06s`); su pasada queda en `reduced-motion-8000-docker.md`.

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
| `/` | 1440 px | 51 | 6 | 0 | 0 | 0s | cumple |
| `/cruces` | 1440 px | 391 | 52 | 0 | 0 | 0s | cumple |
| `/qaqc/1` | 1440 px | 65 | 4 | 0 | 0 | 0s | cumple |
| `/` | 390 px | 51 | 6 | 0 | 0 | 0s | cumple |
| `/cruces` | 390 px | 391 | 52 | 0 | 0 | 0s | cumple |
| `/qaqc/1` | 390 px | 65 | 4 | 0 | 0 | 0s | cumple |

> «Con transición (sin preferencia)» documenta cuántos elementos sí animan cuando el
> usuario no ha pedido movimiento reducido: demuestra que la media query tiene algo que
> anular y que la comparación es significativa.

## Drawer móvil bajo `reduce` (390×844)

| Comprobación | Valor |
|---|---|
| Drawer cerrado — `transition-duration` | `0s` (propiedad `all`) |
| Overlay cerrado — `transition-duration` | `0s` |
| Tras el clic, `data-open` | `true` en 0 ms (1 fotograma) |
| Drawer abierto — `transition-duration` | `0s` |
| Overlay abierto — `transition-duration` | `0s` |
| Eventos `transitionstart` al abrir | 0 |
| Eventos `animationstart` al abrir | 0 |
| Foco tras abrir | `Cerrar menú de navegación` |
| Tras `Escape`: `data-open` | `false` |
| Tras `Escape`: eventos acumulados | 0 transición / 0 animación |
| Tras `Escape`: foco devuelto a | `drawer-toggle` |

## Conclusión

**Cumple.** En las tres vistas y los dos anchos, con `prefers-reduced-motion: reduce`
ningún elemento auditado conserva transición ni animación con duración mayor que 0 s, y el
drawer móvil abre y cierra sin transición perceptible (0 eventos `transitionstart`).

Artefacto crudo: [`reduced-motion.json`](./reduced-motion.json).
