/* =========================================================================
   scripts/a11y-axe-recheck.mjs
   Regresión de axe-core sobre las 11 rutas a 1440 y 390 px. Se usa tras
   cambiar CSS o plantillas para confirmar que no aparecen violaciones nuevas.

   Uso:
     node scripts/a11y-axe-recheck.mjs [--base http://127.0.0.1:8018]
   ========================================================================= */
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import { arg, BASE, OUT, ROOT } from "./a11y-helpers.mjs";

const RUTAS = [
  "/",
  "/entregas",
  "/entregas/1/preview",
  "/cruces",
  "/cruces/81/2008",
  "/qaqc",
  "/qaqc/1",
  "/reemplazos",
  "/malla",
  "/mapa",
  "/exportar",
];
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

const axeSource = fs.readFileSync(path.join(ROOT, "node_modules", "axe-core", "axe.min.js"), "utf8");
const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--no-sandbox"] });
const salida = { base: BASE, fecha: new Date().toISOString(), tags: TAGS, rutas: [] };

for (const ancho of [1440, 390]) {
  for (const ruta of RUTAS) {
    const page = await browser.newPage({ viewport: { width: ancho, height: 900 } });
    await page.goto(`${BASE}${ruta}`, { waitUntil: "networkidle" });
    await page.addScriptTag({ content: axeSource });
    const res = await page.evaluate(async (tags) => window.axe.run(document, { runOnly: { type: "tag", values: tags } }), TAGS);
    salida.rutas.push({
      ruta,
      ancho,
      violaciones: res.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
      incompletos: res.incomplete.map((v) => ({ id: v.id, nodes: v.nodes.length })),
    });
    await page.close();
  }
}
await browser.close();

const total = salida.rutas.reduce((a, r) => a + r.violaciones.length, 0);
salida.violacionesTotales = total;
fs.writeFileSync(path.join(OUT, "axe-recheck.json"), JSON.stringify(salida, null, 2));

console.log(`[axe] ${BASE}: ${total} violaciones en 11 rutas × 1440/390 px`);
for (const r of salida.rutas) {
  if (r.violaciones.length) console.log(`  ${r.ruta} @${r.ancho}: ${JSON.stringify(r.violaciones)}`);
}
process.exit(total ? 1 : 0);
