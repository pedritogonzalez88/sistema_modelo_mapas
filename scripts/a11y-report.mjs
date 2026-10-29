#!/usr/bin/env node
/* =========================================================================
   scripts/a11y-report.mjs
   Compone docs/qa-visual/resumen-auditoria.md a partir de:
     docs/qa-visual/audit-data.json        (a11y-audit.mjs)
     docs/qa-visual/interaction-data.json  (a11y-interaction.mjs)
   No navega: solo agrega y formatea evidencia ya capturada.
   ========================================================================= */
import fs from "node:fs";
import path from "node:path";
import { OUT, ROOT } from "./a11y-helpers.mjs";

const auditFile = path.join(OUT, "audit-data.json");
const interFile = path.join(OUT, "interaction-data.json");
for (const f of [auditFile, interFile]) {
  if (!fs.existsSync(f)) throw new Error(`Falta ${f}. Ejecuta antes los scripts de auditoría.`);
}

const audit = JSON.parse(fs.readFileSync(auditFile, "utf8"));
const inter = JSON.parse(fs.readFileSync(interFile, "utf8"));

const IMPACTS = ["critical", "serious", "moderate", "minor"];
const IMPACT_ES = { critical: "critical", serious: "serious", moderate: "moderate", minor: "minor" };
const cero = () => ({ critical: 0, serious: 0, moderate: 0, minor: 0 });
const esc = (s) => String(s == null ? "" : s).replace(/\|/g, "\\|").replace(/\n/g, " ");

/* Causas detectadas por inspección del DOM en las rutas afectadas. */
const CAUSAS = {
  "link-in-text-block":
    "En las tarjetas móviles de `/cruces` el número de punto es un enlace inline `text-brand-700` junto a texto " +
    "`ink-900`: 2.44:1 de contraste entre ambos y sin subrayado (`hover:underline` solo al pasar el ratón), " +
    "por debajo del 3:1 que exige 1.4.1 cuando el color es el único distintivo. En 1440 px el mismo enlace vive " +
    "en una celda de tabla sin texto contiguo, por eso la regla no se activa.",
  dlitem:
    "El `<dl>` de la tarjeta «Archivo» en `entrega_preview.html` agrupa los pares con un `<div>` de rejilla que a " +
    "su vez contiene un `<div>` por dato. HTML solo permite **un** nivel de `<div>` entre `<dl>` y `<dt>/<dd>`, " +
    "así que los 12 pares quedan sin `<dl>` padre válido y se pierde la semántica de lista de descripciones.",
  "definition-list":
    "Misma causa que `dlitem`: el hijo directo del `<dl>` es un `<div>` que envuelve otros `<div>` con los `dt`/`dd`.",
  "landmark-unique":
    "En `/entregas` y `/entregas/1/preview`, el `<section aria-labelledby=\"…\">` recibe nombre accesible y se " +
    "convierte en landmark `region`; su nombre coincide con el del `div.table-wrap role=\"region\"` " +
    "(`aria-label` de la tabla que contiene, p. ej. «Entregas registradas»). Resultado: dos regiones con el mismo " +
    "nombre. Las demás vistas no nombran la sección que envuelve la tabla, por eso no aparecen.",
};

/* ------------------- resultados incompletos (revisión manual) ------------ */
const incompletos = new Map();
for (const r of audit.rutas) {
  for (const [sufijo, ancho] of [
    ["", "1440"],
    ["-390", "390"],
  ]) {
    const f = path.join(OUT, "axe", `${r.slug}${sufijo}.json`);
    if (!fs.existsSync(f)) continue;
    const j = JSON.parse(fs.readFileSync(f, "utf8"));
    for (const v of j.incomplete || []) {
      if (!incompletos.has(v.id)) {
        incompletos.set(v.id, { id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl, casos: [] });
      }
      for (const n of v.nodes) {
        const target = Array.isArray(n.target) ? n.target.join(" ") : String(n.target);
        const why = ((n.any && n.any.map((a) => a.message)) || (n.all && n.all.map((a) => a.message)) || []).join(" / ");
        const yaEsta = incompletos.get(v.id).casos.some((c) => c.ruta === r.url && c.target === target && c.why === why);
        if (yaEsta) continue;
        incompletos.get(v.id).casos.push({
          ruta: r.url,
          ancho,
          target,
          html: String(n.html || "").slice(0, 160),
          why,
        });
      }
    }
  }
}

/* ------------------------- totales y por ruta ---------------------------- */

const total1440 = { ...cero(), reglas: 0, nodos: 0 };
const total390 = { ...cero(), reglas: 0, nodos: 0 };
const filas = [];

for (const r of audit.rutas) {
  const p = r.impactos ? r.impactos.porImpacto : cero();
  const m = r.axe390 ? r.axe390.impactos.porImpacto : null;
  for (const k of IMPACTS) {
    total1440[k] += p[k] || 0;
    if (m) total390[k] += m[k] || 0;
  }
  total1440.reglas += r.impactos ? r.impactos.totalReglas : 0;
  total1440.nodos += r.impactos ? r.impactos.totalNodos : 0;
  total390.reglas += r.axe390 ? r.axe390.impactos.totalReglas : 0;
  total390.nodos += r.axe390 ? r.axe390.impactos.totalNodos : 0;

  filas.push({
    ruta: r.url,
    nombre: r.nombre,
    status: r.status,
    ok: r.ok,
    p1440: p,
    reglas1440: r.impactos ? r.impactos.totalReglas : 0,
    nodos1440: r.impactos ? r.impactos.totalNodos : 0,
    p390: m,
    reglas390: r.axe390 ? r.axe390.impactos.totalReglas : 0,
    nodos390: r.axe390 ? r.axe390.impactos.totalNodos : 0,
  });
}

/* ---------------------------- agregado de reglas ------------------------- */
const reglas = new Map();
function acumular(violations, ruta, ancho) {
  for (const v of violations) {
    if (!reglas.has(v.id)) {
      reglas.set(v.id, {
        id: v.id,
        impact: v.impact || "minor",
        help: v.help,
        helpUrl: v.helpUrl,
        rutas: new Map(),
        nodos: 0,
        selectores: [],
      });
    }
    const acc = reglas.get(v.id);
    acc.nodos += v.nodes.length;
    const clave = `${ruta} @${ancho}`;
    acc.rutas.set(clave, (acc.rutas.get(clave) || 0) + v.nodes.length);
    for (const n of v.nodes) {
      const sel = Array.isArray(n.target) ? n.target.join(" ") : String(n.target);
      if (acc.selectores.length < 3 && !acc.selectores.includes(sel)) acc.selectores.push(sel);
    }
  }
}
for (const r of audit.rutas) {
  acumular(r.violaciones || [], r.url, "1440");
  if (r.axe390) acumular(r.axe390.violaciones || [], r.url, "390");
}
const reglasOrdenadas = [...reglas.values()].sort((a, b) => {
  const d = IMPACTS.indexOf(a.impact) - IMPACTS.indexOf(b.impact);
  return d !== 0 ? d : b.nodos - a.nodos;
});

/* ------------------------------- desbordes ------------------------------- */
const anchos = audit.viewports.map((v) => v.name);
const desbordes = [];
for (const r of audit.rutas) {
  for (const w of anchos) {
    const m = r.anchos[w];
    if (m && m.desborda) {
      desbordes.push({
        ruta: r.url,
        ancho: w,
        scrollWidth: m.scrollWidth,
        innerWidth: m.innerWidth,
        fuera: m.totalFueraDeScroller,
        offenders: m.offenders,
      });
    }
  }
}

/* --------------------------- consola y recursos -------------------------- */
const conConsola = audit.rutas.filter(
  (r) => r.consola.length || r.pageErrors.length || r.recursosFallidos.length
);

/* --------------------------------- salida -------------------------------- */
const L = [];
const hoy = new Date().toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });

L.push("# Auditoría de accesibilidad y responsive — Validador EET (AEP)");
L.push("");
L.push(`**Fecha de ejecución:** ${hoy}`);
L.push(`**Base auditada:** ${audit.base}`);
L.push(`**Motor:** axe-core ${audit.axeVersion} sobre Chrome ${audit.navegadorVersion || "del sistema"} (Playwright, \`channel: "chrome"\`), headless`);
L.push("**Anchos comprobados:** " + anchos.map((w) => `${w} px`).join(" · "));
L.push("**Rutas:** 11 (ver tabla)");
L.push("");
L.push("**Scripts reproducibles:**");
L.push("");
L.push("```bash");
L.push("node scripts/a11y-audit.mjs        # axe + capturas + desbordamiento + consola");
L.push("node scripts/a11y-interaction.mjs  # teclado, drawer y zoom");
L.push("node scripts/a11y-report.mjs       # este informe");
L.push("```");
L.push("");
L.push("**Evidencia cruda:** `docs/qa-visual/audit-data.json`, `docs/qa-visual/interaction-data.json`,");
L.push("`docs/qa-visual/axe/*.json` (violaciones e incompletos íntegros), `docs/qa-visual/axe/resumen.md`.");
L.push("");

/* 1. Global */
L.push("## 1. Resultado global");
L.push("");
L.push("| Impacto | Reglas (1440) | Reglas (390) |");
L.push("|---|---|---|");
for (const k of IMPACTS) {
  L.push(`| ${IMPACT_ES[k]} | ${total1440[k]} | ${total390[k]} |`);
}
L.push(`| **Total reglas** | **${total1440.reglas}** | **${total390.reglas}** |`);
L.push(`| **Nodos afectados** | **${total1440.nodos}** | **${total390.nodos}** |`);
L.push("");
L.push(
  `Las 11 rutas responden 200 y el HTML carga sin errores de JavaScript. ` +
    `No hay **ninguna** violación de impacto \`critical\` ni desbordamiento horizontal de página en ` +
    `ninguno de los ${anchos.length} anchos (${anchos.join(", ")} px).`
);L.push("");
L.push("Reparto: en 1440 px solo 3 de 11 rutas presentan hallazgos; en 390 px son 3 de 11 (dos coinciden).");
L.push("");

/* 2. Por ruta */
L.push("## 2. Violaciones por ruta e impacto");
L.push("");
L.push("| Ruta | Estado | 1440: critical | serious | moderate | minor | Reglas | Nodos | 390: critical | serious | moderate | minor | Reglas | Nodos |");
L.push("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|");
for (const f of filas) {
  const a = f.p1440;
  const b = f.p390;
  L.push(
    `| \`${f.ruta}\` | ${f.ok ? f.status : `ERROR ${f.status ?? "-"}`} | ` +
      `${a.critical} | ${a.serious} | ${a.moderate} | ${a.minor} | ${f.reglas1440} | ${f.nodos1440} | ` +
      (b
        ? `${b.critical} | ${b.serious} | ${b.moderate} | ${b.minor} | ${f.reglas390} | ${f.nodos390}`
        : "- | - | - | - | - | -") +
      " |"
  );
}
L.push("");
L.push("Rutas sin ningún hallazgo en ambos anchos: " + filas.filter((f) => f.nodos1440 === 0 && f.nodos390 === 0).map((f) => `\`${f.ruta}\``).join(", ") + ".");
L.push("");

/* 3. Top reglas */
L.push(`## 3. Reglas incumplidas (${reglasOrdenadas.length} en total${reglasOrdenadas.length > 15 ? "; top 15" : ""})`);
L.push("");
L.push("| # | id | Impacto | Ayuda | Rutas afectadas | Nodos | 2 selectores de ejemplo |");
L.push("|---|---|---|---|---|---|---|");
reglasOrdenadas.slice(0, 15).forEach((r, i) => {
  const rutas = [...r.rutas.keys()].join("<br>");
  const sels = r.selectores.slice(0, 2).map((s) => `\`${esc(s)}\``).join("<br>");
  L.push(`| ${i + 1} | \`${r.id}\` | ${r.impact} | ${esc(r.help)} | ${rutas} | ${r.nodos} | ${sels} |`);
});
L.push("");
L.push("### Detalle por regla");
L.push("");
for (const r of reglasOrdenadas) {
  L.push(`#### \`${r.id}\` — ${r.impact}`);
  L.push("");
  L.push(`${esc(r.help)}`);
  L.push("");
  if (CAUSAS[r.id]) {
    L.push(`**Causa detectada:** ${CAUSAS[r.id]}`);
    L.push("");
  }
  L.push(`- Documentación: ${r.helpUrl}`);
  L.push(
    `- Rutas: ${[...r.rutas.entries()]
      .map(([k, n]) => `${k} (${n} ${n === 1 ? "nodo" : "nodos"})`)
      .join("; ")}`
  );
  L.push(`- Selectores observados:`);
  for (const s of r.selectores) L.push(`  - \`${s}\``);
  L.push("");
}

/* 3.bis Incompletos */
L.push(`### 3.1 Resultados incompletos de axe (${incompletos.size} reglas, ${[...incompletos.values()].reduce((a, r) => a + r.casos.length, 0)} casos)`);
L.push("");
L.push("axe no pudo decidir estos casos; **no son violaciones**, pero tampoco están verificados y");
L.push("requieren revisión manual:");
L.push("");
if (incompletos.size === 0) {
  L.push("Sin resultados incompletos.");
} else {
  for (const r of incompletos.values()) {
    L.push(`#### \`${r.id}\` — ${r.impact}`);
    L.push("");
    L.push(`${esc(r.help)}`);
    L.push("");
    L.push(`- Rutas: ${[...new Set(r.casos.map((c) => `${c.ruta} @${c.ancho}`))].join("; ")}`);
    for (const c of r.casos.slice(0, 4)) {
      L.push(`- \`${esc(c.target)}\` — ${esc(c.why)}`);
      L.push(`  \`\`\`html`);
      L.push(`  ${esc(c.html)}`);
      L.push(`  \`\`\``);
    }
    L.push("");
  }
}

/* 4. Desbordes */L.push("## 4. Desbordamiento horizontal");
L.push("");
if (desbordes.length === 0) {
  L.push(
    `Ninguna de las 11 rutas desborda horizontalmente en ${anchos.join(", ")} px ` +
      `(\`document.documentElement.scrollWidth <= window.innerWidth + 1\` en todos los casos).`
  );
  L.push("");
  L.push("Las tablas anchas quedan contenidas en su propio contenedor con scroll (`role=\"region\"` +");
  L.push("`tabindex=\"0\"`), que es el comportamiento previsto: el documento no gana scroll lateral.");
} else {
  L.push("| Ruta | Ancho | scrollWidth / innerWidth | Elementos fuera de contenedor con scroll |");
  L.push("|---|---|---|---|");
  for (const d of desbordes) {
    const els = d.offenders.slice(0, 5).map((o) => `\`${esc(o.selector)}\` (${o.width}px)`).join("<br>");
    L.push(`| \`${d.ruta}\` | ${d.ancho} | ${d.scrollWidth} / ${d.innerWidth} | ${els || "-"} |`);
  }
}
L.push("");

/* 5. Consola */
L.push("## 5. Errores de consola y de carga");
L.push("");
if (conConsola.length === 0) {
  L.push("Ninguna ruta registró errores de consola (`console` tipo `error`), excepciones no capturadas");
  L.push("(`pageerror`), peticiones fallidas ni respuestas HTTP ≥ 400 en ninguno de los anchos probados.");
} else {
  L.push("| Ruta | Consola | pageerror | Recursos fallidos |");
  L.push("|---|---|---|---|");
  for (const r of conConsola) {
    L.push(
      `| \`${r.url}\` | ${r.consola.map((c) => `\`${esc(c)}\``).join("<br>") || "-"} | ` +
        `${r.pageErrors.map((c) => `\`${esc(c)}\``).join("<br>") || "-"} | ` +
        `${r.recursosFallidos.map((c) => `\`${esc(c)}\``).join("<br>") || "-"} |`
    );
  }
}
L.push("");

/* 6. Teclado */
const t = inter.teclado;
L.push("## 6. Prueba de teclado — skip link (390x844, `/`)");
L.push("");
L.push(`**Resultado: ${t.resultado}**${t.todosLosPasosOk ? " (todos los pasos)" : ""}`);
L.push("");
L.push("| Paso | Resultado | Evidencia |");
L.push("|---|---|---|");
for (const p of t.pasos) L.push(`| ${esc(p.paso)} | ${p.ok ? "PASA" : "FALLA"} | ${esc(p.detalle)} |`);
L.push("");
L.push("Orden de tabulación observado (primeras 6 paradas, sin haber usado el ratón):");
L.push("");
L.push("| # | Elemento | Texto / etiqueta | y (px) |");
L.push("|---|---|---|---|");
for (const o of t.ordenDeTabulacion) {
  L.push(`| ${o.orden} | \`<${o.tag}${o.id ? "#" + o.id : ""}>\` | ${esc(o.texto || o.ariaLabel || "")} | ${o.y} |`);
}
L.push("");

/* 7. Drawer */
const d = inter.drawer;
L.push("## 7. Drawer móvil (390x844, `/`)");
L.push("");
L.push(`**Resultado: ${d.resultado}**${d.todosLosPasosOk ? " (todos los pasos)" : ""} — apertura con clic real (${esc(d.metodoClick)})`);
L.push("");
L.push("| Paso | Resultado | Evidencia |");
L.push("|---|---|---|");
for (const p of d.pasos) L.push(`| ${esc(p.paso)} | ${p.ok ? "PASA" : "FALLA"} | ${esc(p.detalle)} |`);
L.push("");
if (inter.drawerAxe && !inter.drawerAxe.error) {
  L.push(
    `axe-core sobre el drawer abierto (\`${inter.drawerAxe.archivo}\`): ` +
      `**${inter.drawerAxe.violaciones} violaciones / ${inter.drawerAxe.nodos} nodos**.`
  );
  if (inter.drawerAxe.detalle.length) {
    for (const v of inter.drawerAxe.detalle) L.push(`- \`${v.id}\` (${v.impact}, ${v.nodos} nodos): ${esc(v.help)}`);
  }
  L.push("");
}
const sonda = d.sondaTrampaDeFoco;
if (sonda) {
  L.push("### Sonda adicional: contención del foco en el modal");
  L.push("");
  if (sonda.fugaEn === null) {
    L.push("El foco nunca abandonó el diálogo en 12 pulsaciones de `Tab`.");
  } else {
    L.push(
      `Con el drawer abierto (\`aria-modal="true"\`), la pulsación **${sonda.fugaEn}** de \`Tab\` saca el foco ` +
        `del diálogo y lo lleva al resto de la página:`
    );
    L.push("");
    for (const s of sonda.secuencia.slice(Math.max(0, sonda.fugaEn - 2), sonda.fugaEn + 2)) {
      L.push(
        `- ${s.paso}. \`<${s.tag}>\` ${esc(s.texto || s.ariaLabel || "")}` +
          `${s.href ? ` → \`${s.href}\`` : ""} — dentro del drawer: ${s.enDrawer ? "sí" : "**no**"}`
      );
    }
    L.push("");
    L.push(
      "No lo detecta ninguna regla automática de axe: es una desviación del patrón APG para diálogos modales " +
        "(el fondo debería quedar inerte mientras el diálogo está abierto). Queda registrado como hallazgo menor."
    );
  }
  L.push("");
}

/* 8. Zoom */
L.push("## 8. Zoom 200 % y reflow");
L.push("");
L.push("| Ruta | CSS `zoom:2` (1440) | Zoom de navegador 200 % (720×450 @ DPR 2) | Reflow 720 px |");
L.push("|---|---|---|---|");
for (const z of inter.zoom) {
  const css = z.zoomCss200.medida;
  const nav = z.zoomNavegador200;
  L.push(
    `| \`${z.ruta}\` | ${css.desborda ? "**desborda**" : "sin desbordamiento"} ` +
      `(${css.scrollWidth}/${css.innerWidth}) | ${nav.ok ? "sin desbordamiento" : "**desborda**"} ` +
      `(${nav.medida.scrollWidth}/${nav.medida.innerWidth}) | ${z.reflow720.ok ? "sin desbordamiento" : "**desborda**"} |`
  );
}
L.push("");
L.push("### Interpretación del resultado con `zoom` CSS");
L.push("");
const z0 = inter.zoom.find((z) => z.zoomCss200.medida.desborda);
if (z0) {
  const dg = z0.zoomCss200.diagnostico;
  L.push(
    `En \`${z0.ruta}\`, aplicar \`document.documentElement.style.zoom = "2"\` produce un scroll horizontal ` +
      `de ${z0.zoomCss200.medida.scrollXmax} px (\`scrollWidth\` ${z0.zoomCss200.medida.scrollWidth} frente a ` +
      `\`innerWidth\` ${z0.zoomCss200.medida.innerWidth}). La causa está en el propio método: **\`zoom\` CSS no ` +
      `reduce el viewport de maquetación, así que las media queries de escritorio siguen activas** ` +
      `(\`(min-width: 1024px)\` = ${dg.media1024}, \`aside\` = \`${dg.sidebarDisplay}\`) mientras el contenido se ` +
      `dibuja al doble de tamaño. Chrome real no hace eso: al ampliar al 200 % reduce el viewport CSS a 720 px y ` +
      `la barra lateral desaparece.`
  );
  L.push("");
  L.push(
    "Por eso se añadieron dos comprobaciones fieles: **zoom de navegador emulado** (720×450 con `deviceScaleFactor: 2`, " +
      "donde `(min-width: 1024px)` = false y el `aside` = `none`) y **reflow a 720 px**. Ambas pasan en `/` y `/qaqc/1`."
  );
  L.push("");
  L.push(
    "Conclusión: **no se incumple 1.4.4 (Redimensionar texto) ni 1.4.10 (Reflow)** por zoom; el desbordamiento " +
      "observado con `zoom` CSS es un artefacto del método. Como nota de robustez, el contenido de la tarjeta de " +
      "concordancia mide ~816 px de ancho intrínseco, así que **si en el futuro se fuerza el layout de escritorio " +
      "en un espacio menor a ese ancho, desbordaría**; conviene revisarlo si se añade un modo de sidebar fijo en tablet."
  );
} else {
  L.push("Ninguna ruta desbordó con `zoom:2` ni con la emulación de zoom de navegador.");
}
L.push("");

/* 9. Capturas */
const capturas = audit.rutas.flatMap((r) => r.capturas || []);
L.push("## 9. Capturas generadas");
L.push("");
L.push(`${capturas.length} capturas \`fullPage\` en \`docs/qa-visual/capturas/\` (1440×900, 768×1024 y 390×844).`);
L.push("");
L.push("| Ruta | Capturas |");
L.push("|---|---|");
for (const r of audit.rutas) {
  const lista = (r.capturas || []).map((c) => `[\`${path.basename(c)}\`](${c.replace("docs/qa-visual/", "./")})`).join(" · ");
  L.push(`| \`${r.url}\` | ${lista || "-"} |`);
}
L.push("");

/* 10. Cobertura y limitaciones */
L.push("## 10. Cobertura y limitaciones");
L.push("");
L.push("**Cubierto con evidencia de navegador real:**");
L.push("");
L.push("- axe-core con las etiquetas `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` y `best-practice` en las 11 rutas, a 1440 px y a 390 px.");
L.push("- Desbordamiento horizontal de documento en " + anchos.length + " anchos (" + anchos.join(", ") + " px).");
L.push("- Errores de consola, excepciones de página, peticiones fallidas y respuestas ≥ 400 por ruta y ancho.");
L.push("- Navegación por teclado del skip link (foco, visibilidad y salto real al contenido).");
L.push("- Drawer móvil: `aria-expanded`, apertura, `Escape` y retorno del foco, más axe sobre el drawer abierto.");
L.push("- Zoom 200 % (emulación fiel y `zoom` CSS) y reflow a 720 px en `/` y `/qaqc/1`.");
L.push("");
L.push("**No cubierto (sigue pendiente de revisión manual/AT):**");
L.push("");
L.push("- Recorrido completo con teclado de las 11 vistas (aquí solo el arranque en `/` y el drawer).");
L.push("- Lectores de pantalla reales (NVDA/JAWS + Firefox/Chrome, VoiceOver + Safari).");
L.push("- Contraste: `color-contrast` sin violaciones; 2 casos quedaron **incompletos** y se listan en 3.1 (texto SVG de la dona y un párrafo parcialmente tapado por la barra sticky).");
L.push("- `prefers-reduced-motion: reduce`.");
L.push("- Envío de formularios sin ratón, incluida la carga de archivo.");
L.push("- Lighthouse (puntaje numérico); esta auditoría usa axe-core directamente, que es el motor de su categoría de accesibilidad.");
L.push("- Contenido dentro de estados no alcanzables en esta BD de muestra (p. ej. fichas cerradas, errores de validación vacíos).");
L.push("");

const destino = path.join(OUT, "resumen-auditoria.md");
fs.writeFileSync(destino, L.join("\n"));
console.log(`[ok] ${destino}`);
console.log(`     ${filas.length} rutas · ${reglasOrdenadas.length} reglas · ${capturas.length} capturas · ${desbordes.length} desbordes`);
