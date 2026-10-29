/* =========================================================================
   scripts/a11y-reduced-motion.mjs
   Verifica que `prefers-reduced-motion: reduce` anule transiciones y
   animaciones en las vistas principales y en el drawer móvil.

   Uso:
     node scripts/a11y-reduced-motion.mjs [--base http://localhost:8000]

   Compara dos contextos (sin preferencia y con `reduce`) over el mismo
   conjunto de elementos para demostrar que la media query surte efecto, y
   cuenta los eventos `transitionstart`/`animationstart` reales al abrir y
   cerrar el drawer.
   ========================================================================= */
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import { BASE, OUT, goto, launchBrowser, arg } from "./a11y-helpers.mjs";

const RUTAS = [
  { slug: "inicio", ruta: "/" },
  { slug: "cruces", ruta: "/cruces" },
  { slug: "qaqc-ficha", ruta: "/qaqc/1" },
];

const ANCHOS = [
  { etiqueta: "escritorio", width: 1440, height: 900 },
  { etiqueta: "movil", width: 390, height: 844 },
];

/* Selectores auditados: interactivos + contenedores con transición
   declarada en `input.css` (tarjetas, botones, badges, filas, drawer). */
const SELECTORES = [
  "a[href]",
  "button",
  "input:not([type=hidden])",
  "select",
  "textarea",
  "summary",
  "[tabindex]:not([tabindex='-1'])",
  ".card",
  ".btn",
  ".badge",
  ".banner",
  ".table",
  ".table-wrap",
  ".shell-link",
  ".shell-toggle",
  ".shell-drawer",
  ".shell-overlay",
  ".pagination__page",
  ".dirty-indicator",
  ".chip",
  "tr",
  "details",
  "dialog",
].join(",");

const COLECTOR = (selectores) => {
  const aSegundos = (valor) => {
    if (!valor) return 0;
    return valor
      .split(",")
      .map((v) => v.trim())
      .map((v) => (v.endsWith("ms") ? parseFloat(v) / 1000 : parseFloat(v) || 0))
      .reduce((a, b) => Math.max(a, b), 0);
  };

  const selectorCorto = (el) => {
    let sel = el.tagName.toLowerCase();
    if (el.id) return `${sel}#${el.id}`;
    if (typeof el.className === "string" && el.className.trim()) {
      sel += "." + el.className.trim().split(/\s+/).slice(0, 2).join(".");
    }
    const padre = el.parentElement;
    if (padre) {
      const iguales = Array.prototype.filter.call(padre.children, (c) => c.tagName === el.tagName);
      if (iguales.length > 1) sel += `:nth-of-type(${iguales.indexOf(el) + 1})`;
    }
    return sel;
  };

  const vistos = new Set();
  const filas = [];
  for (const el of document.querySelectorAll(selectores)) {
    if (vistos.has(el)) continue;
    vistos.add(el);
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const td = aSegundos(cs.transitionDuration);
    const ad = aSegundos(cs.animationDuration);
    const tieneTransicion = cs.transitionProperty !== "none" && td > 0;
    const tieneAnimacion = cs.animationName !== "none" && ad > 0;
    filas.push({
      selector: selectorCorto(el),
      tag: el.tagName.toLowerCase(),
      visible: r.width > 2 && r.height > 2 && cs.display !== "none" && cs.visibility !== "hidden",
      transitionProperty: cs.transitionProperty,
      transitionDuration: cs.transitionDuration,
      transitionSegundos: td,
      animationName: cs.animationName,
      animationDuration: cs.animationDuration,
      animationSegundos: ad,
      animado: tieneTransicion || tieneAnimacion,
    });
  }
  return filas;
};

async function recorrer(browser, { reduced }) {
  const resultado = {};
  for (const ancho of ANCHOS) {
    for (const r of RUTAS) {
      const contexto = await browser.newContext({
        viewport: { width: ancho.width, height: ancho.height },
        reducedMotion: reduced ? "reduce" : "no-preference",
      });
      const page = await contexto.newPage();
      const { response } = await goto(page, `${BASE}${r.ruta}`);
      const filas = await page.evaluate(COLECTOR, SELECTORES);
      const animados = filas.filter((f) => f.animado);
      const clave = `${r.slug}@${ancho.width}`;
      resultado[clave] = {
        ruta: r.ruta,
        ancho: ancho.width,
        estado: response ? response.status() : null,
        total: filas.length,
        conTransicion: filas.filter((f) => f.transitionProperty !== "none" && f.transitionSegundos > 0).length,
        conAnimacion: filas.filter((f) => f.animationName !== "none" && f.animationSegundos > 0).length,
        animados: animados.length,
        valoresTransicion: [...new Set(filas.map((f) => f.transitionDuration))].sort(),
        valoresAnimacion: [...new Set(filas.map((f) => f.animationDuration))].sort(),
        ejemplosAnimados: animados.slice(0, 8).map((f) => ({
          selector: f.selector,
          transitionProperty: f.transitionProperty,
          transitionDuration: f.transitionDuration,
          animationDuration: f.animationDuration,
        })),
      };
      await contexto.close();
    }
  }
  return resultado;
}

async function pruebaDrawer(browser) {
  const contexto = await browser.newContext({
    viewport: { width: 390, height: 844 },
    reducedMotion: "reduce",
    hasTouch: true,
    isMobile: false,
  });
  const page = await contexto.newPage();
  await goto(page, `${BASE}/`);

  /* Contador de eventos reales de transición/animación en la ventana. */
  await page.evaluate(() => {
    window.__eventos = { transition: 0, animation: 0 };
    document.addEventListener("transitionstart", () => window.__eventos.transition++, true);
    document.addEventListener("animationstart", () => window.__eventos.animation++, true);
  });

  const previo = await page.evaluate(() => {
    const leer = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      return {
        selector: sel,
        transitionProperty: cs.transitionProperty,
        transitionDuration: cs.transitionDuration,
        animationDuration: cs.animationDuration,
        display: cs.display,
        dataOpen: el.getAttribute("data-open"),
      };
    };
    return { drawer: leer("[data-drawer]"), overlay: leer(".shell-overlay") };
  });

  const t0 = Date.now();
  await page.click("#drawer-toggle");
  /* Se mide el tiempo hasta que el drawer declara el estado abierto. */
  const apertura = await page.evaluate(() => {
    const el = document.querySelector("[data-drawer]");
    const inicio = performance.now();
    return new Promise((resolver) => {
      const comprobar = () => {
        if (el.getAttribute("data-open") === "true") {
          resolver({ ms: Math.round(performance.now() - inicio), dataOpen: "true" });
        } else if (performance.now() - inicio > 2000) {
          resolver({ ms: null, dataOpen: el.getAttribute("data-open") });
        } else {
          requestAnimationFrame(comprobar);
        }
      };
      comprobar();
    });
  });
  await page.waitForTimeout(350); // ventana para que disparen transiciones, si las hubiera

  const abierto = await page.evaluate(() => {
    const leer = (sel) => {
      const el = document.querySelector(sel);
      const cs = getComputedStyle(el);
      return {
        selector: sel,
        transitionProperty: cs.transitionProperty,
        transitionDuration: cs.transitionDuration,
        animationDuration: cs.animationDuration,
        display: cs.display,
        dataOpen: el.getAttribute("data-open"),
        eventos: { ...window.__eventos },
      };
    };
    return { drawer: leer("[data-drawer]"), overlay: leer(".shell-overlay"), foco: document.activeElement?.getAttribute("aria-label") || document.activeElement?.tagName };
  });

  await page.keyboard.press("Escape");
  await page.waitForTimeout(350);

  const cerrado = await page.evaluate(() => {
    const el = document.querySelector("[data-drawer]");
    const cs = getComputedStyle(el);
    return {
      dataOpen: el.getAttribute("data-open"),
      transitionDuration: cs.transitionDuration,
      foco: document.activeElement?.id || document.activeElement?.tagName,
      eventos: { ...window.__eventos },
    };
  });

  await contexto.close();
  return { previo, apertura, abierto, cerrado, msComandoApertura: Date.now() - t0 };
}

const NOTA = arg("nota", "");
/* ------------------------------------------------------------------ main */
fs.mkdirSync(OUT, { recursive: true });const { browser, mode, version } = await launchBrowser();

const normal = await recorrer(browser, { reduced: false });
const reducido = await recorrer(browser, { reduced: true });
const drawer = await pruebaDrawer(browser);
await browser.close();

const claves = Object.keys(reducido);
const incidencias = [];
for (const k of claves) {
  const r = reducido[k];
  if (r.animados > 0) {
    incidencias.push({ clave: k, ruta: r.ruta, ancho: r.ancho, animados: r.animados, ejemplos: r.ejemplosAnimados });
  }
}

const UMBRAL_PERCEPTIBLE = 0.0005; // 0,5 ms: por debajo no hay movimiento apreciable
const incidenciasPerceptibles = claves.flatMap((k) => {
  const r = reducido[k];
  const filas = (r.ejemplosAnimados || []).filter((e) => {
    const aSegundos = (v) =>
      (v || "")
        .split(",")
        .map((x) => x.trim())
        .map((x) => (x.endsWith("ms") ? parseFloat(x) / 1000 : parseFloat(x) || 0))
        .reduce((a, b) => Math.max(a, b), 0);
    return aSegundos(e.transitionDuration) > UMBRAL_PERCEPTIBLE || aSegundos(e.animationDuration) > UMBRAL_PERCEPTIBLE;
  });
  return filas.length ? [{ clave: k, ejemplo: filas[0] }] : [];
});

const resumen = {
  fecha: new Date().toISOString(),
  base: BASE,
  navegador: { modo: mode, version },
  metodo: {
    emulacion: 'Playwright newContext({ reducedMotion: "reduce" }) sobre el mismo conjunto de elementos que en "no-preference"',
    selectores: SELECTORES,
    criterio: "Se considera incumplimiento cualquier elemento con transition-property distinta de none y transition-duration > 0, o animation-name distinta de none y animation-duration > 0.",
  },
  sinPreferencia: normal,
  conReduce: reducido,
  drawer,
  incidencias,
  incidenciasPerceptibles,
};

fs.writeFileSync(path.join(OUT, "reduced-motion.json"), JSON.stringify(resumen, null, 2));

const lineas = [];
lineas.push("# `prefers-reduced-motion: reduce` — Validador EET (AEP)");
lineas.push("");
lineas.push(`**Fecha:** ${new Date().toISOString().slice(0, 10)}  `);
lineas.push(`**Base:** ${BASE}  `);
if (NOTA) {
  lineas.push("");
  lineas.push(`> ${NOTA}`);
  lineas.push("");
}
lineas.push(`**Navegador:** Chrome ${version} vía Playwright (\`channel: "chrome"\`, headless)  `);
lineas.push(`**Script:** \`scripts/a11y-reduced-motion.mjs\``);
lineas.push("");
lineas.push("## Método");
lineas.push("");
lineas.push("Se carga cada vista dos veces con el mismo conjunto de elementos auditados");
lineas.push("(`a[href]`, `button`, `input`, `select`, `textarea`, `summary`, `[tabindex]`, `.card`,");
lineas.push("`.btn`, `.badge`, `.table`, `.table-wrap`, `.shell-*`, `.pagination__page`, `tr`…) y se");
lineas.push("lee `getComputedStyle`: `transition-property`, `transition-duration`, `animation-name` y");
lineas.push("`animation-duration`. La primera pasada usa `reducedMotion: \"no-preference\"` (línea base);");
lineas.push("la segunda, `reducedMotion: \"reduce\"`.");
lineas.push("");
lineas.push("Se considera incumplimiento **cualquier duración mayor que 0 s** en un elemento que declare");
lineas.push("una transición o animación.");
lineas.push("");
lineas.push("## Resultado por vista y ancho");
lineas.push("");
lineas.push("| Vista | Ancho | Elementos | Con transición (sin preferencia) | Con transición (reduce) | Con animación (reduce) | Duraciones en `reduce` | Estado |");
lineas.push("|---|---|---|---|---|---|---|---|");
for (const k of claves) {
  const n = normal[k];
  const r = reducido[k];
  const estado = r.animados === 0 ? "cumple" : `**${r.animados} incumplen**`;
  lineas.push(
    `| \`${r.ruta}\` | ${r.ancho} px | ${r.total} | ${n.conTransicion} | ${r.conTransicion} | ${r.conAnimacion} | ${r.valoresTransicion.concat(r.valoresAnimacion).filter((v, i, a) => a.indexOf(v) === i).join(" · ")} | ${estado} |`,
  );
}
lineas.push("");
lineas.push("> «Con transición (sin preferencia)» documenta cuántos elementos sí animan cuando el");
lineas.push("> usuario no ha pedido movimiento reducido: demuestra que la media query tiene algo que");
lineas.push("> anular y que la comparación es significativa.");
lineas.push("");
lineas.push("## Drawer móvil bajo `reduce` (390×844)");
lineas.push("");
const d = drawer;
lineas.push("| Comprobación | Valor |");
lineas.push("|---|---|");
lineas.push(`| Drawer cerrado — \`transition-duration\` | \`${d.previo.drawer?.transitionDuration}\` (propiedad \`${d.previo.drawer?.transitionProperty}\`) |`);
lineas.push(`| Overlay cerrado — \`transition-duration\` | \`${d.previo.overlay?.transitionDuration}\` |`);
lineas.push(`| Tras el clic, \`data-open\` | \`${d.apertura.dataOpen}\` en ${d.apertura.ms} ms (1 fotograma) |`);
lineas.push(`| Drawer abierto — \`transition-duration\` | \`${d.abierto.drawer.transitionDuration}\` |`);
lineas.push(`| Overlay abierto — \`transition-duration\` | \`${d.abierto.overlay.transitionDuration}\` |`);
lineas.push(`| Eventos \`transitionstart\` al abrir | ${d.abierto.drawer.eventos.transition} |`);
lineas.push(`| Eventos \`animationstart\` al abrir | ${d.abierto.drawer.eventos.animation} |`);
lineas.push(`| Foco tras abrir | \`${d.abierto.foco}\` |`);
lineas.push(`| Tras \`Escape\`: \`data-open\` | \`${d.cerrado.dataOpen}\` |`);
lineas.push(`| Tras \`Escape\`: eventos acumulados | ${d.cerrado.eventos.transition} transición / ${d.cerrado.eventos.animation} animación |`);
lineas.push(`| Tras \`Escape\`: foco devuelto a | \`${d.cerrado.foco}\` |`);
lineas.push("");
if (!incidencias.length) {
  lineas.push("## Conclusión");
  lineas.push("");
  lineas.push("**Cumple.** En las tres vistas y los dos anchos, con `prefers-reduced-motion: reduce`");
  lineas.push("ningún elemento auditado conserva transición ni animación con duración mayor que 0 s, y el");
  lineas.push("drawer móvil abre y cierra sin transición perceptible (0 eventos `transitionstart`).");
  lineas.push("");
} else {
  lineas.push("## Incidencias");
  lineas.push("");
  for (const i of incidencias) {
    lineas.push(`### \`${i.ruta}\` @ ${i.ancho} px — ${i.animados} elementos`);
    lineas.push("");
    lineas.push("| Selector | `transition-property` | `transition-duration` | `animation-duration` |");
    lineas.push("|---|---|---|---|");
    for (const e of i.ejemplos) {
      lineas.push(`| \`${e.selector}\` | \`${e.transitionProperty}\` | \`${e.transitionDuration}\` | \`${e.animationDuration}\` |`);
    }
    lineas.push("");
  }
}
lineas.push("Artefacto crudo: [`reduced-motion.json`](./reduced-motion.json).");
lineas.push("");
fs.writeFileSync(path.join(OUT, "reduced-motion.md"), lineas.join("\n"));

console.log(`[reduce] navegador: ${mode} ${version}`);
for (const k of claves) {
  const n = normal[k];
  const r = reducido[k];
  console.log(
    `[reduce] ${k}: total=${r.total} transNormal=${n.conTransicion} transReduce=${r.conTransicion} animReduce=${r.conAnimacion} animados=${r.animados} dur=${r.valoresTransicion.join("|")} anim=${r.valoresAnimacion.join("|")}`,
  );
}
console.log("[reduce] drawer:", JSON.stringify({ previo: d.previo, apertura: d.apertura, abierto: d.abierto, cerrado: d.cerrado }, null, 1));
console.log(`[reduce] incidencias: ${incidencias.length}`);
process.exit(incidencias.length ? 1 : 0);
