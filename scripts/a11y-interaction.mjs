#!/usr/bin/env node
/* =========================================================================
   scripts/a11y-interaction.mjs
   Pruebas de interacción que la auditoría por reglas no cubre:
     1) Navegación por teclado desde el inicio (skip link) a 390x844.
     2) Drawer móvil: apertura, aria-expanded, Escape y retorno del foco.
     3) Zoom 200 % / reflow a 720 px de ancho efectivo en / y /qaqc/1.

   Uso:
     node scripts/a11y-interaction.mjs

   Salida:
     docs/qa-visual/interaction-data.json
     docs/qa-visual/axe/drawer-390.json   (axe sobre el drawer abierto)
   ========================================================================= */
import fs from "node:fs";
import path from "node:path";
import { BASE, OUT, ROOT, PAGE_HELPERS, launchBrowser, goto } from "./a11y-helpers.mjs";

const AXE_PATH = path.join(ROOT, "node_modules", "axe-core", "axe.min.js");
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

const data = {
  generado: new Date().toISOString(),
  base: BASE,
  navegador: null,
  teclado: null,
  drawer: null,
  drawerAxe: null,
  zoom: [],
};

const log = (...a) => console.log(...a);

/* ------------------------------ 1) teclado ------------------------------- */
async function pruebaTeclado(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    locale: "es-ES",
  });
  const page = await context.newPage();
  await page.addInitScript(PAGE_HELPERS);
  await goto(page, BASE + "/");

  const paradas = [];
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    await page.waitForTimeout(120);
    paradas.push(await page.evaluate(() => window.__a11yFocus()));
  }

  const primero = paradas[0];
  const esSkipLink = primero.href === "#contenido" && /saltar al contenido/i.test(primero.text);
  const visible = !!primero.visible;

  // ¿existe el destino del skip link?
  const destino = await page.evaluate(() => {
    const el = document.querySelector("#contenido");
    return el ? { existe: true, tag: el.tagName.toLowerCase() } : { existe: false };
  });

  // Secuencia limpia: recargar, Tab una vez (skip link) y Enter.
  await goto(page, BASE + "/");
  await page.keyboard.press("Tab");
  const focoSkip = await page.evaluate(() => window.__a11yFocus());
  await page.keyboard.press("Enter");
  await page.waitForTimeout(350);
  const trasEnter = await page.evaluate(() => ({
    hash: location.hash,
    foco: window.__a11yFocus(),
    scrollY: Math.round(window.scrollY),
  }));

  // Tras activar el skip link, el siguiente Tab debe entrar en #contenido
  // (el punto de inicio de navegación secuencial se mueve al destino).
  await page.keyboard.press("Tab");
  await page.waitForTimeout(200);
  const siguienteFoco = await page.evaluate(() => {
    const el = document.activeElement;
    return {
      foco: window.__a11yFocus(),
      dentroDeContenido: !!el && !!el.closest("#contenido"),
      enNavegacion: !!el && !!el.closest("nav, aside, [data-drawer], body > header"),
    };
  });

  await context.close();

  const ok = esSkipLink && visible;
  return {
    ancho: "390x844",
    ruta: "/",
    pasos: [      {
        paso: "Primer Tab desde el inicio de la página",
        ok: esSkipLink,
        detalle: `Foco en <${primero.tag}> href="${primero.href}" texto="${primero.text}"`,
      },
      {
        paso: "El skip link es visible al recibir foco",
        ok: visible,
        detalle:
          `rect=${primero.rect.w}x${primero.rect.h} en (${primero.rect.x},${primero.rect.y}); ` +
          `clip-path=${primero.clipPath}; position=${primero.position}; display=${primero.display}`,
      },
      {
        paso: "El destino #contenido existe",
        ok: destino.existe,
        detalle: destino.existe ? `<${destino.tag}> con id="contenido" (skippable)` : "no se encontró #contenido",
      },
      {
        paso: "Enter activa el skip link (#contenido)",
        ok: trasEnter.hash === "#contenido",
        detalle: `hash="${trasEnter.hash}" foco=<${trasEnter.foco.tag}#${trasEnter.foco.id}> scrollY=${trasEnter.scrollY}`,
      },
      {
        paso: "El Tab posterior entra directamente en #contenido (no recorre la navegación)",
        ok: siguienteFoco.dentroDeContenido && !siguienteFoco.enNavegacion,
        detalle:
          `foco=<${siguienteFoco.foco.tag}> "${siguienteFoco.foco.text.slice(0, 50)}" ` +
          `href="${siguienteFoco.foco.href}" dentroDeContenido=${siguienteFoco.dentroDeContenido} ` +
          `enNavegacion=${siguienteFoco.enNavegacion}`,
      },
    ],
    ordenDeTabulacion: paradas.map((p, i) => ({
      orden: i + 1,
      tag: p.tag,
      id: p.id,
      href: p.href,
      texto: p.text,
      ariaLabel: p.ariaLabel,
      y: p.rect.y,
      visible: p.visible,
    })),
    resultado: ok ? "PASA" : "FALLA",
  };
}

/* ------------------------------- 2) drawer ------------------------------- */
async function pruebaDrawer(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    locale: "es-ES",
  });
  const page = await context.newPage();
  await page.addInitScript(PAGE_HELPERS);
  await goto(page, BASE + "/");

  const estado = () =>
    page.evaluate(() => {
      const toggle = document.querySelector("[data-drawer-toggle]");
      const drawer = document.querySelector("[data-drawer]");
      const overlay = document.querySelector("[data-overlay]");
      const r = drawer ? drawer.getBoundingClientRect() : null;
      const cs = drawer ? getComputedStyle(drawer) : null;
      return {
        toggleVisible: toggle ? getComputedStyle(toggle).display !== "none" : false,
        ariaExpanded: toggle ? toggle.getAttribute("aria-expanded") : null,
        ariaControls: toggle ? toggle.getAttribute("aria-controls") : null,
        toggleLabel: toggle ? toggle.getAttribute("aria-label") : null,
        drawerOpen: drawer ? drawer.getAttribute("data-open") : null,
        overlayOpen: overlay ? overlay.getAttribute("data-open") : null,
        drawerDisplay: cs ? cs.display : null,
        drawerRect: r ? { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x) } : null,
        scrollBloqueado: document.body.style.overflow === "hidden",
        foco: window.__a11yFocus(),
      };
    });

  const antes = await estado();

  // Click real (como un usuario) con fallback forzado si algo lo intercepta
  let metodoClick = "locator.click";
  try {
    await page.locator("[data-drawer-toggle]").click({ timeout: 5000 });
  } catch (err) {
    metodoClick = "click({force:true}) — " + String(err.message).split("\n")[0];
    await page.locator("[data-drawer-toggle]").click({ force: true });
  }
  await page.waitForTimeout(250);
  const abierto = await estado();

  // axe sobre el drawer abierto
  let drawerAxe = null;
  try {
    await page.addScriptTag({ path: AXE_PATH });
    const results = await page.evaluate(
      ({ tags }) =>
        window.axe.run(document.querySelector("[data-drawer]"), {
          runOnly: { type: "tag", values: tags },
          resultTypes: ["violations", "incomplete"],
        }),
      { tags: AXE_TAGS }
    );
    drawerAxe = {
      archivo: "docs/qa-visual/axe/drawer-390.json",
      violaciones: results.violations.length,
      nodos: results.violations.reduce((a, v) => a + v.nodes.length, 0),
      detalle: results.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodos: v.nodes.length })),
    };
    fs.writeFileSync(
      path.join(OUT, "axe", "drawer-390.json"),
      JSON.stringify(
        {
          ruta: "/ (drawer abierto)",
          slug: "drawer",
          ancho: "390x844",
          url: results.url,
          timestamp: results.timestamp,
          runOnly: { type: "tag", values: AXE_TAGS },
          nota: "axe se ejecutó sobre el elemento [data-drawer] con el drawer abierto (aria-modal=\"true\").",
          conteos: {
            violations: results.violations.length,
            incomplete: results.incomplete.length,
            passes: results.passes.length,
            nodosAfectados: drawerAxe.nodos,
          },
          violations: results.violations,
          incomplete: results.incomplete,
        },
        null,
        2
      )
    );
  } catch (err) {
    drawerAxe = { error: String(err.message).split("\n")[0] };
  }

  // Sonda adicional (fuera del alcance obligatorio): ¿el diálogo modal retiene
  // el foco mientras está abierto? Se tabula 12 veces y se anota la primera
  // salida fuera de [data-drawer].
  const secuenciaFoco = [];
  let fugaEn = null;
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    await page.waitForTimeout(70);
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      return {
        tag: el ? el.tagName.toLowerCase() : null,
        texto: el ? (el.textContent || "").trim().slice(0, 28) : "",
        href: el && el.getAttribute ? el.getAttribute("href") : null,
        ariaLabel: el && el.getAttribute ? el.getAttribute("aria-label") : null,
        enDrawer: !!el && !!el.closest("[data-drawer]"),
      };
    });
    secuenciaFoco.push({ paso: i + 1, ...info });
    if (!info.enDrawer && fugaEn === null) fugaEn = i + 1;
  }

  // Escape
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  const cerrado = await estado();

  await context.close();
  const devuelveFoco = cerrado.foco && cerrado.foco.id === "drawer-toggle";
  const ok = abierto.ariaExpanded === "true" && abierto.drawerOpen === "true" &&
    cerrado.ariaExpanded === "false" && cerrado.drawerOpen === "false" && devuelveFoco;

  return {
    ancho: "390x844",
    ruta: "/",
    metodoClick,
    estadoInicial: antes,
    trasAbrir: abierto,
    trasEscape: cerrado,
    sondaTrampaDeFoco: {
      nota:
        "Sonda adicional no exigida por el encargo: con el drawer abierto (aria-modal=\"true\") se pulsó Tab " +
        "12 veces. Un 0 significaría que el foco nunca sale del diálogo.",
      fugaEn,
      secuencia: secuenciaFoco,
    },
    pasos: [
      {
        paso: "El botón hamburguesa es visible a 390 px",
        ok: antes.toggleVisible,
        detalle: `aria-label="${antes.toggleLabel}" aria-controls="${antes.ariaControls}"`,
      },
      {
        paso: "Al abrir, aria-expanded pasa a \"true\"",
        ok: abierto.ariaExpanded === "true",
        detalle: `aria-expanded="${abierto.ariaExpanded}", data-open="${abierto.drawerOpen}", overlay="${abierto.overlayOpen}"`,
      },
      {
        paso: "El drawer queda visible como diálogo",
        ok: abierto.drawerOpen === "true" && abierto.drawerDisplay !== "none",
        detalle: `display=${abierto.drawerDisplay} rect=${JSON.stringify(abierto.drawerRect)} scroll-bloqueado=${abierto.scrollBloqueado}`,
      },
      {
        paso: "El foco entra en el drawer (botón cerrar)",
        ok: !!abierto.foco && /cerrar menú/i.test(abierto.foco.ariaLabel || ""),
        detalle: `foco=<${abierto.foco.tag}> aria-label="${abierto.foco.ariaLabel}"`,
      },
      {
        paso: "Escape cierra el drawer y aria-expanded vuelve a \"false\"",
        ok: cerrado.ariaExpanded === "false" && cerrado.drawerOpen === "false",
        detalle: `aria-expanded="${cerrado.ariaExpanded}", data-open="${cerrado.drawerOpen}", overlay="${cerrado.overlayOpen}"`,
      },
      {
        paso: "El foco regresa al botón de apertura",
        ok: devuelveFoco,
        detalle: `foco=<${cerrado.foco.tag}#${cerrado.foco.id}> aria-label="${cerrado.foco.ariaLabel}"`,
      },
    ],
    resultado: ok ? "PASA" : "FALLA",
  };
}

/* -------------------------------- 3) zoom -------------------------------- */
async function medir(page) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(200);
  return page.evaluate(() => window.__a11yScanAnchos(5));
}

async function pruebaZoom(browser) {
  const resultados = [];

  for (const ruta of ["/", "/qaqc/1"]) {
    // (a) zoom 200 % aplicado con CSS sobre el viewport de 1440x900
    const ctxZoom = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
      locale: "es-ES",
    });
    const page = await ctxZoom.newPage();
    await page.addInitScript(PAGE_HELPERS);
    await goto(page, BASE + ruta);

    const base = await medir(page);
    await page.evaluate(() => {
      document.documentElement.style.zoom = "2";
    });
    await page.waitForTimeout(600);
    const zoom = await medir(page);

    // Diagnóstico: ¿siguen activas las media queries de escritorio?
    const diagnostico = await page.evaluate(() => {
      const aside = document.querySelector("aside");
      return {
        zoomAplicado: document.documentElement.style.zoom,
        clientWidth: document.documentElement.clientWidth,
        media1024: window.matchMedia("(min-width: 1024px)").matches,
        sidebarDisplay: aside ? getComputedStyle(aside).display : "sin aside",
        bodyScrollWidth: document.body.scrollWidth,
      };
    });
    await ctxZoom.close();

    // (b) emulación fiel de zoom de navegador al 200 %: el viewport CSS se
    // reduce a la mitad y el factor de dispositivo sube a 2.
    const ctxReal = await browser.newContext({
      viewport: { width: 720, height: 450 },
      deviceScaleFactor: 2,
      locale: "es-ES",
    });
    const pageReal = await ctxReal.newPage();
    await pageReal.addInitScript(PAGE_HELPERS);
    await goto(pageReal, BASE + ruta);
    const zoomReal = await medir(pageReal);
    const diagReal = await pageReal.evaluate(() => {
      const aside = document.querySelector("aside");
      return {
        dpr: window.devicePixelRatio,
        media1024: window.matchMedia("(min-width: 1024px)").matches,
        sidebarDisplay: aside ? getComputedStyle(aside).display : "sin aside",
      };
    });
    await ctxReal.close();

    // (c) reflow a 720 px de ancho (WCAG 1.4.10 sin zoom)
    const ctx720 = await browser.newContext({
      viewport: { width: 720, height: 900 },
      deviceScaleFactor: 1,
      locale: "es-ES",
    });
    const page720 = await ctx720.newPage();
    await page720.addInitScript(PAGE_HELPERS);
    await goto(page720, BASE + ruta);
    const ancho720 = await medir(page720);
    await ctx720.close();

    resultados.push({
      ruta,
      base1440: {
        innerWidth: base.innerWidth,
        scrollWidth: base.scrollWidth,
        desborda: base.desborda,
        scrollHorizontalReal: base.scrollHorizontalReal,
      },
      zoomCss200: {
        diagnostico,
        medida: {
          innerWidth: zoom.innerWidth,
          scrollWidth: zoom.scrollWidth,
          clientWidth: zoom.clientWidth,
          documentHeight: zoom.documentHeight,
          desborda: zoom.desborda,
          scrollHorizontalReal: zoom.scrollHorizontalReal,
          scrollXmax: zoom.scrollXmax,
          totalFueraDeScroller: zoom.totalFueraDeScroller,
        },
        offenders: zoom.offenders,
      },
      zoomNavegador200: {
        emulacion: "viewport 720x450 con deviceScaleFactor 2",
        diagnostico: diagReal,
        medida: {
          innerWidth: zoomReal.innerWidth,
          scrollWidth: zoomReal.scrollWidth,
          desborda: zoomReal.desborda,
          scrollHorizontalReal: zoomReal.scrollHorizontalReal,
          scrollXmax: zoomReal.scrollXmax,
          totalFueraDeScroller: zoomReal.totalFueraDeScroller,
        },
        offenders: zoomReal.offenders,
        ok: !zoomReal.desborda && !zoomReal.scrollHorizontalReal,
      },
      reflow720: {
        medida: {
          innerWidth: ancho720.innerWidth,
          scrollWidth: ancho720.scrollWidth,
          desborda: ancho720.desborda,
          scrollHorizontalReal: ancho720.scrollHorizontalReal,
          scrollXmax: ancho720.scrollXmax,
          totalFueraDeScroller: ancho720.totalFueraDeScroller,
        },
        offenders: ancho720.offenders,
        ok: !ancho720.desborda && !ancho720.scrollHorizontalReal,
      },
    });
    const r = resultados.at(-1);
    log(
      `  ${ruta}: zoom css200 ${zoom.desborda ? "DESBORDA" : "ok"} · ` +
        `zoom navegador 200 % ${r.zoomNavegador200.ok ? "ok" : "DESBORDA"} · ` +
        `reflow 720 ${r.reflow720.ok ? "ok" : "DESBORDA"}`
    );
  }

  return resultados;
}

/* ---------------------------------- main --------------------------------- */
async function main() {
  const { browser, mode, version } = await launchBrowser();
  data.navegador = mode;
  data.navegadorVersion = version;

  log("\n[1] teclado y skip link (390x844, /)");
  data.teclado = await pruebaTeclado(browser);
  for (const p of data.teclado.pasos) log(`  ${p.ok ? "PASA" : "FALLA"} · ${p.paso} · ${p.detalle}`);
  data.teclado.todosLosPasosOk = data.teclado.pasos.every((p) => p.ok);
  log(`  => ${data.teclado.resultado} (todos los pasos: ${data.teclado.todosLosPasosOk ? "sí" : "no"})`);

  log("\n[2] drawer móvil (390x844, /)");
  data.drawer = await pruebaDrawer(browser);
  for (const p of data.drawer.pasos) log(`  ${p.ok ? "PASA" : "FALLA"} · ${p.paso} · ${p.detalle}`);
  data.drawer.todosLosPasosOk = data.drawer.pasos.every((p) => p.ok);
  log(`  => ${data.drawer.resultado} (todos los pasos: ${data.drawer.todosLosPasosOk ? "sí" : "no"})`);

  log("\n[3] zoom 200 % y reflow 720 px");
  data.zoom = await pruebaZoom(browser);

  await browser.close();

  // recuperar el archivo del drawer para el resumen
  const drawFile = path.join(OUT, "axe", "drawer-390.json");
  if (fs.existsSync(drawFile)) {
    const dj = JSON.parse(fs.readFileSync(drawFile, "utf8"));
    data.drawerAxe = {
      archivo: "docs/qa-visual/axe/drawer-390.json",
      violaciones: dj.conteos.violations,
      nodos: dj.conteos.nodosAfectados,
      detalle: dj.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodos: v.nodes.length })),
    };
  }

  fs.writeFileSync(path.join(OUT, "interaction-data.json"), JSON.stringify(data, null, 2));
  log(`\n[ok] ${path.join(OUT, "interaction-data.json")}`);
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
