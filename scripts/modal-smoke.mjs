// Smoke del modal de acciones de usuarios en un navegador real (Edge).
// Uso: node scripts/modal-smoke.mjs
import { chromium } from "playwright";

const BASE = "http://localhost:8000";
const USUARIO = process.env.EET_SMOKE_USER || "demo.admin";
const PASSWORD = process.env.EET_SMOKE_PASSWORD || "Demo-Admin-2026!";

const navegador = await chromium.launch({ channel: "msedge", headless: true });
const pagina = await navegador.newPage();
const errores = [];
pagina.on("pageerror", (error) => errores.push(String(error)));

try {
  await pagina.goto(`${BASE}/auth/login`, { waitUntil: "networkidle" });
  await pagina.fill('input[name="login"]', USUARIO);
  await pagina.fill('input[name="password"]', PASSWORD);
  await pagina.locator('form button[type="submit"]').first().click();
  await pagina.waitForURL(`${BASE}/`, { timeout: 10000 });

  await pagina.goto(`${BASE}/auth/usuarios`, { waitUntil: "networkidle" });
  const disparador = pagina.locator("[data-modal-open]").first();
  await disparador.click();

  const dialogo = pagina.locator("dialog[open]").first();
  await dialogo.waitFor({ state: "visible", timeout: 5000 });
  const titulo = (await dialogo.locator("h2").innerText()).trim();
  const formularios = await dialogo.locator("form").count();
  console.log(`ABIERTO: ${titulo} (${formularios} formularios)`);

  await pagina.keyboard.press("Escape");
  await pagina.waitForTimeout(250);
  console.log(`TRAS_ESCAPE: ${await pagina.locator("dialog[open]").count()} abiertos`);
  console.log(`FOCO_EN_DISPARADOR: ${await disparador.evaluate(
    (el) => document.activeElement === el,
  )}`);

  await disparador.click();
  const dialogo2 = pagina.locator("dialog[open]").first();
  await dialogo2.waitFor({ state: "visible" });
  await dialogo2.locator("[data-modal-close]").last().click();
  await pagina.waitForTimeout(250);
  console.log(`TRAS_CERRAR: ${await pagina.locator("dialog[open]").count()} abiertos`);

  if (errores.length) {
    console.error("ERRORES_JS:", errores);
    process.exitCode = 1;
  } else {
    console.log("MODAL OK");
  }
} finally {
  await navegador.close();
}
