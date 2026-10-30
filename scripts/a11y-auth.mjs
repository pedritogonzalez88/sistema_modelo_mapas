#!/usr/bin/env node
/* =========================================================================
   scripts/a11y-auth.mjs
   Auditoría axe-core de las pantallas de autenticación del Validador EET a
   1440 y 390 px, más comprobaciones de teclado en /auth/login, /auth/mfa y
   /auth/usuarios.

   Requiere la app con EET_AUTH_ENABLED=true en `--base` y un administrador de
   prueba (`e2e-admin`). El secreto TOTP se toma de `--totp-secret`, de
   `EET_E2E_TOTP_SECRET` o del archivo temporal que guarda el E2E; si el
   administrador todavía no está enrolado, el propio script lo enrola y guarda
   el secreto en ese archivo temporal (nunca en el repositorio).

   Uso:
     node scripts/a11y-auth.mjs --base http://127.0.0.1:8033
     node scripts/a11y-auth.mjs --base http://127.0.0.1:8033 --ancho 1440

   Salidas:
     docs/qa-visual/axe-auth/<slug>-<ancho>.json   resultado axe íntegro
     docs/qa-visual/axe-auth/teclado-auth.json     comprobaciones de teclado
     docs/qa-visual/axe-auth/resumen.md            resumen por impacto y regla
     docs/qa-visual/capturas-auth/<slug>-<ancho>.png
   ========================================================================= */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { launchBrowser, ROOT, arg, PAGE_HELPERS } from "./a11y-helpers.mjs";
import { crearGenerador } from "./totp.mjs";

const BASE = (process.env.A11Y_BASE_URL || arg("base", "http://127.0.0.1:8033")).replace(/\/$/, "");
const OUT = path.resolve(ROOT, arg("out", "docs/qa-visual"));
const AXE_DIR = path.join(OUT, "axe-auth");
const SHOT_DIR = path.join(OUT, "capturas-auth");
const AXE_PATH = path.join(ROOT, "node_modules", "axe-core", "axe.min.js");
const SECRETO_FILE = path.join(
  process.env.TEMP || os.tmpdir(),
  "opencode",
  "e2e-totp-secret.txt"
);

const ADMIN = {
  login: "e2e-admin",
  password: process.env.EET_ADMIN_PASSWORD || "ClaveE2EAdmin-2026!",
};

const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];
const IMPACTOS = ["critical", "serious", "moderate", "minor"];
const ANCHOS = [
  { nombre: "1440", width: 1440, height: 900 },
  { nombre: "390", width: 390, height: 844 },
];

const RUTAS_ANONIMAS = [
  { slug: "login", ruta: "/auth/login", nombre: "Inicio de sesión (anónimo)" },
  { slug: "recuperar", ruta: "/auth/recuperar", nombre: "Recuperar acceso (anónimo)" },
  { slug: "restablecer-invalido", ruta: "/auth/restablecer?token=invalido", nombre: "Enlace de restablecimiento inválido" },
];

const RUTAS_AUTENTICADAS = [
  { slug: "seguridad", ruta: "/auth/seguridad", nombre: "Seguridad (segundo factor y sesiones)" },
  { slug: "usuarios", ruta: "/auth/usuarios", nombre: "Usuarios (administración)" },
  { slug: "auditoria", ruta: "/auth/auditoria", nombre: "Auditoría (eventos y CSV)" },
];

fs.mkdirSync(AXE_DIR, { recursive: true });
fs.mkdirSync(SHOT_DIR, { recursive: true });

const log = (...a) => console.log("[a11y-auth]", ...a);
const soloAncho = arg("ancho", null);
const ANCHOS_EFECTIVOS = soloAncho ? ANCHOS.filter((a) => a.nombre === String(soloAncho)) : ANCHOS;

const registros = [];
const teclado = [];
let generador = null;
let secreto = process.env.EET_E2E_TOTP_SECRET || arg("totp-secret", null);

function leerSecretoGuardado() {
  if (secreto) return secreto;
  try {
    const guardado = fs.readFileSync(SECRETO_FILE, "utf8").replace(/[^A-Za-z2-7]/g, "").toUpperCase();
    if (guardado.length >= 16) {
      secreto = guardado;
      return secreto;
    }
  } catch {
    /* sin archivo: se enrolará */
  }
  return null;
}

function guardarSecreto(valor) {
  secreto = valor;
  try {
    fs.mkdirSync(path.dirname(SECRETO_FILE), { recursive: true });
    fs.writeFileSync(SECRETO_FILE, valor, "utf8");
  } catch (err) {
    log(`aviso: no se pudo guardar el secreto TOTP temporal (${err.message})`);
  }
}

/* --- Utilidades de página ------------------------------------------------- */
async function goto(page, ruta) {
  for (const waitUntil of ["networkidle", "load"]) {
    try {
      const resp = await page.goto(`${BASE}${ruta}`, { waitUntil, timeout: 45000 });
      await page.waitForTimeout(250);
      return resp;
    } catch {
      /* segundo intento */
    }
  }
  return null;
}

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

function contar(violations) {
  const porImpacto = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  let nodos = 0;
  for (const v of violations) {
    porImpacto[v.impact || "minor"] += 1;
    nodos += v.nodes.length;
  }
  return { porImpacto, reglas: violations.length, nodos };
}

async function auditar(page, { slug, ruta, nombre }, ancho) {
  const resp = await goto(page, ruta);
  const status = resp ? resp.status() : 0;
  const url = page.url();
  const results = await correrAxe(page);
  const resumen = contar(results.violations);
  await page.screenshot({
    path: path.join(SHOT_DIR, `${slug}-${ancho.nombre}.png`),
    fullPage: true,
  });

  const registro = {
    slug,
    nombre,
    ruta,
    url,
    status,
    ancho: ancho.nombre,
    porImpacto: resumen.porImpacto,
    reglas: resumen.reglas,
    nodos: resumen.nodos,
    violaciones: results.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      criterios: v.tags.filter((t) => t.startsWith("wcag")),
      nodos: v.nodes.length,
      ejemplos: v.nodes.slice(0, 3).map((n) => ({ target: n.target, html: String(n.html).slice(0, 300) })),
    })),
    incompletos: results.incomplete.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodos: v.nodes.length,
      ejemplos: v.nodes.slice(0, 2).map((n) => ({ target: n.target, html: String(n.html).slice(0, 200) })),
    })),
    captura: `docs/qa-visual/capturas-auth/${slug}-${ancho.nombre}.png`,
  };
  fs.writeFileSync(
    path.join(AXE_DIR, `${slug}-${ancho.nombre}.json`),
    JSON.stringify({ ...registro, crudo: results }, null, 2),
    "utf8"
  );
  registros.push(registro);
  log(
    `${slug} @${ancho.nombre}: ${status} — ${resumen.reglas} reglas / ${resumen.nodos} nodos` +
      (resumen.reglas ? ` (${JSON.stringify(resumen.porImpacto)})` : "")
  );
  return registro;
}

/* --- Teclado -------------------------------------------------------------- */
async function probarTeclado(page, etiqueta) {
  const foco = () =>
    page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return { tag: "body" };
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        href: el.getAttribute ? el.getAttribute("href") : null,
        texto: (el.textContent || "").trim().slice(0, 40),
        visible: r.width > 2 && r.height > 2 && cs.visibility !== "hidden" && cs.display !== "none",
        anillo: Boolean(
          (cs.outlineStyle && cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth || "0") > 0) ||
            (cs.boxShadow && cs.boxShadow !== "none")
        ),
      };
    });

  const r = {
    pantalla: etiqueta,
    url: new URL(page.url()).pathname,
    focoInicial: null,
    primerTab: null,
    skipLink: null,
    paradas: 0,
    trampa: false,
    ocultos: [],
    envio: null,
  };

  r.focoInicial = await foco();
  await page.keyboard.press("Tab");
  r.primerTab = await foco();

  // El skip link debe ser alcanzable con Shift+Tab y hacerse visible al
  // enfocarlo (en el login el campo de usuario lleva autofocus, así que no es
  // el destino del primer Tab).
  for (let i = 0; i < 3; i++) {
    const actual = await foco();
    if (actual.href === "#contenido") break;
    await page.keyboard.press("Shift+Tab");
  }
  const tras = await page.evaluate(() => {
    const el = document.activeElement;
    return el ? { href: el.getAttribute && el.getAttribute("href") } : null;
  });
  const focoSkip = await foco();
  r.skipLink = {
    alcanzado: Boolean(tras && tras.href === "#contenido"),
    visible: Boolean(focoSkip.visible),
  };

  const vistos = [];
  let repetidas = 0;
  let anterior = JSON.stringify(await foco());
  for (let i = 0; i < 24; i++) {
    await page.keyboard.press("Tab");
    const actual = await foco();
    const clave = JSON.stringify(actual);
    if (clave === anterior) repetidas += 1;
    else repetidas = 0;
    if (repetidas >= 3) r.trampa = true;
    if (actual.visible === false) r.ocultos.push(actual);
    vistos.push(clave);
    anterior = clave;
  }
  r.paradas = new Set(vistos).size;

  // Enter sobre el botón de envío: se captura el submit y se cancela, así no
  // se envía nada ni se modifica la base. El escucha se retira tras la primera
  // captura para no bloquear los envíos reales de los pasos siguientes.
  await page.evaluate(() => {
    window.__submitAuth = null;
    window.__submitAuthHandler = (ev) => {
      window.__submitAuth = {
        accion: ev.target.getAttribute("action") || "(por defecto)",
        metodo: (ev.target.getAttribute("method") || "get").toLowerCase(),
        disparador: ev.submitter ? (ev.submitter.textContent || "").trim().slice(0, 40) : null,
      };
      ev.preventDefault();
      ev.stopPropagation();
      document.removeEventListener("submit", window.__submitAuthHandler, true);
    };
    document.addEventListener("submit", window.__submitAuthHandler, true);
  });
  const boton = await page.evaluate(() => {
    // Los campos obligatorios vacíos se rellenan con datos de prueba para que
    // el formulario sea válido: el envío se cancela más abajo, así que no se
    // envía nada ni se modifica la base.
    for (const el of Array.from(document.querySelectorAll("input, select, textarea"))) {
      if (!el.required) continue;
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox" || type === "radio") {
        if (!el.checked && type === "checkbox") el.click();
        continue;
      }
      if (el.tagName === "SELECT") {
        if (!el.value && el.options.length > 1) el.selectedIndex = 1;
        continue;
      }
      if (!el.value) {
        el.value =
          type === "email" ? "prueba@ejemplo.org"
          : type === "password" ? "PruebaE2e-2026!"
          : type === "number" ? "1"
          : "prueba";
      }
    }
    for (const form of Array.from(document.querySelectorAll("form"))) {
      const b = form.querySelector('button[type="submit"]:not([disabled]), input[type="submit"]:not([disabled])');
      if (!b) continue;
      if (b.checkVisibility && !b.checkVisibility()) continue;
      const style = getComputedStyle(b);
      if (style.display === "none" || style.visibility === "hidden") continue;
      if (!form.checkValidity()) continue;
      b.focus();
      return (b.textContent || b.value || "").trim().replace(/\s+/g, " ").slice(0, 40);
    }
    return null;
  });
  if (boton) {
    await page.keyboard.press("Enter");
    await page.waitForTimeout(150);
    const capturado = await page.evaluate(() => window.__submitAuth);
    r.envio = { boton, disparado: Boolean(capturado), detalle: capturado };
  } else {
    r.envio = { boton: null, disparado: false, detalle: "sin formulario válido" };
  }
  teclado.push(r);
  log(`teclado ${etiqueta}: primer Tab=${r.primerTab.tag}${r.primerTab.id ? "#" + r.primerTab.id : ""}, ${r.paradas} paradas, trampa=${r.trampa}, Enter=${r.envio.disparado}`);
  return r;
}

/* --- Sesión --------------------------------------------------------------- */
async function entrarConPassword(page) {
  await goto(page, "/auth/login");
  await page.fill("#campo-login", ADMIN.login);
  await page.fill("#campo-password", ADMIN.password);
  const espera = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.request().isNavigationRequest(),
    { timeout: 20000 }
  );
  await page.click('form[action="/auth/login"] button[type="submit"]');
  await espera;
  await page.waitForLoadState("load").catch(() => {});
}

async function completarMfa(page) {
  // El servidor rechaza un código de un paso ya usado (antirreplay): si el
  // paso actual coincide con el de una corrida anterior, se reintenta con el
  // siguiente (la ventana acepta ±1 paso).
  for (let intento = 0; intento < 3 && /\/auth\/mfa/.test(page.url()); intento++) {
    await page.fill("#campo-codigo", await generador.codigo());
    const espera = page.waitForResponse(
      (r) => r.request().method() === "POST" && r.request().isNavigationRequest(),
      { timeout: 20000 }
    );
    await page.click('form[action="/auth/mfa"] button[type="submit"]');
    await espera;
    await page.waitForLoadState("load").catch(() => {});
  }
  if (/\/auth\/mfa/.test(page.url())) throw new Error("El código TOTP no fue aceptado tras 3 intentos");
  await page.waitForURL((u) => u.pathname === "/", { timeout: 20000 });
}

/** Enrola el administrador si hace falta y devuelve el secreto base32. */
async function asegurarEnrolamiento(page) {
  await page.waitForSelector('form[action="/auth/seguridad/totp/iniciar"]', { timeout: 15000 });
  const espera = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.request().isNavigationRequest(),
    { timeout: 20000 }
  );
  await page.click('form[action="/auth/seguridad/totp/iniciar"] button[type="submit"]');
  await espera;
  await page.waitForLoadState("load").catch(() => {});
  await page.waitForSelector(".clave-manual__valor", { timeout: 15000 });
  const manual = await page.locator(".clave-manual__valor").first().textContent();
  const valor = manual.replace(/[^A-Za-z2-7]/g, "").toUpperCase();
  if (valor.length < 16) throw new Error(`Clave manual TOTP inesperada: "${manual}"`);
  guardarSecreto(valor);
  generador = crearGenerador(valor, { log });

  const esperaConfirmar = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.request().isNavigationRequest(),
    { timeout: 20000 }
  );
  await page.fill("#campo-codigo", await generador.codigo());
  await page.click('form[action="/auth/seguridad/totp/confirmar"] button[type="submit"]');
  await esperaConfirmar;
  await page.waitForLoadState("load").catch(() => {});
  await page.waitForSelector(".codigos-lista", { timeout: 15000 });
  return valor;
}

async function cerrarSesion(page) {
  const menu = page.locator("details[data-user-menu]:visible").first();
  if (await menu.count()) await menu.locator("summary").first().click();
  const espera = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.request().isNavigationRequest(),
    { timeout: 20000 }
  );
  const boton = page.locator('form[action="/auth/logout"]:visible button[type="submit"]').first();
  if (await boton.count()) await boton.click();
  else await page.locator('form[action="/auth/logout"]').first().evaluate((f) => f.requestSubmit());
  await espera;
  await page.waitForLoadState("load").catch(() => {});
}

let navegador = null;

/* --- Flujo --------------------------------------------------------------- */
async function main() {
  if (!fs.existsSync(AXE_PATH)) throw new Error(`Falta axe-core en ${AXE_PATH} (npm install).`);
  leerSecretoGuardado();
  const { browser, version, mode } = await launchBrowser();
  navegador = browser;
  log(`base=${BASE} · ${mode} ${version} · anchos=${ANCHOS_EFECTIVOS.map((a) => a.nombre).join(", ")}`);

  for (const ancho of ANCHOS_EFECTIVOS) {
    // `bypassCSP` es necesario para inyectar axe-core (la CSP del
    // endurecimiento A6a solo admite scripts con nonce). No altera el DOM, los
    // estilos ni la semántica: solo permite evaluar el script de la auditoría.
    const contexto = await browser.newContext({
      viewport: { width: ancho.width, height: ancho.height },
      locale: "es-PY",
      bypassCSP: true,
    });
    const page = await contexto.newPage();
    await page.addInitScript(PAGE_HELPERS);

    // 1) Pantallas anónimas
    for (const ruta of RUTAS_ANONIMAS) await auditar(page, ruta, ancho);
    await goto(page, "/auth/login");
    await probarTeclado(page, `login-${ancho.nombre}`);

    // 2) Acceso: enrolamiento (solo la primera vez) o segundo factor.
    await entrarConPassword(page);
    let camino = new URL(page.url()).pathname;
    if (camino === "/auth/seguridad") {
      await auditar(page, { slug: "seguridad-sin-activar", ruta: "/auth/seguridad", nombre: "Seguridad (autenticador sin activar)" }, ancho);
      const valor = await asegurarEnrolamiento(page);
      generador = generador || crearGenerador(valor, { log });
      log(`enrolamiento completado (secreto guardado en ${SECRETO_FILE})`);
      await auditar(page, { slug: "seguridad-codigos-nuevos", ruta: "/auth/seguridad", nombre: "Seguridad (códigos de respaldo recién generados)" }, ancho);
    } else if (camino === "/auth/mfa") {
      if (!secreto) throw new Error("El administrador ya está enrolado y no hay secreto TOTP: ejecute scripts/e2e-auth-reset.ps1");
      generador = generador || crearGenerador(secreto, { log });
      await completarMfa(page);
    }

    // 3) Pantallas autenticadas
    for (const ruta of RUTAS_AUTENTICADAS) await auditar(page, ruta, ancho);
    await goto(page, "/auth/usuarios");
    await probarTeclado(page, `usuarios-${ancho.nombre}`);

    // 4) Segundo factor: se cierra sesión y se vuelve a entrar para dejar un
    // challenge pendiente y auditarlo en este ancho.
    await cerrarSesion(page);
    await entrarConPassword(page);
    if (new URL(page.url()).pathname !== "/auth/mfa") {
      throw new Error(`Se esperaba /auth/mfa tras el login y se llegó a ${page.url()}`);
    }
    await auditar(page, { slug: "mfa", ruta: "/auth/mfa", nombre: "Verificación en dos pasos (challenge pendiente)" }, ancho);
    await probarTeclado(page, `mfa-${ancho.nombre}`);
    await completarMfa(page);

    await cerrarSesion(page).catch(() => {});
    await contexto.close();
  }

  await browser.close();

  fs.writeFileSync(
    path.join(AXE_DIR, "teclado-auth.json"),
    JSON.stringify({ base: BASE, navegador: version, comprobaciones: teclado }, null, 2),
    "utf8"
  );
  fs.writeFileSync(path.join(AXE_DIR, "resumen.md"), resumenMarkdown(registros, teclado), "utf8");

  const total = registros.reduce(
    (acc, r) => {
      for (const i of IMPACTOS) acc[i] += r.porImpacto[i] || 0;
      acc.reglas += r.reglas;
      acc.nodos += r.nodos;
      return acc;
    },
    { critical: 0, serious: 0, moderate: 0, minor: 0, reglas: 0, nodos: 0 }
  );
  console.log(
    `\n[a11y-auth] ${registros.length} auditorías · ` +
      `${total.reglas} reglas / ${total.nodos} nodos · ` +
      `critical=${total.critical} serious=${total.serious} moderate=${total.moderate} minor=${total.minor}`
  );
  if (total.critical || total.serious || total.moderate) process.exitCode = 2;
}

/* --- Informe ------------------------------------------------------------- */
function resumenMarkdown(regs, teclas) {
  const L = [];
  L.push("# Auditoría axe de las pantallas de autenticación");
  L.push("");
  L.push(`**Base:** ${BASE} · **Generado:** ${new Date().toLocaleString("es-PY")}`);
  L.push("");
  L.push("## Resultado por pantalla y ancho");
  L.push("");
  L.push("| Pantalla | Ruta | Ancho | HTTP | critical | serious | moderate | minor | Reglas | Nodos |");
  L.push("|---|---|---|---|---|---|---|---|---|---|");
  for (const r of regs) {
    L.push(
      `| ${r.nombre} | \`${r.ruta}\` | ${r.ancho} | ${r.status} | ${r.porImpacto.critical} | ${r.porImpacto.serious} | ${r.porImpacto.moderate} | ${r.porImpacto.minor} | ${r.reglas} | ${r.nodos} |`
    );
  }
  const totales = { critical: 0, serious: 0, moderate: 0, minor: 0, reglas: 0, nodos: 0 };
  for (const r of regs) {
    for (const i of IMPACTOS) totales[i] += r.porImpacto[i] || 0;
    totales.reglas += r.reglas;
    totales.nodos += r.nodos;
  }
  L.push(
    `| **Total** | | | | **${totales.critical}** | **${totales.serious}** | **${totales.moderate}** | **${totales.minor}** | **${totales.reglas}** | **${totales.nodos}** |`
  );
  L.push("");
  L.push("## Violaciones por regla");
  L.push("");
  const porRegla = new Map();
  for (const r of regs) {
    for (const v of r.violaciones) {
      const k = `${v.id}|${v.impact}`;
      const previo = porRegla.get(k) || { id: v.id, impact: v.impact, help: v.help, nodos: 0, pantallas: new Set() };
      previo.nodos += v.nodos;
      previo.pantallas.add(`${r.slug}@${r.ancho}`);
      porRegla.set(k, previo);
    }
  }
  if (porRegla.size === 0) {
    L.push("Sin violaciones en ninguna pantalla ni ancho.");
  } else {
    L.push("| Regla | Impacto | Nodos | Pantallas | Ayuda |");
    L.push("|---|---|---|---|---|");
    for (const v of porRegla.values()) {
      L.push(`| \`${v.id}\` | ${v.impact} | ${v.nodos} | ${[...v.pantallas].join(", ")} | ${v.help} |`);
    }
  }
  L.push("");
  L.push("## Reglas incompletas (requieren criterio humano)");
  L.push("");
  const inc = new Map();
  for (const r of regs) {
    for (const i of r.incompletos) {
      const k = i.id;
      const previo = inc.get(k) || { id: i.id, nodos: 0, pantallas: new Set() };
      previo.nodos += i.nodos;
      previo.pantallas.add(`${r.slug}@${r.ancho}`);
      inc.set(k, previo);
    }
  }
  if (!inc.size) L.push("Ninguna.");
  for (const i of inc.values()) {
    L.push(`- \`${i.id}\`: ${i.nodos} nodos en ${[...i.pantallas].join(", ")}`);
  }
  L.push("");
  L.push("## Teclado");
  L.push("");
  L.push("| Pantalla | Foco inicial | Primer Tab | Skip link alcanzable | Paradas | Trampa | Ocultos | Enter dispara envío |");
  L.push("|---|---|---|---|---|---|---|---|");
  for (const t of teclas) {
    const primero = t.primerTab ? `${t.primerTab.tag}${t.primerTab.id ? " #" + t.primerTab.id : ""}` : "—";
    const inicial = t.focoInicial ? `${t.focoInicial.tag}${t.focoInicial.id ? " #" + t.focoInicial.id : ""}` : "—";
    const skip = t.skipLink ? `${t.skipLink.alcanzado ? "sí" : "no"}${t.skipLink.visible ? " (visible)" : ""}` : "—";
    L.push(
      `| ${t.pantalla} | ${inicial} | ${primero}${t.primerTab && t.primerTab.visible ? " (visible, anillo)" : " (sin anillo)"} | ${skip} | ${t.paradas} | ${t.trampa ? "SÍ" : "no"} | ${t.ocultos.length} | ${t.envio && t.envio.disparado ? "sí" : "no"} |`
    );
  }
  L.push("");
  return L.join("\n");
}

main().catch(async (err) => {
  console.error(`\n[a11y-auth] ERROR: ${err.message}`);
  try {
    await navegador?.close();
  } catch {
    /* el navegador ya estaba cerrado */
  }
  process.exitCode = 1;
});
