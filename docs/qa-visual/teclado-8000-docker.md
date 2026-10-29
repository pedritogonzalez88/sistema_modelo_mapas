# Barrido de teclado — 11 vistas (Validador EET)

**Fecha:** 2026-10-29  
**Base:** http://localhost:8000  
**Navegador:** Chrome 156.0.8078.74 vía Playwright (`channel: "chrome"`, headless)  
**Script:** `scripts/a11y-keyboard-sweep.mjs`  
**Anchos:** 1440 y 390 px

## Método

Para cada ruta y ancho se recorre la página con `Tab` sin usar el ratón. Se
registran 80 pulsaciones como mínimo; si la vista tiene más enfocables, el
recorrido continúa (hasta cerrar el ciclo) para poder verificar la alcanzabilidad
completa. En cada pulsación se lee `document.activeElement` junto con su selector,
`getComputedStyle` (display/visibility), `offsetParent` y posición.

Conjunto obligatorio de interactivos: `a[href]`, `button`, `input:not([type=hidden])`,
`select` y `textarea` **visibles, habilitados y no excluidos con `tabindex="-1"`**.
Los radios de un mismo `name` cuentan como una sola parada de tabulación (semántica
nativa de los grupos de radio), y se verifican aparte con las flechas.

Criterios:

1. No se enfocan elementos ocultos (`display:none`, `visibility:hidden`, tamaño 0 u
   `offsetParent:null` sin `position:fixed`).
2. No hay ciclos de foco (trampas) fuera del drawer.
3. Todos los interactivos del conjunto obligatorio son alcanzables.
4. El orden de foco no retrocede en el orden del documento y el primer `Tab` llega al
   skip link.
5. Si la barra lateral está visible (escritorio), la hamburguesa del menú móvil no debe
   ser enfocable: abriría un drawer oculto y dejaría solo el velo sobre la página.

## Resumen

| # | Ruta | Interactivos visibles | Alcanzados | Pulsaciones | Incidencias |
|---|---|---|---|---|---|
| 1 | `/` @1440 | 16 | 16 | 17 (fin en 18) | **1** |
| 2 | `/` @390 | 8 | 8 | 9 (fin en 10) | 0 |
| 3 | `/entregas` @1440 | 20 | 20 | 21 (fin en 22) | **1** |
| 4 | `/entregas` @390 | 12 | 12 | 13 (fin en 14) | 0 |
| 5 | `/entregas/1/preview` @1440 | 14 | 14 | 15 (fin en 16) | **1** |
| 6 | `/entregas/1/preview` @390 | 6 | 6 | 7 (fin en 8) | 0 |
| 7 | `/cruces` @1440 | 73 | 73 | 74 (fin en 75) | **1** |
| 8 | `/cruces` @390 | 115 | 115 | 115 (fin en 116) | 0 |
| 9 | `/cruces/81/2008` @1440 | 15 | 15 | 15 (fin en 16) | **1** |
| 10 | `/cruces/81/2008` @390 | 7 | 7 | 7 (fin en 8) | 0 |
| 11 | `/qaqc` @1440 | 124 | 124 | 125 (fin en 126) | **1** |
| 12 | `/qaqc` @390 | 116 | 116 | 117 (fin en 118) | 0 |
| 13 | `/qaqc/1` @1440 | 28 | 28 | 34 (fin en 35) | **1** |
| 14 | `/qaqc/1` @390 | 20 | 20 | 26 (fin en 27) | 0 |
| 15 | `/reemplazos` @1440 | 15 | 15 | 15 (fin en 16) | **1** |
| 16 | `/reemplazos` @390 | 7 | 7 | 7 (fin en 8) | 0 |
| 17 | `/malla` @1440 | 20 | 20 | 21 (fin en 22) | **1** |
| 18 | `/malla` @390 | 12 | 12 | 13 (fin en 14) | 0 |
| 19 | `/mapa` @1440 | 12 | 12 | 14 (fin en 15) | **1** |
| 20 | `/mapa` @390 | 4 | 4 | 6 (fin en 7) | 0 |
| 21 | `/exportar` @1440 | 11 | 11 | 11 (fin en 12) | **1** |
| 22 | `/exportar` @390 | 3 | 3 | 3 (fin en 4) | 0 |

**11 incidencias** acumuladas; el detalle está en la sección siguiente.

Pulsaciones pedidas por el encargo: 80. «Fin en N» indica la pulsación en la
que el foco abandona el documento (fin natural del ciclo).

## Incidencias

### `/` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/entregas` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/entregas/1/preview` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/cruces` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/cruces/81/2008` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/qaqc` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/qaqc/1` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/reemplazos` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/malla` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/mapa` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

### `/exportar` @ 1440 px

- La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.

## Grupos de radio

En HTML nativo, un grupo de radios (mismo `name`) es **una sola parada de
tabulación**: el radio marcado, o el primero si ninguno lo está. Los demás miembros
se recorren con las flechas. Por eso no se cuentan como «no alcanzados»: se verifica
que la parada del grupo sí sea alcanzable y que `ArrowDown` mueva el foco dentro del
grupo.

| Ruta | Ancho | Grupo | Miembros | Parada alcanzable | Flechas |
|---|---|---|---|---|---|
| `/entregas` | 1440 px | `asistente_letra` | 3 | sí | sí |
| `/entregas` | 1440 px | `tipo` | 3 | sí | sí |
| `/entregas` | 390 px | `asistente_letra` | 3 | sí | sí |
| `/entregas` | 390 px | `tipo` | 3 | sí | sí |
| `/qaqc/1` | 1440 px | `dictamen` | 2 | sí | sí |
| `/qaqc/1` | 1440 px | `clase_definitiva` | 9 | sí | sí |
| `/qaqc/1` | 1440 px | `causa_descarte` | 3 | sí | sí |
| `/qaqc/1` | 390 px | `dictamen` | 2 | sí | sí |
| `/qaqc/1` | 390 px | `clase_definitiva` | 9 | sí | sí |
| `/qaqc/1` | 390 px | `causa_descarte` | 3 | sí | sí |
| `/reemplazos` | 1440 px | `causa` | 4 | sí | sí |
| `/reemplazos` | 390 px | `causa` | 4 | sí | sí |

## Cromo en escritorio

| Ruta | Barra lateral visible | Hamburguesa visible | Estado |
|---|---|---|---|
| `/` | sí | sí | **control redundante** |
| `/entregas` | sí | sí | **control redundante** |
| `/entregas/1/preview` | sí | sí | **control redundante** |
| `/cruces` | sí | sí | **control redundante** |
| `/cruces/81/2008` | sí | sí | **control redundante** |
| `/qaqc` | sí | sí | **control redundante** |
| `/qaqc/1` | sí | sí | **control redundante** |
| `/reemplazos` | sí | sí | **control redundante** |
| `/malla` | sí | sí | **control redundante** |
| `/mapa` | sí | sí | **control redundante** |
| `/exportar` | sí | sí | **control redundante** |

## Trayectorias registradas

### `/`

<details><summary>1440 px — 17 pulsaciones, el foco sale del documento en la 18</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `a[href="/entregas"].btn.btn--primary` | Nueva entrega | contenido |
| 12 | `a[href="/malla"].btn.btn--secondary` | Ver malla | contenido |
| 13 | `a[href="/mapa"].btn.btn--secondary` | Abrir mapa | contenido |
| 14 | `a[href="/entregas"].btn.btn--secondary` | Ver entregas | contenido |
| 15 | `a[href="/qaqc"].btn.btn--secondary` | Abrir fichas | contenido |
| 16 | `a[href="/exportar"].btn.btn--secondary` | Exportar | contenido |
| 17 | `div.table-wrap` | Últimas entregas recibidas | contenido |

</details>

<details><summary>390 px — 9 pulsaciones, el foco sale del documento en la 10</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `a[href="/entregas"].btn.btn--primary` | Nueva entrega | contenido |
| 4 | `a[href="/malla"].btn.btn--secondary` | Ver malla | contenido |
| 5 | `a[href="/mapa"].btn.btn--secondary` | Abrir mapa | contenido |
| 6 | `a[href="/entregas"].btn.btn--secondary` | Ver entregas | contenido |
| 7 | `a[href="/qaqc"].btn.btn--secondary` | Abrir fichas | contenido |
| 8 | `a[href="/exportar"].btn.btn--secondary` | Exportar | contenido |
| 9 | `div.table-wrap` | Últimas entregas recibidas | contenido |

</details>

### `/entregas`

<details><summary>1440 px — 21 pulsaciones, el foco sale del documento en la 22</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `input#campo-file` | — | contenido |
| 12 | `input.peer.sr-only` | — | contenido |
| 13 | `input#campo-semana` | — | contenido |
| 14 | `input.peer.sr-only` | — | contenido |
| 15 | `button.btn.btn--primary` | Subir y previsualizar | contenido |
| 16 | `select#f-asistente` | Todas Evaluadora A Evaluadora B Evaluadora C | contenido |
| 17 | `select#f-semana` | Todas 2026-S2 2026-S1 | contenido |
| 18 | `select#f-estado` | Todos (6) Pendiente (0) Aceptada (6) Rechazada (0) | contenido |
| 19 | `button.btn.btn--secondary` | Aplicar filtros | contenido |
| 20 | `a[href="/entregas"].btn.btn--ghost` | Limpiar | contenido |
| 21 | `div.table-wrap` | Entregas registradas por semana | contenido |

</details>

<details><summary>390 px — 13 pulsaciones, el foco sale del documento en la 14</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `input#campo-file` | — | contenido |
| 4 | `input.peer.sr-only` | — | contenido |
| 5 | `input#campo-semana` | — | contenido |
| 6 | `input.peer.sr-only` | — | contenido |
| 7 | `button.btn.btn--primary` | Subir y previsualizar | contenido |
| 8 | `select#f-asistente` | Todas Evaluadora A Evaluadora B Evaluadora C | contenido |
| 9 | `select#f-semana` | Todas 2026-S2 2026-S1 | contenido |
| 10 | `select#f-estado` | Todos (6) Pendiente (0) Aceptada (6) Rechazada (0) | contenido |
| 11 | `button.btn.btn--secondary` | Aplicar filtros | contenido |
| 12 | `a[href="/entregas"].btn.btn--ghost` | Limpiar | contenido |
| 13 | `div.table-wrap` | Entregas registradas por semana | contenido |

</details>

### `/entregas/1/preview`

<details><summary>1440 px — 15 pulsaciones, el foco sale del documento en la 16</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `button.btn.btn--secondary` | Copiar | contenido |
| 12 | `div.table-wrap` | Errores de validación detectados | contenido |
| 13 | `button.btn.btn--primary` | Confirmar y cruzar | contenido |
| 14 | `input#campo-motivo` | — | contenido |
| 15 | `button.btn.btn--danger` | Rechazar | contenido |

</details>

<details><summary>390 px — 7 pulsaciones, el foco sale del documento en la 8</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `button.btn.btn--secondary` | Copiar | contenido |
| 4 | `div.table-wrap` | Errores de validación detectados | contenido |
| 5 | `button.btn.btn--primary` | Confirmar y cruzar | contenido |
| 6 | `input#campo-motivo` | — | contenido |
| 7 | `button.btn.btn--danger` | Rechazar | contenido |

</details>

### `/cruces`

<details><summary>1440 px — 74 pulsaciones, el foco sale del documento en la 75</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `a[href="/cruces"].inline-flex.items-center:nth-of-type(1)` | Todos 15400 | contenido |
| 12 | `a[href="/cruces?resultado=unanimidad"].inline-flex.items-center:nth-of-type(2)` | Unanimidad 12230 | contenido |
| 13 | `a[href="/cruces?resultado=mayoria"].inline-flex.items-center:nth-of-type(3)` | Mayoría 2975 | contenido |
| 14 | `a[href="/cruces?resultado=discrepancia"].inline-flex.items-center:nth-of-type(4)` | Discrepancia 195 | contenido |
| 15 | `a[href="/cruces?resultado=incompleto"].inline-flex.items-center:nth-of-type(5)` | Incompleto 0 | contenido |
| 16 | `input#campo-punto` | — | contenido |
| 17 | `select#filtro-anio` | Todos 1990 1991 1992 1993 1994 1995 1996 1997 1998 1999 2000 | contenido |
| 18 | `button.btn.btn--primary` | Filtrar | contenido |
| 19 | `div.table-wrap` | Cruces de la terna por punto y año | contenido |
| 20 | `a[href="/cruces/1/1990?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 21 | `a[href="/cruces/1/1991?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 22 | `a[href="/cruces/1/1992?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 23 | `a[href="/cruces/1/1993?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 24 | `a[href="/cruces/1/1994?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 25 | `a[href="/cruces/1/1995?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 26 | `a[href="/cruces/1/1996?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 27 | `a[href="/cruces/1/1997?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 28 | `a[href="/cruces/1/1998?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 29 | `a[href="/cruces/1/1999?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 30 | `a[href="/cruces/1/2000?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 31 | `a[href="/cruces/1/2001?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 32 | `a[href="/cruces/1/2002?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 33 | `a[href="/cruces/1/2003?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 34 | `a[href="/cruces/1/2004?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 35 | `a[href="/cruces/1/2005?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 36 | `a[href="/cruces/1/2006?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 37 | `a[href="/cruces/1/2007?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 38 | `a[href="/cruces/1/2008?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 39 | `a[href="/cruces/1/2009?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 40 | `a[href="/cruces/1/2010?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 41 | `a[href="/cruces/1/2011?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 42 | `a[href="/cruces/1/2012?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 43 | `a[href="/cruces/1/2013?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 44 | `a[href="/cruces/1/2014?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 45 | `a[href="/cruces/1/2015?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 46 | `a[href="/cruces/1/2016?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 47 | `a[href="/cruces/1/2017?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 48 | `a[href="/cruces/1/2018?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 49 | `a[href="/cruces/1/2019?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 50 | `a[href="/cruces/1/2020?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 51 | `a[href="/cruces/1/2021?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 52 | `a[href="/cruces/1/2022?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 53 | `a[href="/cruces/1/2023?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 54 | `a[href="/cruces/1/2024?return_to=/cruces"].font-medium.text-brand-700` | 1 | contenido |
| 55 | `a[href="/cruces/2/1990?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 56 | `a[href="/cruces/2/1991?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 57 | `a[href="/cruces/2/1992?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 58 | `a[href="/cruces/2/1993?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 59 | `a[href="/cruces/2/1994?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 60 | `a[href="/cruces/2/1995?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 61 | `a[href="/cruces/2/1996?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 62 | `a[href="/cruces/2/1997?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 63 | `a[href="/cruces/2/1998?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 64 | `a[href="/cruces/2/1999?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 65 | `a[href="/cruces/2/2000?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 66 | `a[href="/cruces/2/2001?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 67 | `a[href="/cruces/2/2002?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 68 | `a[href="/cruces/2/2003?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 69 | `a[href="/cruces/2/2004?return_to=/cruces"].font-medium.text-brand-700` | 2 | contenido |
| 70 | `select#filas-por-pagina` | 25 50 100 | contenido |
| 71 | `button.btn.btn--secondary` | Aplicar | contenido |
| 72 | `a[href="http://localhost:8000/cruces?page=2"].pagination__page:nth-of-type(1)` | 2 | contenido |
| 73 | `a[href="http://localhost:8000/cruces?page=3"].pagination__page:nth-of-type(2)` | 3 | contenido |
| 74 | `a[href="http://localhost:8000/cruces?page=2"].pagination__page:nth-of-type(3)` | ›Página siguiente | contenido |

</details>

<details><summary>390 px — 115 pulsaciones, el foco sale del documento en la 116</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `a[href="/cruces"].inline-flex.items-center:nth-of-type(1)` | Todos 15400 | contenido |
| 4 | `a[href="/cruces?resultado=unanimidad"].inline-flex.items-center:nth-of-type(2)` | Unanimidad 12230 | contenido |
| 5 | `a[href="/cruces?resultado=mayoria"].inline-flex.items-center:nth-of-type(3)` | Mayoría 2975 | contenido |
| 6 | `a[href="/cruces?resultado=discrepancia"].inline-flex.items-center:nth-of-type(4)` | Discrepancia 195 | contenido |
| 7 | `a[href="/cruces?resultado=incompleto"].inline-flex.items-center:nth-of-type(5)` | Incompleto 0 | contenido |
| 8 | `input#campo-punto` | — | contenido |
| 9 | `select#filtro-anio` | Todos 1990 1991 1992 1993 1994 1995 1996 1997 1998 1999 2000 | contenido |
| 10 | `button.btn.btn--primary` | Filtrar | contenido |
| 11 | `a[href="/cruces/1/1990?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 12 | `a[href="/cruces/1/1990?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 13 | `a[href="/cruces/1/1991?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 14 | `a[href="/cruces/1/1991?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 15 | `a[href="/cruces/1/1992?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 16 | `a[href="/cruces/1/1992?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 17 | `a[href="/cruces/1/1993?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 18 | `a[href="/cruces/1/1993?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 19 | `a[href="/cruces/1/1994?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 20 | `a[href="/cruces/1/1994?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 21 | `a[href="/cruces/1/1995?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 22 | `a[href="/cruces/1/1995?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 23 | `a[href="/cruces/1/1996?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 24 | `a[href="/cruces/1/1996?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 25 | `a[href="/cruces/1/1997?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 26 | `a[href="/cruces/1/1997?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 27 | `a[href="/cruces/1/1998?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 28 | `a[href="/cruces/1/1998?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 29 | `a[href="/cruces/1/1999?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 30 | `a[href="/cruces/1/1999?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 31 | `a[href="/cruces/1/2000?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 32 | `a[href="/cruces/1/2000?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 33 | `a[href="/cruces/1/2001?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 34 | `a[href="/cruces/1/2001?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 35 | `a[href="/cruces/1/2002?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 36 | `a[href="/cruces/1/2002?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 37 | `a[href="/cruces/1/2003?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 38 | `a[href="/cruces/1/2003?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 39 | `a[href="/cruces/1/2004?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 40 | `a[href="/cruces/1/2004?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 41 | `a[href="/cruces/1/2005?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 42 | `a[href="/cruces/1/2005?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 43 | `a[href="/cruces/1/2006?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 44 | `a[href="/cruces/1/2006?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 45 | `a[href="/cruces/1/2007?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 46 | `a[href="/cruces/1/2007?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 47 | `a[href="/cruces/1/2008?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 48 | `a[href="/cruces/1/2008?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 49 | `a[href="/cruces/1/2009?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 50 | `a[href="/cruces/1/2009?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 51 | `a[href="/cruces/1/2010?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 52 | `a[href="/cruces/1/2010?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 53 | `a[href="/cruces/1/2011?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 54 | `a[href="/cruces/1/2011?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 55 | `a[href="/cruces/1/2012?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 56 | `a[href="/cruces/1/2012?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 57 | `a[href="/cruces/1/2013?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 58 | `a[href="/cruces/1/2013?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 59 | `a[href="/cruces/1/2014?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 60 | `a[href="/cruces/1/2014?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 61 | `a[href="/cruces/1/2015?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 62 | `a[href="/cruces/1/2015?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 63 | `a[href="/cruces/1/2016?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 64 | `a[href="/cruces/1/2016?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 65 | `a[href="/cruces/1/2017?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 66 | `a[href="/cruces/1/2017?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 67 | `a[href="/cruces/1/2018?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 68 | `a[href="/cruces/1/2018?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 69 | `a[href="/cruces/1/2019?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 70 | `a[href="/cruces/1/2019?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 71 | `a[href="/cruces/1/2020?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 72 | `a[href="/cruces/1/2020?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 73 | `a[href="/cruces/1/2021?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 74 | `a[href="/cruces/1/2021?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 75 | `a[href="/cruces/1/2022?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 76 | `a[href="/cruces/1/2022?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 77 | `a[href="/cruces/1/2023?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 78 | `a[href="/cruces/1/2023?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 79 | `a[href="/cruces/1/2024?return_to=/cruces"].tabular.text-brand-700` | 1 | contenido |
| 80 | `a[href="/cruces/1/2024?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 81 | `a[href="/cruces/2/1990?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 82 | `a[href="/cruces/2/1990?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 83 | `a[href="/cruces/2/1991?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 84 | `a[href="/cruces/2/1991?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 85 | `a[href="/cruces/2/1992?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 86 | `a[href="/cruces/2/1992?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 87 | `a[href="/cruces/2/1993?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 88 | `a[href="/cruces/2/1993?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 89 | `a[href="/cruces/2/1994?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 90 | `a[href="/cruces/2/1994?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 91 | `a[href="/cruces/2/1995?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 92 | `a[href="/cruces/2/1995?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 93 | `a[href="/cruces/2/1996?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 94 | `a[href="/cruces/2/1996?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 95 | `a[href="/cruces/2/1997?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 96 | `a[href="/cruces/2/1997?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 97 | `a[href="/cruces/2/1998?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 98 | `a[href="/cruces/2/1998?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 99 | `a[href="/cruces/2/1999?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 100 | `a[href="/cruces/2/1999?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 101 | `a[href="/cruces/2/2000?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 102 | `a[href="/cruces/2/2000?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 103 | `a[href="/cruces/2/2001?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 104 | `a[href="/cruces/2/2001?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 105 | `a[href="/cruces/2/2002?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 106 | `a[href="/cruces/2/2002?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 107 | `a[href="/cruces/2/2003?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 108 | `a[href="/cruces/2/2003?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 109 | `a[href="/cruces/2/2004?return_to=/cruces"].tabular.text-brand-700` | 2 | contenido |
| 110 | `a[href="/cruces/2/2004?return_to=/cruces"].btn.btn--secondary` | Ver cruce | contenido |
| 111 | `select#filas-por-pagina` | 25 50 100 | contenido |
| 112 | `button.btn.btn--secondary` | Aplicar | contenido |
| 113 | `a[href="http://localhost:8000/cruces?page=2"].pagination__page:nth-of-type(1)` | 2 | contenido |
| 114 | `a[href="http://localhost:8000/cruces?page=3"].pagination__page:nth-of-type(2)` | 3 | contenido |
| 115 | `a[href="http://localhost:8000/cruces?page=2"].pagination__page:nth-of-type(3)` | ›Página siguiente | contenido |

</details>

### `/cruces/81/2008`

<details><summary>1440 px — 15 pulsaciones, el foco sale del documento en la 16</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `a[href="/cruces"]` | Cruces | contenido |
| 12 | `a[href="/cruces"].btn.btn--secondary` | Volver a cruces | contenido |
| 13 | `a[href="/qaqc/1"].font-medium.text-brand-700` | #1 | contenido |
| 14 | `a[href="/qaqc/1"].btn.btn--primary` | Abrir ficha | contenido |
| 15 | `a[href="/cruces"].btn.btn--secondary` | Volver a cruces | contenido |

</details>

<details><summary>390 px — 7 pulsaciones, el foco sale del documento en la 8</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `a[href="/cruces"]` | Cruces | contenido |
| 4 | `a[href="/cruces"].btn.btn--secondary` | Volver a cruces | contenido |
| 5 | `a[href="/qaqc/1"].font-medium.text-brand-700` | #1 | contenido |
| 6 | `a[href="/qaqc/1"].btn.btn--primary` | Abrir ficha | contenido |
| 7 | `a[href="/cruces"].btn.btn--secondary` | Volver a cruces | contenido |

</details>

### `/qaqc`

<details><summary>1440 px — 125 pulsaciones, el foco sale del documento en la 126</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `a[href="/qaqc"].inline-flex.items-center:nth-of-type(1)` | Todas 221 | contenido |
| 12 | `a[href="/qaqc?estado=Detectado"].inline-flex.items-center:nth-of-type(2)` | Detectado 221 | contenido |
| 13 | `a[href="/qaqc?estado=En%20revisi%C3%B3n"].inline-flex.items-center:nth-of-type(3)` | En revisión 0 | contenido |
| 14 | `a[href="/qaqc?estado=Pendiente%20de%20aprobaci%C3%B3n"].inline-flex.items-center:nth-of-type(4)` | Pendiente de aprobación 0 | contenido |
| 15 | `a[href="/qaqc?estado=Resuelto%20por%20consenso"].inline-flex.items-center:nth-of-type(5)` | Resuelto por consenso 0 | contenido |
| 16 | `a[href="/qaqc?estado=Sin%20consenso"].inline-flex.items-center:nth-of-type(6)` | Sin consenso 0 | contenido |
| 17 | `a[href="/qaqc?estado=Reemplazo%20autorizado"].inline-flex.items-center:nth-of-type(7)` | Reemplazo autorizado 0 | contenido |
| 18 | `a[href="/qaqc?estado=Reserva%20en%20interpretaci%C3%B3n"].inline-flex.items-center:nth-of-type(8)` | Reserva en interpretación 0 | contenido |
| 19 | `a[href="/qaqc?estado=Reemplazo%20completado"].inline-flex.items-center:nth-of-type(9)` | Reemplazo completado 0 | contenido |
| 20 | `div.table-wrap:nth-of-type(1)` | Fichas QA/QC por punto y año | contenido |
| 21 | `a[href="/qaqc/4"].font-medium.text-brand-700` | #4 | contenido |
| 22 | `a[href="/qaqc/4"].btn.btn--secondary` | Abrir | contenido |
| 23 | `a[href="/qaqc/5"].font-medium.text-brand-700` | #5 | contenido |
| 24 | `a[href="/qaqc/5"].btn.btn--secondary` | Abrir | contenido |
| 25 | `a[href="/qaqc/6"].font-medium.text-brand-700` | #6 | contenido |
| 26 | `a[href="/qaqc/6"].btn.btn--secondary` | Abrir | contenido |
| 27 | `a[href="/qaqc/7"].font-medium.text-brand-700` | #7 | contenido |
| 28 | `a[href="/qaqc/7"].btn.btn--secondary` | Abrir | contenido |
| 29 | `a[href="/qaqc/8"].font-medium.text-brand-700` | #8 | contenido |
| 30 | `a[href="/qaqc/8"].btn.btn--secondary` | Abrir | contenido |
| 31 | `a[href="/qaqc/9"].font-medium.text-brand-700` | #9 | contenido |
| 32 | `a[href="/qaqc/9"].btn.btn--secondary` | Abrir | contenido |
| 33 | `a[href="/qaqc/10"].font-medium.text-brand-700` | #10 | contenido |
| 34 | `a[href="/qaqc/10"].btn.btn--secondary` | Abrir | contenido |
| 35 | `a[href="/qaqc/11"].font-medium.text-brand-700` | #11 | contenido |
| 36 | `a[href="/qaqc/11"].btn.btn--secondary` | Abrir | contenido |
| 37 | `a[href="/qaqc/12"].font-medium.text-brand-700` | #12 | contenido |
| 38 | `a[href="/qaqc/12"].btn.btn--secondary` | Abrir | contenido |
| 39 | `a[href="/qaqc/13"].font-medium.text-brand-700` | #13 | contenido |
| 40 | `a[href="/qaqc/13"].btn.btn--secondary` | Abrir | contenido |
| 41 | `a[href="/qaqc/14"].font-medium.text-brand-700` | #14 | contenido |
| 42 | `a[href="/qaqc/14"].btn.btn--secondary` | Abrir | contenido |
| 43 | `a[href="/qaqc/15"].font-medium.text-brand-700` | #15 | contenido |
| 44 | `a[href="/qaqc/15"].btn.btn--secondary` | Abrir | contenido |
| 45 | `a[href="/qaqc/16"].font-medium.text-brand-700` | #16 | contenido |
| 46 | `a[href="/qaqc/16"].btn.btn--secondary` | Abrir | contenido |
| 47 | `a[href="/qaqc/17"].font-medium.text-brand-700` | #17 | contenido |
| 48 | `a[href="/qaqc/17"].btn.btn--secondary` | Abrir | contenido |
| 49 | `a[href="/qaqc/18"].font-medium.text-brand-700` | #18 | contenido |
| 50 | `a[href="/qaqc/18"].btn.btn--secondary` | Abrir | contenido |
| 51 | `a[href="/qaqc/19"].font-medium.text-brand-700` | #19 | contenido |
| 52 | `a[href="/qaqc/19"].btn.btn--secondary` | Abrir | contenido |
| 53 | `a[href="/qaqc/20"].font-medium.text-brand-700` | #20 | contenido |
| 54 | `a[href="/qaqc/20"].btn.btn--secondary` | Abrir | contenido |
| 55 | `a[href="/qaqc/21"].font-medium.text-brand-700` | #21 | contenido |
| 56 | `a[href="/qaqc/21"].btn.btn--secondary` | Abrir | contenido |
| 57 | `a[href="/qaqc/22"].font-medium.text-brand-700` | #22 | contenido |
| 58 | `a[href="/qaqc/22"].btn.btn--secondary` | Abrir | contenido |
| 59 | `a[href="/qaqc/23"].font-medium.text-brand-700` | #23 | contenido |
| 60 | `a[href="/qaqc/23"].btn.btn--secondary` | Abrir | contenido |
| 61 | `a[href="/qaqc/24"].font-medium.text-brand-700` | #24 | contenido |
| 62 | `a[href="/qaqc/24"].btn.btn--secondary` | Abrir | contenido |
| 63 | `a[href="/qaqc/25"].font-medium.text-brand-700` | #25 | contenido |
| 64 | `a[href="/qaqc/25"].btn.btn--secondary` | Abrir | contenido |
| 65 | `a[href="/qaqc/26"].font-medium.text-brand-700` | #26 | contenido |
| 66 | `a[href="/qaqc/26"].btn.btn--secondary` | Abrir | contenido |
| 67 | `a[href="/qaqc/27"].font-medium.text-brand-700` | #27 | contenido |
| 68 | `a[href="/qaqc/27"].btn.btn--secondary` | Abrir | contenido |
| 69 | `a[href="/qaqc/28"].font-medium.text-brand-700` | #28 | contenido |
| 70 | `a[href="/qaqc/28"].btn.btn--secondary` | Abrir | contenido |
| 71 | `a[href="/qaqc/29"].font-medium.text-brand-700` | #29 | contenido |
| 72 | `a[href="/qaqc/29"].btn.btn--secondary` | Abrir | contenido |
| 73 | `a[href="/qaqc/30"].font-medium.text-brand-700` | #30 | contenido |
| 74 | `a[href="/qaqc/30"].btn.btn--secondary` | Abrir | contenido |
| 75 | `a[href="/qaqc/31"].font-medium.text-brand-700` | #31 | contenido |
| 76 | `a[href="/qaqc/31"].btn.btn--secondary` | Abrir | contenido |
| 77 | `a[href="/qaqc/32"].font-medium.text-brand-700` | #32 | contenido |
| 78 | `a[href="/qaqc/32"].btn.btn--secondary` | Abrir | contenido |
| 79 | `a[href="/qaqc/33"].font-medium.text-brand-700` | #33 | contenido |
| 80 | `a[href="/qaqc/33"].btn.btn--secondary` | Abrir | contenido |
| 81 | `a[href="/qaqc/34"].font-medium.text-brand-700` | #34 | contenido |
| 82 | `a[href="/qaqc/34"].btn.btn--secondary` | Abrir | contenido |
| 83 | `a[href="/qaqc/35"].font-medium.text-brand-700` | #35 | contenido |
| 84 | `a[href="/qaqc/35"].btn.btn--secondary` | Abrir | contenido |
| 85 | `a[href="/qaqc/36"].font-medium.text-brand-700` | #36 | contenido |
| 86 | `a[href="/qaqc/36"].btn.btn--secondary` | Abrir | contenido |
| 87 | `a[href="/qaqc/37"].font-medium.text-brand-700` | #37 | contenido |
| 88 | `a[href="/qaqc/37"].btn.btn--secondary` | Abrir | contenido |
| 89 | `a[href="/qaqc/38"].font-medium.text-brand-700` | #38 | contenido |
| 90 | `a[href="/qaqc/38"].btn.btn--secondary` | Abrir | contenido |
| 91 | `a[href="/qaqc/39"].font-medium.text-brand-700` | #39 | contenido |
| 92 | `a[href="/qaqc/39"].btn.btn--secondary` | Abrir | contenido |
| 93 | `a[href="/qaqc/40"].font-medium.text-brand-700` | #40 | contenido |
| 94 | `a[href="/qaqc/40"].btn.btn--secondary` | Abrir | contenido |
| 95 | `a[href="/qaqc/41"].font-medium.text-brand-700` | #41 | contenido |
| 96 | `a[href="/qaqc/41"].btn.btn--secondary` | Abrir | contenido |
| 97 | `a[href="/qaqc/42"].font-medium.text-brand-700` | #42 | contenido |
| 98 | `a[href="/qaqc/42"].btn.btn--secondary` | Abrir | contenido |
| 99 | `a[href="/qaqc/43"].font-medium.text-brand-700` | #43 | contenido |
| 100 | `a[href="/qaqc/43"].btn.btn--secondary` | Abrir | contenido |
| 101 | `a[href="/qaqc/44"].font-medium.text-brand-700` | #44 | contenido |
| 102 | `a[href="/qaqc/44"].btn.btn--secondary` | Abrir | contenido |
| 103 | `a[href="/qaqc/45"].font-medium.text-brand-700` | #45 | contenido |
| 104 | `a[href="/qaqc/45"].btn.btn--secondary` | Abrir | contenido |
| 105 | `a[href="/qaqc/46"].font-medium.text-brand-700` | #46 | contenido |
| 106 | `a[href="/qaqc/46"].btn.btn--secondary` | Abrir | contenido |
| 107 | `a[href="/qaqc/2"].font-medium.text-brand-700` | #2 | contenido |
| 108 | `a[href="/qaqc/2"].btn.btn--secondary` | Abrir | contenido |
| 109 | `a[href="/qaqc/47"].font-medium.text-brand-700` | #47 | contenido |
| 110 | `a[href="/qaqc/47"].btn.btn--secondary` | Abrir | contenido |
| 111 | `a[href="/qaqc/48"].font-medium.text-brand-700` | #48 | contenido |
| 112 | `a[href="/qaqc/48"].btn.btn--secondary` | Abrir | contenido |
| 113 | `a[href="/qaqc/49"].font-medium.text-brand-700` | #49 | contenido |
| 114 | `a[href="/qaqc/49"].btn.btn--secondary` | Abrir | contenido |
| 115 | `a[href="/qaqc/50"].font-medium.text-brand-700` | #50 | contenido |
| 116 | `a[href="/qaqc/50"].btn.btn--secondary` | Abrir | contenido |
| 117 | `a[href="/qaqc/51"].font-medium.text-brand-700` | #51 | contenido |
| 118 | `a[href="/qaqc/51"].btn.btn--secondary` | Abrir | contenido |
| 119 | `a[href="/qaqc/3"].font-medium.text-brand-700` | #3 | contenido |
| 120 | `a[href="/qaqc/3"].btn.btn--secondary` | Abrir | contenido |
| 121 | `select#fichas-por-pagina` | 25 50 100 | contenido |
| 122 | `button.btn.btn--secondary` | Aplicar | contenido |
| 123 | `a[href="http://localhost:8000/qaqc?page=2"].pagination__page:nth-of-type(1)` | 2 | contenido |
| 124 | `a[href="http://localhost:8000/qaqc?page=3"].pagination__page:nth-of-type(2)` | 3 | contenido |
| 125 | `a[href="http://localhost:8000/qaqc?page=2"].pagination__page:nth-of-type(3)` | ›Página siguiente | contenido |

</details>

<details><summary>390 px — 117 pulsaciones, el foco sale del documento en la 118</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `a[href="/qaqc"].inline-flex.items-center:nth-of-type(1)` | Todas 221 | contenido |
| 4 | `a[href="/qaqc?estado=Detectado"].inline-flex.items-center:nth-of-type(2)` | Detectado 221 | contenido |
| 5 | `a[href="/qaqc?estado=En%20revisi%C3%B3n"].inline-flex.items-center:nth-of-type(3)` | En revisión 0 | contenido |
| 6 | `a[href="/qaqc?estado=Pendiente%20de%20aprobaci%C3%B3n"].inline-flex.items-center:nth-of-type(4)` | Pendiente de aprobación 0 | contenido |
| 7 | `a[href="/qaqc?estado=Resuelto%20por%20consenso"].inline-flex.items-center:nth-of-type(5)` | Resuelto por consenso 0 | contenido |
| 8 | `a[href="/qaqc?estado=Sin%20consenso"].inline-flex.items-center:nth-of-type(6)` | Sin consenso 0 | contenido |
| 9 | `a[href="/qaqc?estado=Reemplazo%20autorizado"].inline-flex.items-center:nth-of-type(7)` | Reemplazo autorizado 0 | contenido |
| 10 | `a[href="/qaqc?estado=Reserva%20en%20interpretaci%C3%B3n"].inline-flex.items-center:nth-of-type(8)` | Reserva en interpretación 0 | contenido |
| 11 | `a[href="/qaqc?estado=Reemplazo%20completado"].inline-flex.items-center:nth-of-type(9)` | Reemplazo completado 0 | contenido |
| 12 | `div.table-wrap:nth-of-type(1)` | Fichas QA/QC por punto y año | contenido |
| 13 | `a[href="/qaqc/4"].font-medium.text-brand-700` | #4 | contenido |
| 14 | `a[href="/qaqc/4"].btn.btn--secondary` | Abrir | contenido |
| 15 | `a[href="/qaqc/5"].font-medium.text-brand-700` | #5 | contenido |
| 16 | `a[href="/qaqc/5"].btn.btn--secondary` | Abrir | contenido |
| 17 | `a[href="/qaqc/6"].font-medium.text-brand-700` | #6 | contenido |
| 18 | `a[href="/qaqc/6"].btn.btn--secondary` | Abrir | contenido |
| 19 | `a[href="/qaqc/7"].font-medium.text-brand-700` | #7 | contenido |
| 20 | `a[href="/qaqc/7"].btn.btn--secondary` | Abrir | contenido |
| 21 | `a[href="/qaqc/8"].font-medium.text-brand-700` | #8 | contenido |
| 22 | `a[href="/qaqc/8"].btn.btn--secondary` | Abrir | contenido |
| 23 | `a[href="/qaqc/9"].font-medium.text-brand-700` | #9 | contenido |
| 24 | `a[href="/qaqc/9"].btn.btn--secondary` | Abrir | contenido |
| 25 | `a[href="/qaqc/10"].font-medium.text-brand-700` | #10 | contenido |
| 26 | `a[href="/qaqc/10"].btn.btn--secondary` | Abrir | contenido |
| 27 | `a[href="/qaqc/11"].font-medium.text-brand-700` | #11 | contenido |
| 28 | `a[href="/qaqc/11"].btn.btn--secondary` | Abrir | contenido |
| 29 | `a[href="/qaqc/12"].font-medium.text-brand-700` | #12 | contenido |
| 30 | `a[href="/qaqc/12"].btn.btn--secondary` | Abrir | contenido |
| 31 | `a[href="/qaqc/13"].font-medium.text-brand-700` | #13 | contenido |
| 32 | `a[href="/qaqc/13"].btn.btn--secondary` | Abrir | contenido |
| 33 | `a[href="/qaqc/14"].font-medium.text-brand-700` | #14 | contenido |
| 34 | `a[href="/qaqc/14"].btn.btn--secondary` | Abrir | contenido |
| 35 | `a[href="/qaqc/15"].font-medium.text-brand-700` | #15 | contenido |
| 36 | `a[href="/qaqc/15"].btn.btn--secondary` | Abrir | contenido |
| 37 | `a[href="/qaqc/16"].font-medium.text-brand-700` | #16 | contenido |
| 38 | `a[href="/qaqc/16"].btn.btn--secondary` | Abrir | contenido |
| 39 | `a[href="/qaqc/17"].font-medium.text-brand-700` | #17 | contenido |
| 40 | `a[href="/qaqc/17"].btn.btn--secondary` | Abrir | contenido |
| 41 | `a[href="/qaqc/18"].font-medium.text-brand-700` | #18 | contenido |
| 42 | `a[href="/qaqc/18"].btn.btn--secondary` | Abrir | contenido |
| 43 | `a[href="/qaqc/19"].font-medium.text-brand-700` | #19 | contenido |
| 44 | `a[href="/qaqc/19"].btn.btn--secondary` | Abrir | contenido |
| 45 | `a[href="/qaqc/20"].font-medium.text-brand-700` | #20 | contenido |
| 46 | `a[href="/qaqc/20"].btn.btn--secondary` | Abrir | contenido |
| 47 | `a[href="/qaqc/21"].font-medium.text-brand-700` | #21 | contenido |
| 48 | `a[href="/qaqc/21"].btn.btn--secondary` | Abrir | contenido |
| 49 | `a[href="/qaqc/22"].font-medium.text-brand-700` | #22 | contenido |
| 50 | `a[href="/qaqc/22"].btn.btn--secondary` | Abrir | contenido |
| 51 | `a[href="/qaqc/23"].font-medium.text-brand-700` | #23 | contenido |
| 52 | `a[href="/qaqc/23"].btn.btn--secondary` | Abrir | contenido |
| 53 | `a[href="/qaqc/24"].font-medium.text-brand-700` | #24 | contenido |
| 54 | `a[href="/qaqc/24"].btn.btn--secondary` | Abrir | contenido |
| 55 | `a[href="/qaqc/25"].font-medium.text-brand-700` | #25 | contenido |
| 56 | `a[href="/qaqc/25"].btn.btn--secondary` | Abrir | contenido |
| 57 | `a[href="/qaqc/26"].font-medium.text-brand-700` | #26 | contenido |
| 58 | `a[href="/qaqc/26"].btn.btn--secondary` | Abrir | contenido |
| 59 | `a[href="/qaqc/27"].font-medium.text-brand-700` | #27 | contenido |
| 60 | `a[href="/qaqc/27"].btn.btn--secondary` | Abrir | contenido |
| 61 | `a[href="/qaqc/28"].font-medium.text-brand-700` | #28 | contenido |
| 62 | `a[href="/qaqc/28"].btn.btn--secondary` | Abrir | contenido |
| 63 | `a[href="/qaqc/29"].font-medium.text-brand-700` | #29 | contenido |
| 64 | `a[href="/qaqc/29"].btn.btn--secondary` | Abrir | contenido |
| 65 | `a[href="/qaqc/30"].font-medium.text-brand-700` | #30 | contenido |
| 66 | `a[href="/qaqc/30"].btn.btn--secondary` | Abrir | contenido |
| 67 | `a[href="/qaqc/31"].font-medium.text-brand-700` | #31 | contenido |
| 68 | `a[href="/qaqc/31"].btn.btn--secondary` | Abrir | contenido |
| 69 | `a[href="/qaqc/32"].font-medium.text-brand-700` | #32 | contenido |
| 70 | `a[href="/qaqc/32"].btn.btn--secondary` | Abrir | contenido |
| 71 | `a[href="/qaqc/33"].font-medium.text-brand-700` | #33 | contenido |
| 72 | `a[href="/qaqc/33"].btn.btn--secondary` | Abrir | contenido |
| 73 | `a[href="/qaqc/34"].font-medium.text-brand-700` | #34 | contenido |
| 74 | `a[href="/qaqc/34"].btn.btn--secondary` | Abrir | contenido |
| 75 | `a[href="/qaqc/35"].font-medium.text-brand-700` | #35 | contenido |
| 76 | `a[href="/qaqc/35"].btn.btn--secondary` | Abrir | contenido |
| 77 | `a[href="/qaqc/36"].font-medium.text-brand-700` | #36 | contenido |
| 78 | `a[href="/qaqc/36"].btn.btn--secondary` | Abrir | contenido |
| 79 | `a[href="/qaqc/37"].font-medium.text-brand-700` | #37 | contenido |
| 80 | `a[href="/qaqc/37"].btn.btn--secondary` | Abrir | contenido |
| 81 | `a[href="/qaqc/38"].font-medium.text-brand-700` | #38 | contenido |
| 82 | `a[href="/qaqc/38"].btn.btn--secondary` | Abrir | contenido |
| 83 | `a[href="/qaqc/39"].font-medium.text-brand-700` | #39 | contenido |
| 84 | `a[href="/qaqc/39"].btn.btn--secondary` | Abrir | contenido |
| 85 | `a[href="/qaqc/40"].font-medium.text-brand-700` | #40 | contenido |
| 86 | `a[href="/qaqc/40"].btn.btn--secondary` | Abrir | contenido |
| 87 | `a[href="/qaqc/41"].font-medium.text-brand-700` | #41 | contenido |
| 88 | `a[href="/qaqc/41"].btn.btn--secondary` | Abrir | contenido |
| 89 | `a[href="/qaqc/42"].font-medium.text-brand-700` | #42 | contenido |
| 90 | `a[href="/qaqc/42"].btn.btn--secondary` | Abrir | contenido |
| 91 | `a[href="/qaqc/43"].font-medium.text-brand-700` | #43 | contenido |
| 92 | `a[href="/qaqc/43"].btn.btn--secondary` | Abrir | contenido |
| 93 | `a[href="/qaqc/44"].font-medium.text-brand-700` | #44 | contenido |
| 94 | `a[href="/qaqc/44"].btn.btn--secondary` | Abrir | contenido |
| 95 | `a[href="/qaqc/45"].font-medium.text-brand-700` | #45 | contenido |
| 96 | `a[href="/qaqc/45"].btn.btn--secondary` | Abrir | contenido |
| 97 | `a[href="/qaqc/46"].font-medium.text-brand-700` | #46 | contenido |
| 98 | `a[href="/qaqc/46"].btn.btn--secondary` | Abrir | contenido |
| 99 | `a[href="/qaqc/2"].font-medium.text-brand-700` | #2 | contenido |
| 100 | `a[href="/qaqc/2"].btn.btn--secondary` | Abrir | contenido |
| 101 | `a[href="/qaqc/47"].font-medium.text-brand-700` | #47 | contenido |
| 102 | `a[href="/qaqc/47"].btn.btn--secondary` | Abrir | contenido |
| 103 | `a[href="/qaqc/48"].font-medium.text-brand-700` | #48 | contenido |
| 104 | `a[href="/qaqc/48"].btn.btn--secondary` | Abrir | contenido |
| 105 | `a[href="/qaqc/49"].font-medium.text-brand-700` | #49 | contenido |
| 106 | `a[href="/qaqc/49"].btn.btn--secondary` | Abrir | contenido |
| 107 | `a[href="/qaqc/50"].font-medium.text-brand-700` | #50 | contenido |
| 108 | `a[href="/qaqc/50"].btn.btn--secondary` | Abrir | contenido |
| 109 | `a[href="/qaqc/51"].font-medium.text-brand-700` | #51 | contenido |
| 110 | `a[href="/qaqc/51"].btn.btn--secondary` | Abrir | contenido |
| 111 | `a[href="/qaqc/3"].font-medium.text-brand-700` | #3 | contenido |
| 112 | `a[href="/qaqc/3"].btn.btn--secondary` | Abrir | contenido |
| 113 | `select#fichas-por-pagina` | 25 50 100 | contenido |
| 114 | `button.btn.btn--secondary` | Aplicar | contenido |
| 115 | `a[href="http://localhost:8000/qaqc?page=2"].pagination__page:nth-of-type(1)` | 2 | contenido |
| 116 | `a[href="http://localhost:8000/qaqc?page=3"].pagination__page:nth-of-type(2)` | 3 | contenido |
| 117 | `a[href="http://localhost:8000/qaqc?page=2"].pagination__page:nth-of-type(3)` | ›Página siguiente | contenido |

</details>

### `/qaqc/1`

<details><summary>1440 px — 34 pulsaciones, el foco sale del documento en la 35</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `a[href="/qaqc"]` | Fichas QA/QC | contenido |
| 12 | `summary.cursor-pointer.px-5` | Resumen y pendientes | contenido |
| 13 | `a[href="/cruces/81/2008"].text-brand-700.underline` | Ver el cruce de origen | contenido |
| 14 | `summary.cursor-pointer.text-xs` | Leyenda de clases de cobertura | contenido |
| 15 | `summary.cursor-pointer.text-xs` | Referencias del protocolo | contenido |
| 16 | `textarea#campo-debate` | — | contenido |
| 17 | `input.mt-0\.5.h-4` | — | contenido |
| 18 | `input.h-4.w-4` | — | contenido |
| 19 | `input.h-4.w-4` | — | contenido |
| 20 | `input.h-4.w-4` | — | contenido |
| 21 | `input.h-4.w-4` | — | contenido |
| 22 | `input.h-4.w-4` | — | contenido |
| 23 | `input#evidencia-1` | — | contenido |
| 24 | `button.btn.btn--secondary` | Quitar | contenido |
| 25 | `button.btn.btn--secondary` | Agregar evidencia | contenido |
| 26 | `input#campo-fecha_sesion` | — | contenido |
| 27 | `input#campo-fecha_sesion` | — | contenido |
| 28 | `input#campo-fecha_sesion` | — | contenido |
| 29 | `input#campo-fecha_sesion` | — | contenido |
| 30 | `input#campo-firma_a` | — | contenido |
| 31 | `input#campo-firma_b` | — | contenido |
| 32 | `input#campo-firma_c` | — | contenido |
| 33 | `button.btn.btn--primary` | Guardar dictamen | contenido |
| 34 | `button.btn.btn--secondary` | Iniciar revisión | contenido |

</details>

<details><summary>390 px — 26 pulsaciones, el foco sale del documento en la 27</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `a[href="/qaqc"]` | Fichas QA/QC | contenido |
| 4 | `summary.cursor-pointer.px-5` | Resumen y pendientes | contenido |
| 5 | `a[href="/cruces/81/2008"].text-brand-700.underline` | Ver el cruce de origen | contenido |
| 6 | `summary.cursor-pointer.text-xs` | Leyenda de clases de cobertura | contenido |
| 7 | `summary.cursor-pointer.text-xs` | Referencias del protocolo | contenido |
| 8 | `textarea#campo-debate` | — | contenido |
| 9 | `input.mt-0\.5.h-4` | — | contenido |
| 10 | `input.h-4.w-4` | — | contenido |
| 11 | `input.h-4.w-4` | — | contenido |
| 12 | `input.h-4.w-4` | — | contenido |
| 13 | `input.h-4.w-4` | — | contenido |
| 14 | `input.h-4.w-4` | — | contenido |
| 15 | `input#evidencia-1` | — | contenido |
| 16 | `button.btn.btn--secondary` | Quitar | contenido |
| 17 | `button.btn.btn--secondary` | Agregar evidencia | contenido |
| 18 | `input#campo-fecha_sesion` | — | contenido |
| 19 | `input#campo-fecha_sesion` | — | contenido |
| 20 | `input#campo-fecha_sesion` | — | contenido |
| 21 | `input#campo-fecha_sesion` | — | contenido |
| 22 | `input#campo-firma_a` | — | contenido |
| 23 | `input#campo-firma_b` | — | contenido |
| 24 | `input#campo-firma_c` | — | contenido |
| 25 | `button.btn.btn--primary` | Guardar dictamen | contenido |
| 26 | `button.btn.btn--secondary` | Iniciar revisión | contenido |

</details>

### `/reemplazos`

<details><summary>1440 px — 15 pulsaciones, el foco sale del documento en la 16</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `a[href="#reportar"].btn.btn--secondary` | Reportar píxel inválido | contenido |
| 12 | `input#campo-punto_origen` | — | contenido |
| 13 | `input.peer.sr-only` | — | contenido |
| 14 | `button.btn.btn--primary` | Crear propuesto | contenido |
| 15 | `a[href="#reportar"].btn.btn--secondary` | Reportar un píxel inválido | contenido |

</details>

<details><summary>390 px — 7 pulsaciones, el foco sale del documento en la 8</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `a[href="#reportar"].btn.btn--secondary` | Reportar píxel inválido | contenido |
| 4 | `input#campo-punto_origen` | — | contenido |
| 5 | `input.peer.sr-only` | — | contenido |
| 6 | `button.btn.btn--primary` | Crear propuesto | contenido |
| 7 | `a[href="#reportar"].btn.btn--secondary` | Reportar un píxel inválido | contenido |

</details>

### `/malla`

<details><summary>1440 px — 21 pulsaciones, el foco sale del documento en la 22</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `input#campo-file-malla` | — | contenido |
| 12 | `a[href="/static/plantillas/malla.csv"].btn.btn--secondary` | Descargar plantilla CSV | contenido |
| 13 | `button.btn.btn--primary` | Subir metadatos | contenido |
| 14 | `input#f-q` | — | contenido |
| 15 | `select#f-per-page` | 25 50 100 | contenido |
| 16 | `button.btn.btn--secondary` | Buscar | contenido |
| 17 | `a[href="/malla"].btn.btn--ghost` | Limpiar | contenido |
| 18 | `div.table-wrap` | Puntos de la malla oficial | contenido |
| 19 | `a[href="http://localhost:8000/malla?page=2"].pagination__page:nth-of-type(1)` | 2 | contenido |
| 20 | `a[href="http://localhost:8000/malla?page=3"].pagination__page:nth-of-type(2)` | 3 | contenido |
| 21 | `a[href="http://localhost:8000/malla?page=2"].pagination__page:nth-of-type(3)` | ›Página siguiente | contenido |

</details>

<details><summary>390 px — 13 pulsaciones, el foco sale del documento en la 14</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `input#campo-file-malla` | — | contenido |
| 4 | `a[href="/static/plantillas/malla.csv"].btn.btn--secondary` | Descargar plantilla CSV | contenido |
| 5 | `button.btn.btn--primary` | Subir metadatos | contenido |
| 6 | `input#f-q` | — | contenido |
| 7 | `select#f-per-page` | 25 50 100 | contenido |
| 8 | `button.btn.btn--secondary` | Buscar | contenido |
| 9 | `a[href="/malla"].btn.btn--ghost` | Limpiar | contenido |
| 10 | `div.table-wrap` | Puntos de la malla oficial | contenido |
| 11 | `a[href="http://localhost:8000/malla?page=2"].pagination__page:nth-of-type(1)` | 2 | contenido |
| 12 | `a[href="http://localhost:8000/malla?page=3"].pagination__page:nth-of-type(2)` | 3 | contenido |
| 13 | `a[href="http://localhost:8000/malla?page=2"].pagination__page:nth-of-type(3)` | ›Página siguiente | contenido |

</details>

### `/mapa`

<details><summary>1440 px — 14 pulsaciones, el foco sale del documento en la 15</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `input#campo-file-mapa` | — | contenido |
| 12 | `summary.cursor-pointer.text-sm` | Formato ancho (.xlsx) | contenido |
| 13 | `summary.cursor-pointer.text-sm` | Formato largo (.csv) | contenido |
| 14 | `button.btn.btn--primary` | Cargar mapa | contenido |

</details>

<details><summary>390 px — 6 pulsaciones, el foco sale del documento en la 7</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `input#campo-file-mapa` | — | contenido |
| 4 | `summary.cursor-pointer.text-sm` | Formato ancho (.xlsx) | contenido |
| 5 | `summary.cursor-pointer.text-sm` | Formato largo (.csv) | contenido |
| 6 | `button.btn.btn--primary` | Cargar mapa | contenido |

</details>

### `/exportar`

<details><summary>1440 px — 11 pulsaciones, el foco sale del documento en la 12</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `a[href="/"].shell-link` | Resumen | navegación |
| 3 | `a[href="/entregas"].shell-link` | Entregas | navegación |
| 4 | `a[href="/malla"].shell-link` | Malla oficial | navegación |
| 5 | `a[href="/mapa"].shell-link` | Mapa MADES | navegación |
| 6 | `a[href="/cruces"].shell-link` | Cruces de terna | navegación |
| 7 | `a[href="/qaqc"].shell-link` | Fichas QA/QC | navegación |
| 8 | `a[href="/reemplazos"].shell-link` | Reemplazos | navegación |
| 9 | `a[href="/exportar"].shell-link` | Exportar | navegación |
| 10 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 11 | `a[href="/exportar/consolidado.xlsx"].btn.btn--primary` | Descargar consolidado (.xlsx) | contenido |

</details>

<details><summary>390 px — 3 pulsaciones, el foco sale del documento en la 4</summary>

| # | Selector | Texto / nombre | Zona |
|---|---|---|---|
| 1 | `a[href="#contenido"].sr-only.focus\:not-sr-only` | Saltar al contenido | cromo |
| 2 | `button#drawer-toggle` | Abrir menú de navegación | cromo |
| 3 | `a[href="/exportar/consolidado.xlsx"].btn.btn--primary` | Descargar consolidado (.xlsx) | contenido |

</details>

## Artefactos

- [`teclado-data.json`](./teclado-data.json): inventario, trayectoria completa y veredicto por ruta.
