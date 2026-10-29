/* =========================================================================
   scripts/lighthouse-audit.mjs
   Ejecuta Lighthouse (Chrome del sistema) sobre las rutas indicadas y deja
   un JSON y un HTML por corrida, más un resumen en Markdown.

   Uso:
     node scripts/lighthouse-audit.mjs [--base http://localhost:8000]
                                       [--out docs/qa-visual/lighthouse]
                                       [--only inicio,cruces,qaqc-1]

   Requiere que la aplicación esté sirviendo (por defecto en :8000).
   ========================================================================= */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};

const BASE = arg("base", process.env.A11Y_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const ONLY = arg("only", "").split(",").map((s) => s.trim()).filter(Boolean);
const OUT_DIR = arg("out", "docs/qa-visual/lighthouse");
const REL_OUT = OUT_DIR.replace(/\\/g, "/");
const OUT = path.resolve(ROOT, OUT_DIR);

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean);

const CHROME_PATH = CHROME_CANDIDATES.find((p) => fs.existsSync(p)) || null;
const CHROME_FLAGS = "--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --hide-scrollbars";

const CLI = path.resolve(ROOT, "node_modules", "lighthouse", "cli", "index.js");

const CATEGORIES = "accessibility,performance,best-practices,seo";

/* Una corrida por ruta y factor de forma. `preset` vacío = móvil (por defecto
   de Lighthouse: Moto G Power emulado, CPU 4x, red 4G lenta). */
const RUNS = [
  { slug: "inicio-movil", path: "/", form: "mobile" },
  { slug: "cruces-movil", path: "/cruces", form: "mobile" },
  { slug: "qaqc-1-movil", path: "/qaqc/1", form: "mobile" },
  { slug: "inicio-escritorio", path: "/", form: "desktop", preset: "desktop" },
];

const runs = ONLY.length ? RUNS.filter((r) => ONLY.includes(r.slug) || ONLY.includes(r.path)) : RUNS;

function ejecutar(run) {
  const target = `${BASE}${run.path}`;
  const outputPath = path.join(OUT, run.slug);
  const args = [
    CLI,
    target,
    "--output=json",
    "--output=html",
    `--output-path=${outputPath}`,
    `--only-categories=${CATEGORIES}`,
    `--chrome-flags=${CHROME_FLAGS}`,
    "--max-wait-for-load=90000",
    "--quiet",
  ];
  if (CHROME_PATH) args.push(`--chrome-path=${CHROME_PATH}`);
  if (run.preset) args.push(`--preset=${run.preset}`);

  const started = Date.now();
  const res = spawnSync(process.execPath, args, {
    cwd: ROOT,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, CHROME_PATH: CHROME_PATH || "" },
  });
  const seconds = Math.round((Date.now() - started) / 1000);
  return { status: res.status, stdout: res.stdout || "", stderr: res.stderr || "", seconds };
}

const AUDIT_MODE_IGNORADA = new Set(["informative", "manual", "notApplicable"]);

function resumir(slug, run, json, meta) {
  const cats = {};
  for (const [k, v] of Object.entries(json.categories || {})) {
    cats[k] = v.score === null ? null : Math.round(v.score * 100);
  }

  const metricas = {};
  for (const id of ["first-contentful-paint", "largest-contentful-paint", "total-blocking-time", "cumulative-layout-shift", "speed-index"]) {
    const a = json.audits?.[id];
    if (a) metricas[id] = { display: a.displayValue ?? null, score: a.score };
  }

  /* Audits no perfectos de la categoría de accesibilidad: son los que
     explicarían un puntaje < 100. */
  const a11yRefs = json.categories?.accessibility?.auditRefs || [];
  const fallosA11y = [];
  for (const ref of a11yRefs) {
    const a = json.audits[ref.id];
    if (!a || a.score === null) continue;
    if (AUDIT_MODE_IGNORADA.has(a.scoreDisplayMode)) continue;
    if (a.score === 1) continue;
    fallosA11y.push({
      id: ref.id,
      weight: ref.weight,
      score: a.score,
      mode: a.scoreDisplayMode,
      title: a.title,
      description: a.description,
      items: (a.details?.items || []).slice(0, 10).map((it) => ({
        node: it.node?.selector ?? it.node?.snippet ?? null,
        explanation: it.node?.explanation ?? null,
      })),
      nodes: a.details?.items?.length ?? 0,
    });
  }

  return {
    slug,
    ruta: run.path,
    forma: run.form,
    preset: run.preset || null,
    url: `${BASE}${run.path}`,
    lighthouseVersion: json.lighthouseVersion,
    chromeVersion: json.environment?.hostUserAgent || null,
    fetchTime: json.fetchTime,
    duracionSegundos: meta.seconds,
    puntajes: cats,
    metricas,
    fallosAccesibilidad: fallosA11y,
    reportes: {
      json: `${REL_OUT}/${slug}.report.json`,
      html: `${REL_OUT}/${slug}.report.html`,
    },
  };
}

function mdTabla(filas) {
  const cab = "| Ruta | Forma | Rendimiento | Accesibilidad | Buenas prácticas | SEO |";
  const sep = "|---|---|---|---|---|---|";
  const cuerpos = filas.map(
    (r) =>
      `| \`${r.ruta}\` | ${r.forma === "desktop" ? "escritorio" : "móvil"} | ${r.puntajes.performance} | ${r.puntajes.accessibility} | ${r.puntajes["best-practices"]} | ${r.puntajes.seo} |`,
  );
  return [cab, sep, ...cuerpos].join("\n");
}

fs.mkdirSync(OUT, { recursive: true });

if (!CHROME_PATH) {
  console.error("[lighthouse] No se encontró Chrome. Define CHROME_PATH.");
  process.exit(2);
}
if (!fs.existsSync(CLI)) {
  console.error(`[lighthouse] No se encontró el CLI en ${CLI}. Ejecuta npm install.`);
  process.exit(2);
}

console.log(`[lighthouse] base=${BASE} chrome=${CHROME_PATH}`);
const resultados = [];

for (const run of runs) {
  const target = `${BASE}${run.path}`;
  console.log(`\n[lighthouse] ${run.slug} -> ${target} (${run.form})`);
  const meta = ejecutar(run);
  const jsonPath = path.join(OUT, `${run.slug}.report.json`);

  if (meta.status !== 0 || !fs.existsSync(jsonPath)) {
    console.error(`[lighthouse] FALLÓ ${run.slug} (exit ${meta.status})`);
    console.error(meta.stderr.slice(-4000) || meta.stdout.slice(-4000));
    resultados.push({ slug: run.slug, ruta: run.path, forma: run.form, error: meta.stderr.trim() || `exit ${meta.status}` });
    continue;
  }

  const json = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
  const resumen = resumir(run.slug, run, json, meta);
  resultados.push(resumen);
  console.log(
    `[lighthouse] ${run.slug}: a11y=${resumen.puntajes.accessibility} perf=${resumen.puntajes.performance} bp=${resumen.puntajes["best-practices"]} seo=${resumen.puntajes.seo}`,
  );
}

const ok = resultados.filter((r) => !r.error);
const resumenPath = path.join(OUT, "resumen.json");
fs.writeFileSync(resumenPath, JSON.stringify({ base: BASE, chrome: CHROME_PATH, corridas: resultados }, null, 2));

const lineas = [];
lineas.push("# Lighthouse — Validador EET (AEP)");
lineas.push("");
lineas.push(`**Base:** ${BASE}  `);
lineas.push(`**Chrome:** \`${CHROME_PATH}\` (headless=new, --no-sandbox)  `);
lineas.push(`**Lighthouse:** ${ok[0]?.lighthouseVersion || "n/d"}  `);
lineas.push(`**Fecha de ejecución:** ${ok[0]?.fetchTime || new Date().toISOString()}`);
lineas.push("");
lineas.push("Categorías solicitadas: `accessibility`, `performance`, `best-practices`, `seo`.");
lineas.push("Las corridas «móvil» usan la emulación por defecto de Lighthouse (Moto G Power, CPU 4x,");
lineas.push("red 4G lenta); «escritorio» usa `--preset=desktop`.");
lineas.push("");
lineas.push("## Puntajes");
lineas.push("");
lineas.push(mdTabla(ok));
lineas.push("");
lineas.push("## Métricas de laboratorio");
lineas.push("");
lineas.push("| Ruta | Forma | FCP | LCP | TBT | CLS | Speed Index |");
lineas.push("|---|---|---|---|---|---|---|");
for (const r of ok) {
  const m = r.metricas;
  lineas.push(
    `| \`${r.ruta}\` | ${r.forma === "desktop" ? "escritorio" : "móvil"} | ${m["first-contentful-paint"]?.display ?? "—"} | ${m["largest-contentful-paint"]?.display ?? "—"} | ${m["total-blocking-time"]?.display ?? "—"} | ${m["cumulative-layout-shift"]?.display ?? "—"} | ${m["speed-index"]?.display ?? "—"} |`,
  );
}
lineas.push("");
lineas.push("## Audits de accesibilidad no perfectos");
lineas.push("");
const conFallos = ok.filter((r) => r.fallosAccesibilidad.length);
if (!conFallos.length) {
  lineas.push("Ninguno: las cuatro corridas obtienen **100** en `accessibility` (todos los audits");
  lineas.push("ponderados pasan).");
} else {
  for (const r of conFallos) {
    lineas.push(`### \`${r.ruta}\` (${r.forma})`);
    lineas.push("");
    lineas.push("| Audit | Peso | Puntaje | Nodos | Título |");
    lineas.push("|---|---|---|---|---|");
    for (const f of r.fallosAccesibilidad) {
      lineas.push(`| \`${f.id}\` | ${f.weight} | ${f.score} | ${f.nodes} | ${f.title} |`);
    }
    lineas.push("");
  }
}
lineas.push("## Artefactos");
lineas.push("");
for (const r of ok) {
  lineas.push(`- \`${r.slug}\`: [JSON](${path.basename(r.reportes.json)}) · [HTML](${path.basename(r.reportes.html)})`);
}
const conError = resultados.filter((r) => r.error);
if (conError.length) {
  lineas.push("");
  lineas.push("## Corridas fallidas");
  lineas.push("");
  for (const r of conError) {
    lineas.push(`- \`${r.ruta}\` (${r.forma}): ${r.error.split("\n")[0]}`);
  }
}
lineas.push("");

fs.writeFileSync(path.join(OUT, "resumen.md"), lineas.join("\n"));
console.log(`\n[lighthouse] Resumen en ${path.relative(ROOT, path.join(OUT, "resumen.md"))}`);
process.exit(conError.length ? 1 : 0);
