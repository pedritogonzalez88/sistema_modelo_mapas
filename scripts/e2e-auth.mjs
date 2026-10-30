#!/usr/bin/env node
/* =========================================================================
   scripts/e2e-auth.mjs
   E2E del flujo de autenticación completo (Chrome real vía Playwright).

   Cubre: login genérico, enrolamiento TOTP, códigos de respaldo, segundo
   factor, administración de usuarios, auditoría + CSV, recuperación por
   correo (backend `file`), permisos por rol y cierre de sesión.

   Uso (con la app arrancada en :8033 y EET_EMAIL_DIR apuntando a la bandeja):
     node scripts/e2e-auth.mjs --base http://127.0.0.1:8033

   Antes de cada corrida: ./scripts/e2e-auth-reset.ps1

   Salidas:
     docs/qa-visual/e2e-auth/<nn>-<slug>.png   capturas por paso
     docs/qa-visual/e2e-auth/report.json       resultado estructurado
     docs/qa-visual/e2e-auth/resumen.md        tabla paso/resultado/evidencia

   Falla rápido: ante un error toma captura, imprime el paso y el motivo y
   termina con código 1. El paso 8 (correo) puede fallar sin abortar el resto:
   se registra como hallazgo, según el encargo.
   ========================================================================= */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { launchBrowser, ROOT, arg } from "./a11y-helpers.mjs";
import { crearGenerador } from "./totp.mjs";

const BASE = (process.env.E2E_BASE_URL || arg("base", "http://127.0.0.1:8033")).replace(/\/$/, "");
const OUT = path.resolve(ROOT, arg("out", "docs/qa-visual/e2e-auth"));
const MAIL_DIR = process.env.EET_EMAIL_DIR || "C:\\Users\\pgonzalez\\AppData\\Local\\Temp\\opencode\\mail-e2e";

const ADMIN_PASSWORD = process.env.EET_ADMIN_PASSWORD;
if (!ADMIN_PASSWORD) {
  console.error("Falta EET_ADMIN_PASSWORD: definila en el entorno antes de ejecutar el E2E.");
  process.exit(1);
}

const ADMIN = {
  login: "e2e-admin",
  password: ADMIN_PASSWORD,
  email: "e2e-admin@ejemplo.org",
};
const USER = {
  nombre: "Usuario E2E",
  username: "e2e-user",
  email: "e2e-user@ejemplo.org",
  // Credenciales efimeras: la cuenta se crea y se elimina en esta corrida.
  password: `E2e-${randomUUID()}`,
  passwordNueva: `E2e-${randomUUID()}`,
};

fs.mkdirSync(OUT, { recursive: true });
// Limpia capturas e informes de corridas anteriores: la evidencia debe
// corresponder exactamente a esta ejecución.
for (const f of fs.readdirSync(OUT)) {
  if (/\.(png|json|md|csv)$/i.test(f)) fs.rmSync(path.join(OUT, f), { force: true });
}

/* --- Estado del flujo ---------------------------------------------------- */
const pasos = [];
let fallo = null;
let shotN = 0;
let generador = null; // TOTP del admin de prueba (se crea en el paso 3)
let codigosRespaldo = [];
let passwordTemporal = null;
let userId = null;

// El secreto TOTP del administrador de prueba se guarda fuera del repositorio
// para que la auditoría de accesibilidad reutilice el mismo enrolamiento.
const SECRETO_FILE = path.join(process.env.TEMP || os.tmpdir(), "opencode", "e2e-totp-secret.txt");

function guardarSecretoTemporal(valor) {
  try {
    fs.mkdirSync(path.dirname(SECRETO_FILE), { recursive: true });
    fs.writeFileSync(SECRETO_FILE, valor, "utf8");
  } catch (err) {
    log(`aviso: no se pudo guardar el secreto TOTP temporal (${err.message})`);
  }
}

/* --- Utilidades ---------------------------------------------------------- */
const ahora = () => new Date().toISOString();
const log = (msg) => console.log(`[e2e] ${msg}`);

async function captura(page, slug) {
  shotN += 1;
  const nombre = `${String(shotN).padStart(2, "0")}-${slug}.png`;
  const destino = path.join(OUT, nombre);
  await page.screenshot({ path: destino, fullPage: true }).catch(async () => {
    await page.screenshot({ path: destino }).catch(() => {});
  });
  return `docs/qa-visual/e2e-auth/${nombre}`;
}

/** Ejecuta un paso y registra su resultado; si falla, captura y aborta. */
async function paso(id, titulo, fn, { continuarSiFalla = false } = {}) {
  const inicio = Date.now();
  const registro = { id, titulo, ok: false, detalle: "", http: [], evidencia: [] };
  try {
    const r = await fn(registro);
    registro.ok = true;
    registro.detalle = r || "OK";
    log(`${id} ${titulo} — OK`);
  } catch (err) {
    registro.detalle = String(err && err.message ? err.message : err);
    log(`${id} ${titulo} — FALLO: ${registro.detalle}`);
    if (!continuarSiFalla) fallo = registro;
  }
  registro.ms = Date.now() - inicio;
  pasos.push(registro);
  if (fallo) throw new Error(`Paso ${fallo.id} (${fallo.titulo}): ${fallo.detalle}`);
  return registro;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

/** Sigue una navegación completa y devuelve la cadena de documentos HTTP. */
async function conCadena(page, accion) {
  const cadena = [];
  const onResp = (r) => {
    if (r.request().isNavigationRequest()) {
      cadena.push({ url: r.url().replace(BASE, ""), status: r.status(), metodo: r.request().method() });
    }
  };
  page.on("response", onResp);
  try {
    await accion();
    await page.waitForLoadState("load", { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(300);
  } finally {
    page.off("response", onResp);
  }
  return cadena;
}

async function ir(page, ruta, registro) {
  const resp = await page.goto(`${BASE}${ruta}`, { waitUntil: "networkidle", timeout: 45000 });
  const status = resp ? resp.status() : 0;
  if (registro) registro.http.push({ url: ruta, status, metodo: "GET" });
  return { resp, status };
}

async function enviar(page, registro, accion, { espera = 20000 } = {}) {
  const cadena = await conCadena(page, async () => {
    const esperaResp = page.waitForResponse(
      (r) => r.request().method() === "POST" && r.request().isNavigationRequest(),
      { timeout: espera }
    );
    await accion();
    await esperaResp;
  });
  if (registro) registro.http.push(...cadena);
  return cadena;
}

async function loginPassword(page, registro, usuario, password) {
  await ir(page, "/auth/login", registro);
  await page.fill("#campo-login", usuario);
  await page.fill("#campo-password", password);
  return enviar(page, registro, () => page.click('form[action="/auth/login"] button[type="submit"]'));
}

async function logout(page, registro) {
  const menu = page.locator("details[data-user-menu]:visible").first();
  if (await menu.count()) {
    await menu.locator("summary").first().click();
    await enviar(page, registro, () =>
      page.locator('form[action="/auth/logout"]:visible button[type="submit"]').first().click()
    );
  } else {
    await enviar(page, registro, () =>
      page.locator('form[action="/auth/logout"]').first().evaluate((f) => f.requestSubmit())
    );
  }
  await page.waitForURL(/\/auth\/login/, { timeout: 15000 });
}

const cookiesDeSesion = (cookies) => cookies.filter((c) => c.name === "eet_session");

/** Deja una sesión de administrador lista (contraseña + TOTP) desde cualquier estado. */
async function asegurarSesionAdmin(page, registro) {
  await ir(page, "/auth/login", registro);
  if (!/\/auth\/login/.test(page.url())) await logout(page, registro);
  await loginPassword(page, registro, ADMIN.login, ADMIN.password);
  await page.waitForLoadState("load").catch(() => {});
  if (/\/auth\/mfa/.test(page.url())) {
    await page.fill("#campo-codigo", await generador.codigo());
    await enviar(page, registro, () => page.click('form[action="/auth/mfa"] button[type="submit"]'));
  }
  await page.waitForURL((u) => u.pathname === "/", { timeout: 20000 });
}

/* --- Correo (.eml) ------------------------------------------------------- */
function qpDecode(texto) {
  return texto
    .replace(/=\r?\n/g, "")
    .replace(/=([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
    .replace(/\r\n/g, "\n");
}

function correosRecientes() {
  return fs
    .readdirSync(MAIL_DIR)
    .filter((f) => f.toLowerCase().endsWith(".eml"))
    .map((f) => ({ f, mtime: fs.statSync(path.join(MAIL_DIR, f)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime);
}

/** Variantes decodificadas del mensaje: tal cual, quoted-printable y base64. */
function variantes(crudo) {
  const salida = [qpDecode(crudo), crudo];
  for (const bloque of crudo.split(/^--.*$/m)) {
    if (/content-transfer-encoding:\s*base64/i.test(bloque)) {
      const cuerpo = bloque.split(/\r?\n\r?\n/).slice(1).join("\n").replace(/\s+/g, "");
      try {
        salida.push(qpDecode(Buffer.from(cuerpo, "base64").toString("utf8")));
      } catch {
        /* bloque no decodificable: se ignora */
      }
    }
  }
  return salida;
}

function leerUltimoEnlace(patron) {
  const archivos = correosRecientes();
  if (!archivos.length) throw new Error(`No hay correos en ${MAIL_DIR}`);
  const nombre = archivos[0].f;
  const crudo = fs.readFileSync(path.join(MAIL_DIR, nombre), "utf8");
  for (const texto of variantes(crudo)) {
    const m = texto.match(patron);
    if (!m) continue;
    const token = m[1] || "";
    // Descarta coincidencias parciales de una codificación (p. ej. «3Dna8…»).
    if (token.length < 20 || /[^A-Za-z0-9_-]/.test(token)) continue;
    return { archivo: nombre, enlace: m[0].replace(/&amp;/g, "&") };
  }
  throw new Error(`El correo ${nombre} no contiene un enlace de restablecimiento válido`);
}

function leerCodigoCorreo() {
  for (const { f } of correosRecientes().slice(0, 5)) {
    const crudo = fs.readFileSync(path.join(MAIL_DIR, f), "utf8");
    for (const texto of variantes(crudo)) {
      const m = texto.match(/c[oó]digo de acceso[\s\S]{0,160}?(\d{6})/i);
      if (m) return { archivo: f, codigo: m[1] };
    }
  }
  throw new Error(`No se encontró un código de 6 dígitos en los correos de ${MAIL_DIR}`);
}

/* --- Flujo --------------------------------------------------------------- */
async function main() {
  log(`base=${BASE} salida=${OUT} correo=${MAIL_DIR}`);
  const { browser, version, mode } = await launchBrowser();
  const contexto = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "es-PY" });
  const page = await contexto.newPage();
  const erroresConsola = [];
  page.on("pageerror", (e) => erroresConsola.push(String(e.message)));

  const reporte = {
    base: BASE,
    navegador: `${mode} ${version}`,
    inicio: ahora(),
    pasos,
    hallazgos: [],
    erroresConsola,
  };

  try {
    /* 1 — Login: skip link, etiquetas, credenciales incorrectas */
    await paso("1", "Login: skip link, etiquetas y error genérico sin sesión", async (reg) => {
      const { status } = await ir(page, "/auth/login", reg);
      assert(status === 200, `GET /auth/login devolvió ${status}`);
      reg.evidencia.push(await captura(page, "login"));

      // El campo de usuario lleva autofocus: el skip link es el primer
      // tabulable del documento y se alcanza con Shift+Tab; el primer Tab
      // avanza a un control con anillo de foco visible.
      const leerFoco = () =>
        page.evaluate(() => {
          const el = document.activeElement;
          const r = el ? el.getBoundingClientRect() : { width: 0, height: 0 };
          const cs = el ? getComputedStyle(el) : {};
          return {
            tag: el ? el.tagName.toLowerCase() : null,
            id: el ? el.id : null,
            href: el ? el.getAttribute("href") : null,
            texto: el && el.textContent ? el.textContent.trim() : "",
            visible: r.width > 2 && r.height > 2 && cs.visibility !== "hidden",
            anillo: Boolean(
              (cs.outlineStyle && cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth || "0") > 0) ||
                (cs.boxShadow && cs.boxShadow !== "none")
            ),
          };
        });

      const primero = await page.evaluate(() => {
        const el = document.querySelector("a[href='#contenido']");
        const tabulables = Array.from(
          document.querySelectorAll(
            "a[href], button, input:not([type=hidden]), select, textarea, [tabindex]:not([tabindex='-1'])"
          )
        ).filter((e) => e.offsetWidth > 0 || e.offsetHeight > 0);
        return {
          existe: Boolean(el),
          esPrimero: Boolean(el) && tabulables[0] === el,
          texto: el ? el.textContent.trim() : null,
        };
      });
      assert(primero.existe && primero.esPrimero,
        `El skip link no es el primer tabulable del documento (${JSON.stringify(primero)})`);

      await page.keyboard.press("Tab");
      const trasTab = await leerFoco();
      assert(trasTab.visible && trasTab.anillo,
        `El primer Tab no deja un foco visible con anillo (${JSON.stringify(trasTab)})`);
      await page.keyboard.press("Shift+Tab");
      let focoSkip = await leerFoco();
      for (let i = 0; i < 2 && focoSkip.href !== "#contenido"; i++) {
        await page.keyboard.press("Shift+Tab");
        focoSkip = await leerFoco();
      }
      assert(focoSkip.href === "#contenido" && focoSkip.visible,
        `No se alcanza el skip link visible con Shift+Tab (foco: ${JSON.stringify(focoSkip)})`);

      const etiquetas = await page.evaluate(() => {
        const t = (sel) => {
          const l = document.querySelector(sel);
          return l ? l.textContent.replace(/\s+/g, " ").trim() : null;
        };
        const input = (sel) => {
          const i = document.querySelector(sel);
          return i ? { id: i.id, name: i.name, required: i.required, ac: i.getAttribute("autocomplete") } : null;
        };
        return {
          usuario: t('label[for="campo-login"]'),
          password: t('label[for="campo-password"]'),
          inputUsuario: input("#campo-login"),
          inputPassword: input("#campo-password"),
        };
      });
      assert(etiquetas.usuario && /usuario/i.test(etiquetas.usuario), "Falta la etiqueta del usuario");
      assert(etiquetas.password && /contrase/i.test(etiquetas.password), "Falta la etiqueta de la contraseña");
      assert(etiquetas.inputUsuario && etiquetas.inputUsuario.required, "El campo usuario no es obligatorio");
      assert(etiquetas.inputUsuario.ac === "username", "autocomplete del usuario no es 'username'");
      assert(etiquetas.inputPassword.ac === "current-password", "autocomplete de la contraseña incorrecto");

      const leerAlerta = () =>
        page.evaluate(() => {
          const b = document.querySelector('.banner[role="alert"]');
          return b ? b.textContent.replace(/\s+/g, " ").trim() : null;
        });

      // Credenciales incorrectas (cuenta existente) → mensaje genérico.
      await loginPassword(page, reg, ADMIN.login, "clave-equivocada-e2e");
      const mensajeExistente = await leerAlerta();
      assert(mensajeExistente && /Usuario o contrase/i.test(mensajeExistente),
        `Sin mensaje genérico tras credenciales incorrectas (${mensajeExistente})`);
      let cookies = await contexto.cookies();
      assert(cookiesDeSesion(cookies).length === 0, "Se creó sesión con credenciales incorrectas");
      assert(!cookies.some((c) => c.name === "eet_mfa"), "Se creó un challenge con credenciales incorrectas");
      reg.evidencia.push(await captura(page, "login-error-generico"));

      // Misma respuesta para una cuenta inexistente (sin enumeración).
      await loginPassword(page, reg, "e2e-noexiste", "clave-equivocada-e2e");
      const mensajeInexistente = await leerAlerta();
      assert(mensajeInexistente === mensajeExistente,
        `El mensaje difiere entre cuenta existente e inexistente: "${mensajeExistente}" vs "${mensajeInexistente}"`);
      cookies = await contexto.cookies();
      assert(cookiesDeSesion(cookies).length === 0, "Se creó sesión con una cuenta inexistente");

      return `200 login; skip link = primer tabulable y visible al enfocar; primer Tab con anillo visible; etiquetas y autocomplete correctos; mensaje genérico idéntico (${JSON.stringify(mensajeExistente)}) y sin cookies de sesión/MFA`;
    });

    /* 2 — Login del admin → enrolamiento obligatorio */
    await paso("2", "Login del admin redirige a /auth/seguridad (enrolamiento obligatorio)", async (reg) => {
      const cadena = await loginPassword(page, reg, ADMIN.login, ADMIN.password);
      assert(cadena.some((c) => c.status === 303), `El POST de login no devolvió 303 (${JSON.stringify(cadena)})`);
      await page.waitForURL(/\/auth\/seguridad/, { timeout: 15000 });
      const status = (await page.request.get(`${BASE}/auth/seguridad`)).status();
      reg.http.push({ url: "/auth/seguridad", status, metodo: "GET" });
      const info = await page.evaluate(() => ({
        h1: (document.querySelector("h1") || {}).textContent.trim(),
        aviso: (document.querySelector(".banner") || {}).textContent || "",
        tieneIniciar: !!document.querySelector('form[action="/auth/seguridad/totp/iniciar"]'),
      }));
      assert(/Seguridad/.test(info.h1), `h1 inesperado en /auth/seguridad: ${info.h1}`);
      assert(/exige el segundo factor/i.test(info.aviso), "No aparece el aviso de enrolamiento obligatorio");
      assert(info.tieneIniciar, "No está el formulario para iniciar el enrolamiento");
      reg.evidencia.push(await captura(page, "seguridad-enrolamiento"));
      return "303 → /auth/seguridad 200; aviso de enrolamiento y formulario «Activar autenticador» presentes";
    });

    /* 3 — TOTP: clave manual, confirmación y códigos de respaldo */
    await paso("3", "Enrolamiento TOTP con clave manual y captura de los 10 códigos", async (reg) => {
      await enviar(page, reg, () =>
        page.click('form[action="/auth/seguridad/totp/iniciar"] button[type="submit"]')
      );
      await page.waitForSelector(".clave-manual__valor", { timeout: 15000 });
      const manual = await page.locator(".clave-manual__valor").first().textContent();
      const secreto = manual.replace(/[^A-Za-z2-7]/g, "").toUpperCase();
      assert(secreto.length >= 16, `Clave manual inesperada: "${manual}"`);
      generador = crearGenerador(secreto, { log });
      guardarSecretoTemporal(secreto);
      reg.evidencia.push(await captura(page, "seguridad-qr-clave-manual"));

      await page.fill("#campo-codigo", await generador.codigo());
      await enviar(page, reg, () =>
        page.click('form[action="/auth/seguridad/totp/confirmar"] button[type="submit"]')
      );
      await page.waitForSelector(".codigos-lista", { timeout: 15000 });
      codigosRespaldo = (await page.locator(".codigos-lista__codigo").allTextContents())
        .map((c) => c.trim())
        .filter(Boolean);
      assert(codigosRespaldo.length === 10, `Se esperaban 10 códigos y hay ${codigosRespaldo.length}`);
      assert(codigosRespaldo.every((c) => /^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(c)),
        `Formato de códigos inesperado: ${codigosRespaldo[0]}`);
      const activado = await page.evaluate(() =>
        Array.from(document.querySelectorAll(".badge")).some((b) => /Activado/.test(b.textContent))
      );
      assert(activado, "El TOTP no quedó marcado como activado");
      reg.evidencia.push(await captura(page, "codigos-respaldo"));
      reporte.codigosRespaldo = codigosRespaldo.map((c) => `${c.slice(0, 4)}-****`);
      return `clave manual de ${secreto.length} caracteres; TOTP aceptado; 10 códigos XXXX-XXXX capturados; estado «Activado»`;
    });

    /* 4 — Logout, login y segundo factor por TOTP */
    await paso("4", "Logout, login y /auth/mfa con TOTP llega a /", async (reg) => {
      await logout(page, reg);
      await loginPassword(page, reg, ADMIN.login, ADMIN.password);
      await page.waitForURL(/\/auth\/mfa/, { timeout: 15000 });
      const mfaStatus = (await page.request.get(page.url())).status();
      reg.http.push({ url: "/auth/mfa", status: mfaStatus, metodo: "GET" });
      const metodo = await page.locator('input[name="metodo"]').first().inputValue();
      assert(metodo === "totp", `El challenge no es TOTP (metodo=${metodo})`);
      reg.evidencia.push(await captura(page, "mfa-totp"));

      await page.fill("#campo-codigo", await generador.codigo());
      await enviar(page, reg, () => page.click('form[action="/auth/mfa"] button[type="submit"]'));
      await page.waitForURL((u) => u.pathname === "/", { timeout: 15000 });
      const inicio = (await page.request.get(`${BASE}/`)).status();
      reg.http.push({ url: "/", status: inicio, metodo: "GET" });
      const h1 = await page.locator("h1").first().textContent();
      assert(inicio === 200, `/ devolvió ${inicio} tras el segundo factor`);
      reg.evidencia.push(await captura(page, "inicio-con-sesion"));
      return `303 → /auth/mfa 200 (metodo=totp) → 303 → / 200 (h1 «${h1.trim()}»)`;
    });

    /* 5 — Código de respaldo (uso único) */
    await paso("5", "Código de respaldo válido una vez y rechazo al repetirlo", async (reg) => {
      const codigo = codigosRespaldo[0];
      await logout(page, reg);
      await loginPassword(page, reg, ADMIN.login, ADMIN.password);
      await page.waitForURL(/\/auth\/mfa/, { timeout: 15000 });

      await page.locator("details.auth-detalle summary").first().click();
      await page.fill("#campo-respaldo", codigo);
      await enviar(page, reg, () => page.click('form[action="/auth/mfa"] button[type="submit"]'));
      await page.waitForURL((u) => u.pathname === "/", { timeout: 15000 });
      reg.evidencia.push(await captura(page, "respaldo-usado-ok"));

      // Segundo intento con el mismo código: debe fallar y no abrir sesión.
      await logout(page, reg);
      await loginPassword(page, reg, ADMIN.login, ADMIN.password);
      await page.waitForURL(/\/auth\/mfa/, { timeout: 15000 });
      await page.locator("details.auth-detalle summary").first().click();
      await page.fill("#campo-respaldo", codigo);
      await enviar(page, reg, () => page.click('form[action="/auth/mfa"] button[type="submit"]'));
      const estado = await page.evaluate(() => ({
        url: location.pathname,
        error: ((document.querySelector('.banner[role="alert"]') || {}).textContent || "").trim(),
      }));
      assert(estado.url === "/auth/mfa", `El código reutilizado no fue rechazado (url=${estado.url})`);
      assert(/C.digo incorrecto o expirado/.test(estado.error),
        `Mensaje inesperado al reutilizar el respaldo: "${estado.error}"`);
      const cookies = await contexto.cookies();
      assert(cookiesDeSesion(cookies).length === 0, "El código reutilizado creó sesión");
      reg.evidencia.push(await captura(page, "respaldo-reutilizado-falla"));
      return `${codigo.slice(0, 4)}-**** aceptado una vez (303 → / 200); repetido → 200 /auth/mfa con «Código incorrecto o expirado» y sin cookie de sesión`;
    });

    /* 6 — Administración de usuarios */
    await paso("6", "Alta, cambio de rol, baja/alta, reset de contraseña y de 2FA de e2e-user", async (reg) => {
      await asegurarSesionAdmin(page, reg);
      await ir(page, "/auth/usuarios", reg);

      if (!(await page.locator("#alta-usuario").evaluate((d) => d.open))) {
        await page.click("#alta-usuario summary");
      }
      await page.fill("#campo-nombre", USER.nombre);
      await page.fill("#campo-username", USER.username);
      await page.fill("#campo-email", USER.email);
      await page.selectOption("#campo-rol", "consulta");
      await page.fill("#campo-password", USER.password);
      await page.fill("#campo-confirmacion", USER.password);
      await enviar(page, reg, () =>
        page.click('form[action="/auth/usuarios"][method="post"] button[type="submit"]')
      );
      const creado = await page.evaluate(() => ({
        aviso: ((document.querySelector(".banner") || {}).textContent || "").trim(),
        filas: Array.from(document.querySelectorAll("tbody tr")).map((t) =>
          t.textContent.replace(/\s+/g, " ").trim()
        ),
      }));
      assert(/Usuario creado/.test(creado.aviso), `Sin confirmación de alta: "${creado.aviso}"`);
      const filaTexto = creado.filas.find((t) => t.includes(USER.username));
      assert(filaTexto, "El usuario nuevo no aparece en el listado");
      assert(/Consulta/.test(filaTexto), `El rol inicial no es Consulta: ${filaTexto}`);
      reg.evidencia.push(await captura(page, "usuarios-alta"));

      const accion = await page
        .locator("tbody tr", { hasText: USER.username })
        .first()
        .locator('form[action$="/rol"]')
        .first()
        .getAttribute("action");
      userId = Number((accion.match(/usuarios\/(\d+)\/rol/) || [])[1]);
      assert(userId > 0, `No se pudo obtener el id de ${USER.username} (${accion})`);

      // Cambio de rol → cargador
      await page
        .locator("tbody tr", { hasText: USER.username })
        .first()
        .locator("summary.auth-detalle__resumen")
        .first()
        .click();
      await page.selectOption(`#rol-${userId}`, "cargador");
      await page.fill(`#motivo-rol-${userId}`, "e2e: cambio de rol solicitado");
      await enviar(page, reg, () => page.click(`form[action="/auth/usuarios/${userId}/rol"] button[type="submit"]`));
      const rolNuevo = await page.locator("tbody tr", { hasText: USER.username }).first().textContent();
      assert(/Carga/.test(rolNuevo), `El rol no cambió a Carga: ${rolNuevo.replace(/\s+/g, " ")}`);

      // Baja lógica
      await page
        .locator("tbody tr", { hasText: USER.username })
        .first()
        .locator("summary.auth-detalle__resumen")
        .first()
        .click();
      await page.fill(`#motivo-estado-${userId}`, "e2e: baja temporal");
      await enviar(page, reg, () =>
        page.click(`form[action="/auth/usuarios/${userId}/estado"] button[type="submit"]`)
      );
      let estadoFila = await page.locator("tbody tr", { hasText: USER.username }).first().textContent();
      assert(/Inactiva/.test(estadoFila), `La cuenta no quedó inactiva: ${estadoFila.replace(/\s+/g, " ")}`);

      // Reactivación
      await page
        .locator("tbody tr", { hasText: USER.username })
        .first()
        .locator("summary.auth-detalle__resumen")
        .first()
        .click();
      await page.fill(`#motivo-estado-${userId}`, "e2e: reactivación tras la baja de prueba");
      await enviar(page, reg, () =>
        page.click(`form[action="/auth/usuarios/${userId}/estado"] button[type="submit"]`)
      );
      estadoFila = await page.locator("tbody tr", { hasText: USER.username }).first().textContent();
      assert(/Activa/.test(estadoFila), `La cuenta no se reactivó: ${estadoFila.replace(/\s+/g, " ")}`);

      // Reset de contraseña: la temporal se muestra una sola vez
      await page
        .locator("tbody tr", { hasText: USER.username })
        .first()
        .locator("summary.auth-detalle__resumen")
        .first()
        .click();
      await page.fill(`#motivo-clave-${userId}`, "e2e: olvidó su contraseña");
      await enviar(page, reg, () =>
        page.click(`form[action="/auth/usuarios/${userId}/reset-password"] button[type="submit"]`)
      );
      await page.waitForSelector("#password-temporal", { timeout: 15000 });
      passwordTemporal = (await page.locator("#password-temporal").textContent()).trim();
      assert(passwordTemporal.length >= 12, `Contraseña temporal inesperada: "${passwordTemporal}"`);
      assert(passwordTemporal !== USER.password, "La contraseña temporal repite la anterior");
      assert(/\/auth\/usuarios\/\d+\/reset-password$/.test(page.url()),
        `URL inesperada tras el reset: ${page.url()}`);
      reg.evidencia.push(await captura(page, "usuarios-password-temporal"));

      // Reset de 2FA
      await ir(page, "/auth/usuarios", reg);
      await page
        .locator("tbody tr", { hasText: USER.username })
        .first()
        .locator("summary.auth-detalle__resumen")
        .first()
        .click();
      await page.fill(`#motivo-mfa-${userId}`, "e2e: cambio de teléfono");
      const cadena = await enviar(page, reg, () =>
        page.click(`form[action="/auth/usuarios/${userId}/reset-mfa"] button[type="submit"]`)
      );
      assert(cadena.some((c) => c.status === 303), `El reset de 2FA no redirigió (${JSON.stringify(cadena)})`);
      assert(/ok=mfa_reseteado/.test(page.url()), `URL inesperada tras el reset de 2FA: ${page.url()}`);
      return `alta consulta → cargador → inactiva → activa; contraseña temporal de ${passwordTemporal.length} caracteres mostrada una vez; reset de 2FA con 303 (ok=mfa_reseteado)`;
    });

    /* 7 — Auditoría y CSV */
    await paso("7", "Auditoría: eventos del E2E y CSV con BOM y text/csv", async (reg) => {
      await ir(page, "/auth/auditoria", reg);
      const vista = await page.evaluate(() => ({
        h1: (document.querySelector("h1") || {}).textContent.trim(),
        filas: document.querySelectorAll("tbody tr").length,
        acciones: Array.from(document.querySelectorAll("tbody tr td:nth-child(3)")).map((t) =>
          t.textContent.trim()
        ),
        enlaceCsv: !!document.querySelector('a[href^="/auth/auditoria.csv"]'),
      }));
      assert(/Auditor/.test(vista.h1), `h1 inesperado: ${vista.h1}`);
      assert(vista.filas > 0, "La tabla de auditoría está vacía");
      const esperadas = [
        "user_created",
        "role_changed",
        "user_disabled",
        "user_enabled",
        "password_reset_admin",
        "mfa_reset_admin",
      ];
      const faltan = esperadas.filter((a) => !vista.acciones.includes(a));
      assert(faltan.length === 0, `Faltan acciones en la vista: ${faltan.join(", ")}`);
      assert(vista.enlaceCsv, "No hay enlace de descarga del CSV");
      reg.evidencia.push(await captura(page, "auditoria-eventos"));

      const resp = await contexto.request.get(`${BASE}/auth/auditoria.csv`);
      const tipo = resp.headers()["content-type"] || "";
      const cuerpo = Buffer.from(await resp.body());
      reg.http.push({ url: "/auth/auditoria.csv", status: resp.status(), metodo: "GET" });
      assert(resp.status() === 200, `CSV devolvió ${resp.status()}`);
      assert(/^text\/csv/.test(tipo), `Content-Type inesperado: ${tipo}`);
      assert(/charset=utf-8/i.test(tipo), `Falta charset=utf-8 en el Content-Type: ${tipo}`);
      assert(cuerpo[0] === 0xef && cuerpo[1] === 0xbb && cuerpo[2] === 0xbf, "El CSV no empieza con BOM UTF-8");
      const texto = cuerpo.toString("utf8");
      assert(/user_created/.test(texto), "El CSV no contiene user_created");
      assert(/password_reset_admin/.test(texto), "El CSV no contiene password_reset_admin");
      assert(new RegExp(USER.username).test(texto), `El CSV no menciona ${USER.username}`);
      fs.writeFileSync(path.join(OUT, "auditoria-e2e.csv"), cuerpo);
      reg.evidencia.push("docs/qa-visual/e2e-auth/auditoria-e2e.csv");
      return `${vista.filas} eventos en pantalla con las 6 acciones del flujo; CSV 200 ${tipo} con BOM (${cuerpo.length} bytes)`;
    });

    /* 8 — Recuperación por correo */
    await paso(
      "8",
      "Recuperación por correo: enlace del .eml, contraseña nueva y acceso",
      async (reg) => {
        await logout(page, reg);
        await ir(page, "/auth/recuperar", reg);
        await page.fill("#campo-identificador", USER.email);
        await enviar(page, reg, () => page.click('form[action="/auth/recuperar"] button[type="submit"]'));
        const aviso = await page.evaluate(() => {
          const b = document.querySelector(".banner");
          return b ? b.textContent.replace(/\s+/g, " ").trim() : "";
        });
        assert(aviso.length > 0, "Sin respuesta tras solicitar la recuperación");
        reg.evidencia.push(await captura(page, "recuperar-solicitud"));

        const { archivo, enlace } = leerUltimoEnlace(
          /https?:\/\/[^\s"'<>]+\/auth\/restablecer\?token=([A-Za-z0-9_%.-]{20,})/
        );
        const ruta = enlace.startsWith(BASE)
          ? enlace.slice(BASE.length)
          : new URL(enlace).pathname + new URL(enlace).search;
        await ir(page, ruta, reg);
        assert(await page.locator("#campo-nueva").count(),
          "El enlace del correo no abre el formulario de contraseña");
        await page.fill("#campo-nueva", USER.passwordNueva);
        await page.fill("#campo-confirmacion", USER.passwordNueva);
        await enviar(page, reg, () =>
          page.click('form[action="/auth/restablecer"] button[type="submit"]')
        );
        await page.waitForURL(/\/auth\/login/, { timeout: 15000 });
        reg.evidencia.push(await captura(page, "restablecer-aplicado"));

        await loginPassword(page, reg, USER.username, USER.passwordNueva);
        await page.waitForLoadState("load").catch(() => {});
        let destino = new URL(page.url()).pathname;
        let sufijo = "";
        if (destino === "/auth/mfa") {
          // Política «TOTP o correo» sin autenticador: el sistema exige el
          // código enviado al correo. Se lee del mismo backend `file`.
          const { archivo: emlCodigo, codigo } = leerCodigoCorreo();
          const metodo = await page.locator('input[name="metodo"]').first().inputValue();
          assert(metodo === "email", `Segundo factor inesperado tras el reset (metodo=${metodo})`);
          await page.fill("#campo-codigo", codigo);
          await enviar(page, reg, () => page.click('form[action="/auth/mfa"] button[type="submit"]'));
          destino = new URL(page.url()).pathname;
          sufijo = `; segundo factor por correo (${emlCodigo}) aceptado`;
          assert(destino === "/", `El código del correo ${emlCodigo} no abrió sesión (${destino})`);
        }
        assert(destino === "/", `El acceso con la contraseña nueva terminó en ${destino}`);
        reg.evidencia.push(await captura(page, "acceso-password-nueva"));
        return `solicitud neutra → ${archivo} → formulario válido → 303 /auth/login?ok=password_reset → login de ${USER.username} con la contraseña nueva → / 200${sufijo}`;
      },
      { continuarSiFalla: true }
    );
    if (!pasos[pasos.length - 1].ok) {
      reporte.hallazgos.push({
        paso: 8,
        motivo: `La recuperación por correo no se pudo completar: ${pasos[pasos.length - 1].detalle}`,
      });
    }

    /* 9 — Permisos del admin: 403 en /cruces y menú sin Cruces */
    await paso("9", "Permisos: el admin recibe 403 en /cruces y el menú no muestra Cruces", async (reg) => {
      await asegurarSesionAdmin(page, reg);
      await ir(page, "/", reg);
      const menu = await page.evaluate(() => {
        const enlaces = Array.from(document.querySelectorAll('a[href="/cruces"]'));
        const visibles = enlaces.filter((a) => {
          const r = a.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        });
        return { total: enlaces.length, visibles: visibles.length };
      });
      assert(menu.visibles === 0, `El menú del admin muestra Cruces (${menu.visibles} enlaces visibles)`);
      reg.evidencia.push(await captura(page, "inicio-menu-admin"));

      const resp = await page.goto(`${BASE}/cruces`, { waitUntil: "networkidle", timeout: 45000 });
      const status = resp ? resp.status() : 0;
      reg.http.push({ url: "/cruces", status, metodo: "GET" });
      const cuerpo = await page.evaluate(() => (document.body.innerText || "").slice(0, 400));
      assert(status === 403, `Se esperaba 403 en /cruces y llegó ${status}`);
      assert(/No tiene permiso/i.test(cuerpo), `El 403 no muestra el mensaje de negocio: ${cuerpo.slice(0, 120)}`);
      reg.evidencia.push(await captura(page, "cruces-403-admin"));
      return "menú sin enlaces visibles a /cruces; GET /cruces → 403 «No tiene permiso para esta acción.»";
    });

    /* 10 — Cierre de sesión */
    await paso("10", "Cierre de sesión: / redirige al login", async (reg) => {
      await logout(page, reg);
      const resp = await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 45000 });
      const status = resp ? resp.status() : 0;
      const destino = new URL(page.url()).pathname;
      reg.http.push({ url: "/", status, metodo: "GET" });
      assert(destino === "/auth/login", `Sin sesión / terminó en ${destino} (${status})`);
      const cookies = (await contexto.cookies()).filter((c) => c.name === "eet_session");
      assert(cookies.length === 0, "Quedó cookie de sesión tras el logout");
      reg.evidencia.push(await captura(page, "logout-redirige-login"));
      return "/ 303 → /auth/login 200; sin cookie de sesión";
    });
  } finally {
    reporte.fin = ahora();
    reporte.ok = !fallo && pasos.every((p) => p.ok);
    if (fallo) {
      reporte.fallo = fallo;
      const ev = await captura(page, `fallo-paso-${fallo.id}`).catch(() => null);
      if (ev) reporte.evidenciaFallo = ev;
    }
    reporte.erroresConsola = erroresConsola;
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(reporte, null, 2), "utf8");
    fs.writeFileSync(path.join(OUT, "resumen.md"), resumenMarkdown(reporte), "utf8");
    await contexto.close();
    await browser.close();
  }

  console.log(`\n[e2e] ${reporte.ok ? "TODO OK" : "CON FALLOS"} — ${pasos.filter((p) => p.ok).length}/${pasos.length} pasos`);
  for (const p of pasos) {
    const http = p.http.map((h) => `${h.metodo} ${h.url} ${h.status}`).join(" · ");
    console.log(`  ${p.ok ? "OK " : "XX "} ${p.id}. ${p.titulo}${http ? ` — ${http}` : ""}`);
  }
  if (!reporte.ok) process.exitCode = 1;
}

function escapeCelda(texto) {
  return String(texto || "").replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function resumenMarkdown(rep) {
  const filas = rep.pasos
    .map((p) => {
      const http = p.http.map((h) => `${h.metodo} ${h.url} → ${h.status}`).join("<br>") || "—";
      const evid = p.evidencia.length
        ? p.evidencia.map((e) => `[${path.basename(e)}](${path.basename(e)})`).join("<br>")
        : "—";
      return `| ${p.id} | ${p.titulo} | ${p.ok ? "OK" : "FALLO"} | ${escapeCelda(p.detalle)} | ${http} | ${evid} |`;
    })
    .join("\n");
  const hallazgos = rep.hallazgos.length
    ? rep.hallazgos.map((h) => `- **Paso ${h.paso}:** ${h.motivo}`).join("\n")
    : "- Ninguno.";
  const consola = rep.erroresConsola && rep.erroresConsola.length
    ? rep.erroresConsola.map((e) => `- ${e}`).join("\n")
    : "- Ninguno.";
  return `# E2E de autenticación — resultado de la corrida

**Fecha:** ${new Date(rep.inicio).toLocaleString("es-PY")} · **Base:** ${rep.base} · **Navegador:** ${rep.navegador}
**Resultado global:** ${rep.ok ? "todos los pasos OK" : "con fallos"} (${rep.pasos.filter((p) => p.ok).length}/${rep.pasos.length})

| # | Paso | Resultado | Detalle | HTTP | Evidencia |
|---|---|---|---|---|---|
${filas}

## Hallazgos

${hallazgos}

## Errores de consola

${consola}
`;
}

main().catch((err) => {
  console.error(`\n[e2e] ERROR: ${err.message}`);
  process.exitCode = 1;
});
