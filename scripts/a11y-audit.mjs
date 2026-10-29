#!/usr/bin/env node
/* =========================================================================
   scripts/a11y-audit.mjs
   Auditoría automatizada de accesibilidad (axe-core) y responsive de las 11
   vistas del Validador EET, con Chrome del sistema vía Playwright.

   Uso:
     node scripts/a11y-audit.mjs
     node scripts/a11y-audit.mjs --base http://127.0.0.1:8017 --out docs/qa-visual

   Salidas:
     docs/qa-visual/axe/<slug>.json          resultado axe por ruta
     docs/qa-visual/axe/resumen.md           resumen legible por ruta
     docs/qa-visual/capturas/<slug>-<w>.png  capturas fullPage 1440/768/390
     docs/qa-visual/audit-data.json          datos crudos para el informe
   ========================================================================= */
import fs from "node:fs";
import path from "node:path";
import { BASE, OUT, ROOT, PAGE_HELPERS, launchBrowser, goto } from "./a11y-helpers.mjs";

const AXE_DIR = path.join(OUT, "axe");
const SHOT_DIR = path.join(OUT, "capturas");
const AXE_PATH = path.join(ROOT, "node_modules", "axe-core", "axe.min.js");

const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

const ROUTES = [
  { slug: "inicio", url: "/", nombre: "Resumen" },
  { slug: "entregas", url: "/entregas", nombre: "Entregas (listado y carga)" },
  { slug: "entrega-preview", url: "/entregas/1/preview", nombre: "Vista previa de entrega" },
  { slug: "cruces", url: "/cruces", nombre: "Cruces de terna" },
  { slug: "cruce-detalle", url: "/cruces/81/2008", nombre: "Detalle de cruce" },
  { slug: "qaqc-lista", url: "/qaqc", nombre: "Fichas QA/QC (lista)" },
  { slug: "qaqc-ficha", url: "/qaqc/1", nombre: "Ficha QA/QC (formulario)" },
  { slug: "reemplazos", url: "/reemplazos", nombre: "Reemplazos" },
  { slug: "malla", url: "/malla", nombre: "Malla oficial" },
  { slug: "mapa", url: "/mapa", nombre: "Mapa MADES" },
  { slug: "exportar", url: "/exportar", nombre: "Exportar" },
];

const VIEWPORTS = [
  { name: "1440", width: 1440, height: 900, shot: true },
  { name: "1024", width: 1024, height: 768, shot: false },
  { name: "768", width: 768, height: 1024, shot: true },
  { name: "390", width: 390, height: 844, shot: true },
  { name: "360", width: 360, height: 800, shot: false },
  { name: "320", width: 320, height: 720, shot: false },
];

fs.mkdirSync(AXE_DIR, { recursive: true });
fs.mkdirSync(SHOT_DIR, { recursive: true });

const log = (...a) => console.log(...a);
const IMPACT_ORDER = ["critical", "serious", "moderate", "minor"];

function contarImpactos(violations) {
  const acc = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  let nodos = 0;
  for (const v of violations) {
    const k = v.impact || "minor";
    acc[k] = (acc[k] || 0) + 1;
    nodos += v.nodes.length;
  }
  return { porImpacto: acc, totalReglas: violations.length, totalNodos: nodos };
}

const esc = (s) => String(s == null ? "" : s).replace(/\|/g, "\\|").replace(/\n/g, " ");

async function correrAxe(page) {
  await page.addScriptTag({ path: AXE_PATH });
  return page.evaluate(
    ({ tags }) =>
      window.axe.run(document, {
        runOnly: { type: "tag", values: tags },
        resultTypes: ["violations", "incomplete"],
      }),
    { tags: AXE_TAGS }
  );
}

/* Guarda <slug>[-sufijo].json y devuelve el registro resumido. */
function guardarAxe(record, results, { sufijo = "", ancho }) {
  const impactos = contarImpactos(results.violations);
  const incompletos = results.incomplete.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodos: v.nodes.length,
  }));
  const nombre = sufijo ? `${record.slug}-${sufijo}.json` : `${record.slug}.json`;

  fs.writeFileSync(
    path.join(AXE_DIR, nombre),
    JSON.stringify(
      {
        ruta: record.url,
        slug: record.slug,
        ancho,
        url: results.url,
        timestamp: results.timestamp,
        axeVersion: results.testEngine ? results.testEngine.version : null,
        runOnly: { type: "tag", values: AXE_TAGS },
        nota:
          "Se conservan íntegras las listas violations e incomplete. Las comprobaciones superadas (passes) e " +
          "inaplicables (inapplicable) no se almacenan nodo a nodo para mantener el archivo legible.",
        conteos: {
          violations: results.violations.length,
          incomplete: results.incomplete.length,
          passes: results.passes.length,
          inapplicable: results.inapplicable.length,
          nodosAfectados: impactos.totalNodos,
          porImpacto: impactos.porImpacto,
        },
        violations: results.violations,
        incomplete: results.incomplete,
      },
      null,
      2
    )
  );

  return {
    archivo: `docs/qa-visual/axe/${nombre}`,
    axeVersion: results.testEngine ? results.testEngine.version : null,
    urlFinal: results.url,
    timestamp: results.timestamp,
    violaciones: results.violations,
    incompletos,
    impactos,
  };
}

async function main() {
  if (!fs.existsSync(AXE_PATH)) {
    throw new Error(`No se encuentra axe.min.js en ${AXE_PATH}. Ejecuta: npm install --save-dev axe-core playwright`);
  }

  const { browser, mode, version, fallos } = await launchBrowser();
  const axeVersion = JSON.parse(
    fs.readFileSync(path.join(ROOT, "node_modules", "axe-core", "package.json"), "utf8")
  ).version;

  const data = {
    generado: new Date().toISOString(),
    base: BASE,
    navegador: mode,
    navegadorVersion: version,
    fallosLanzamiento: fallos,
    axeTags: AXE_TAGS,
    axeVersion,
    viewports: VIEWPORTS,
    rutas: [],
  };

  for (const route of ROUTES) {
    log(`\n=== ${route.url} (${route.slug}) ===`);
    const record = {
      slug: route.slug,
      url: route.url,
      nombre: route.nombre,
      status: null,
      ok: false,
      errorCarga: null,
      consola: [],
      pageErrors: [],
      recursosFallidos: [],
      violaciones: [],
      incompletos: [],
      impactos: null,
      axeVersion: null,
      anchos: {},
      capturas: [],
    };
    const vistos = new Set();
    const pushUnico = (arr, texto) => {
      const clave = String(texto).trim();
      if (!clave || vistos.has(clave)) return;
      vistos.add(clave);
      arr.push(clave);
    };

    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        deviceScaleFactor: 1,
        locale: "es-ES",
        timezoneId: "America/Asuncion",
        colorScheme: "light",
      });
      const page = await context.newPage();
      await page.addInitScript(PAGE_HELPERS);

      page.on("console", (msg) => {
        if (msg.type() === "error") pushUnico(record.consola, `${vp.name}px · ${msg.text()}`);
      });
      page.on("pageerror", (err) => pushUnico(record.pageErrors, `${vp.name}px · ${err.message}`));
      page.on("requestfailed", (req) => {
        const f = req.failure();
        pushUnico(record.recursosFallidos, `${vp.name}px · ${req.url()} · ${f ? f.errorText : "?"}`);
      });
      page.on("response", (res) => {
        if (res.status() >= 400) {
          pushUnico(record.recursosFallidos, `${vp.name}px · HTTP ${res.status()} · ${res.url()}`);
        }
      });

      const { response, error } = await goto(page, BASE + route.url);
      if (response) {
        record.status = response.status();
        record.ok = response.status() >= 200 && response.status() < 400;
      }
      if (error) record.errorCarga = error;

      const shot = path.join(SHOT_DIR, `${route.slug}-${vp.name}.png`);
      if (vp.shot) {
        try {
          await page.screenshot({ path: shot, fullPage: true });
          record.capturas.push(path.relative(ROOT, shot).replace(/\\/g, "/"));
        } catch (err) {
          log(`  [aviso] screenshot ${vp.name}: ${err.message.split("\n")[0]}`);
        }
      }

      try {
        record.anchos[vp.name] = await page.evaluate(() => window.__a11yScanAnchos(5));
      } catch (err) {
        record.anchos[vp.name] = { error: String(err.message).split("\n")[0] };
      }

      if ((vp.name === "1440" || vp.name === "390") && record.ok) {
        try {
          const results = await correrAxe(page);
          if (vp.name === "1440") {
            const g = guardarAxe(record, results, { ancho: `${vp.width}x${vp.height}` });
            Object.assign(record, {
              axeVersion: g.axeVersion,
              violaciones: g.violaciones,
              incompletos: g.incompletos,
              impactos: g.impactos,
              urlFinal: g.urlFinal,
              timestamp: g.timestamp,
              archivoAxe: g.archivo,
            });
            log(
              `  1440 axe: ${g.violaciones.length} reglas / ${g.impactos.totalNodos} nodos · ` +
                `crit ${g.impactos.porImpacto.critical} · ser ${g.impactos.porImpacto.serious} · ` +
                `mod ${g.impactos.porImpacto.moderate} · min ${g.impactos.porImpacto.minor}`
            );
          } else {
            const g = guardarAxe(record, results, { sufijo: "390", ancho: `${vp.width}x${vp.height}` });
            record.axe390 = {
              archivo: g.archivo,
              violaciones: g.violaciones,
              incompletos: g.incompletos,
              impactos: g.impactos,
            };
            log(
              `  390 axe: ${g.violaciones.length} reglas / ${g.impactos.totalNodos} nodos · ` +
                `crit ${g.impactos.porImpacto.critical} · ser ${g.impactos.porImpacto.serious} · ` +
                `mod ${g.impactos.porImpacto.moderate} · min ${g.impactos.porImpacto.minor}`
            );
          }
        } catch (err) {
          const clave = vp.name === "1440" ? "errorAxe" : "errorAxe390";
          record[clave] = String(err.message).split("\n")[0];
          log(`  [error axe ${vp.name}] ${record[clave]}`);
        }
      }

      const m = record.anchos[vp.name];
      if (m && !m.error) {
        log(
          `  ${vp.name}px: scrollWidth=${m.scrollWidth} innerWidth=${m.innerWidth}` +
            (m.desborda ? `  DESBORDA (${m.totalFueraDeScroller} fuera de scroller)` : "  ok")
        );
      }

      await context.close();
    }

    if (!record.ok) {
      record.errorCarga = record.errorCarga || `HTTP ${record.status}`;
      log(`  [CRITICO] la ruta no respondió correctamente: ${record.errorCarga}`);
    }
    data.rutas.push(record);
  }

  await browser.close();
  fs.writeFileSync(path.join(OUT, "audit-data.json"), JSON.stringify(data, null, 2));

  /* --------------------------- axe/resumen.md ---------------------------- */
  const L = [];
  L.push("# Auditoría axe-core — Validador EET", "");
  L.push(`- **Fecha:** ${new Date().toISOString().slice(0, 19).replace("T", " ")}`);
  L.push(`- **Base:** ${BASE}`);
  L.push(`- **Navegador:** Chrome ${version} (Playwright, \`channel: "chrome"\`)`);
  L.push(`- **axe-core:** ${axeVersion}`);
  L.push(`- **Etiquetas de reglas:** ${AXE_TAGS.join(", ")}`);
  L.push(
    `- **Anchos medidos:** ${VIEWPORTS.map((v) => `${v.name} px`).join(", ")} ` +
      `(axe se ejecuta a 1440 y 390; capturas a 1440, 768 y 390)`
  );
  L.push("");
  L.push("## Resumen por ruta");
  L.push("");
  L.push("| Ruta | Estado | 1440: crit | ser | mod | min | reglas | nodos | 390: crit | ser | mod | min | reglas | nodos |");
  L.push("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|");
  for (const r of data.rutas) {
    const p = r.impactos ? r.impactos.porImpacto : null;
    const m = r.axe390 ? r.axe390.impactos.porImpacto : null;
    L.push(
      `| \`${r.url}\` | ${r.ok ? r.status : `ERROR ${r.status ?? "-"}`} | ` +
        (p
          ? `${p.critical} | ${p.serious} | ${p.moderate} | ${p.minor} | ${r.impactos.totalReglas} | ${r.impactos.totalNodos}`
          : "- | - | - | - | - | -") +
        " | " +
        (m
          ? `${m.critical} | ${m.serious} | ${m.moderate} | ${m.minor} | ${r.axe390.impactos.totalReglas} | ${r.axe390.impactos.totalNodos}`
          : "- | - | - | - | - | -") +
        " |"
    );
  }
  L.push("");
  L.push("> Las cifras de 1440 px usan el resultado principal (`axe/<slug>.json`); las de 390 px,");
  L.push("> la pasada móvil adicional (`axe/<slug>-390.json`).");
  L.push("");
  L.push("## Detalle por ruta (1440x900)");
  L.push("");
  for (const r of data.rutas) {
    L.push(`### ${r.url}`);
    L.push("");
    if (!r.ok) {
      L.push(`No auditada: la ruta no respondió correctamente (${r.errorCarga}).`);
      L.push("");
      continue;
    }
    if (!r.violaciones || r.violaciones.length === 0) {
      L.push("Sin violaciones detectadas.");
      L.push("");
      continue;
    }
    L.push("| Regla | Impacto | Ayuda | Nodos | Ejemplo |");
    L.push("|---|---|---|---|---|");
    const orden = [...r.violaciones].sort(
      (a, b) => IMPACT_ORDER.indexOf(a.impact || "minor") - IMPACT_ORDER.indexOf(b.impact || "minor")
    );
    for (const v of orden) {
      const ejemplo = v.nodes[0] ? String(v.nodes[0].target[0] || "").replace(/\|/g, "\\|") : "";
      L.push(`| \`${v.id}\` | ${v.impact} | ${esc(v.help)} | ${v.nodes.length} | \`${ejemplo}\` |`);
    }
    L.push("");
  }
  const conMovil = data.rutas.filter((r) => r.axe390 && r.axe390.violaciones.length > 0);
  L.push("## Detalle móvil (390x844)");
  L.push("");
  if (conMovil.length === 0) {
    L.push("Sin violaciones en ninguno de los 11 anchos móviles.");
    L.push("");
  } else {
    for (const r of conMovil) {
      L.push(`### ${r.url}`);
      L.push("");
      L.push("| Regla | Impacto | Ayuda | Nodos | Ejemplo |");
      L.push("|---|---|---|---|---|");
      for (const v of r.axe390.violaciones) {
        const ejemplo = v.nodes[0] ? String(v.nodes[0].target[0] || "").replace(/\|/g, "\\|") : "";
        L.push(`| \`${v.id}\` | ${v.impact} | ${esc(v.help)} | ${v.nodes.length} | \`${ejemplo}\` |`);
      }
      L.push("");
    }
  }
  fs.writeFileSync(path.join(AXE_DIR, "resumen.md"), L.join("\n"));
  log(`\n[ok] ${path.join(AXE_DIR, "resumen.md")}`);
  log(`[ok] ${path.join(OUT, "audit-data.json")}`);
}

main().catch((err) => {
  console.error("\n[fatal]", err);
  process.exit(1);
});
