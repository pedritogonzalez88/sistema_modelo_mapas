/* =========================================================================
   scripts/a11y-helpers.mjs
   Utilidades compartidas por los scripts de auditoría (no se ejecuta solo).
   ========================================================================= */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, "..");

export function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

export const BASE = (process.env.A11Y_BASE_URL || arg("base", "http://127.0.0.1:8017")).replace(/\/$/, "");
export const OUT = path.resolve(ROOT, arg("out", "docs/qa-visual"));

/* Código que se inyecta en cada página antes de cargar. */
export const PAGE_HELPERS = `
  window.__a11ySelector = function (el) {
    const parts = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 6) {
      let sel = node.tagName.toLowerCase();
      if (node.id) {
        parts.unshift(sel + '#' + CSS.escape(node.id));
        break;
      }
      const raw = typeof node.className === "string" ? node.className.trim() : "";
      if (raw) {
        const cls = raw.split(/\\s+/).slice(0, 2).map(function (c) { return "." + CSS.escape(c); }).join("");
        sel += cls;
      }
      const parent = node.parentElement;
      if (parent) {
        const sameTag = Array.prototype.filter.call(parent.children, function (c) {
          return c.tagName === node.tagName;
        });
        if (sameTag.length > 1) sel += ":nth-of-type(" + (sameTag.indexOf(node) + 1) + ")";
      }
      parts.unshift(sel);
      node = parent;
      depth++;
    }
    return parts.join(" > ");
  };

  window.__a11yScanAnchos = function (limit) {
    const iw = window.innerWidth;
    const de = document.documentElement;
    const se = document.scrollingElement || de;

    const prev = se.scrollLeft;
    se.scrollLeft = 999999;
    const scrollXmax = se.scrollLeft;
    se.scrollLeft = prev;

    const offenders = [];
    const nodes = document.body ? document.body.querySelectorAll("*") : [];
    for (let i = 0; i < nodes.length; i++) {
      const el = nodes[i];
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none" || cs.opacity === "0") continue;
      if (r.right <= iw + 1) continue;

      let inScroller = false;
      let a = el.parentElement;
      while (a && a !== document.body) {
        const acs = getComputedStyle(a);
        if (/(auto|scroll)/.test(acs.overflowX)) {
          inScroller = true;
          break;
        }
        a = a.parentElement;
      }
      offenders.push({
        selector: window.__a11ySelector(el),
        tag: el.tagName.toLowerCase(),
        width: Math.round(r.width),
        right: Math.round(r.right),
        inScroller: inScroller,
      });
      if (offenders.length >= 80) break;
    }

    const reales = offenders.filter(function (o) { return !o.inScroller; });
    const enScroller = offenders.filter(function (o) { return o.inScroller; });
    const elegidos = reales.concat(enScroller).slice(0, limit || 5);

    return {
      innerWidth: iw,
      scrollWidth: de.scrollWidth,
      clientWidth: de.clientWidth,
      documentScrollWidth: se.scrollWidth,
      viewportHeight: window.innerHeight,
      documentHeight: se.scrollHeight,
      desborda: de.scrollWidth > iw + 1,
      scrollHorizontalReal: scrollXmax > 0,
      scrollXmax: scrollXmax,
      totalOffenders: offenders.length,
      totalFueraDeScroller: reales.length,
      offenders: elegidos,
    };
  };

  window.__a11yFocus = function () {
    const el = document.activeElement;
    if (!el) return { tag: null };
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      tag: el.tagName ? el.tagName.toLowerCase() : String(el.nodeName),
      id: el.id || null,
      text: (el.textContent || "").trim().slice(0, 90),
      href: el.getAttribute ? el.getAttribute("href") : null,
      ariaLabel: el.getAttribute ? el.getAttribute("aria-label") : null,
      claseSrOnly: el.classList ? el.classList.contains("sr-only") : false,
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      clipPath: cs.clipPath,
      position: cs.position,
      display: cs.display,
      visibility: cs.visibility,
      visible:
        r.width > 2 &&
        r.height > 2 &&
        cs.visibility !== "hidden" &&
        cs.display !== "none" &&
        (cs.clipPath === "none" || cs.clipPath === "auto") &&
        Number(cs.opacity) !== 0,
      enPantalla: r.top >= -2 && r.left >= -2 && r.top < window.innerHeight && r.left < window.innerWidth,
    };
  };
`;

const CANDIDATES = [
  { label: "playwright channel=chrome", opts: { channel: "chrome" } },
  { label: "playwright channel=msedge", opts: { channel: "msedge" } },
  {
    label: "executablePath Chrome",
    opts: { executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" },
  },
  {
    label: "executablePath Edge (x86)",
    opts: { executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
  },
];

export async function launchBrowser() {
  const fallos = [];
  for (const c of CANDIDATES) {
    if (c.opts.executablePath && !fs.existsSync(c.opts.executablePath)) {
      fallos.push(`${c.label}: no existe la ruta`);
      continue;
    }
    try {
      const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"], ...c.opts });
      console.log(`[navegador] ${c.label} — ${browser.version()}`);
      return { browser, mode: c.label, version: browser.version(), fallos };
    } catch (err) {
      fallos.push(`${c.label}: ${String(err.message).split("\n")[0]}`);
    }
  }
  throw new Error(`No se pudo lanzar ningún navegador:\n  - ${fallos.join("\n  - ")}`);
}

export async function goto(page, url) {
  let response = null;
  try {
    response = await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
  } catch {
    try {
      response = await page.goto(url, { waitUntil: "load", timeout: 45000 });
    } catch (err2) {
      return { response, error: String(err2.message).split("\n")[0] };
    }
  }
  await page.waitForTimeout(250);
  await page.evaluate(() => (document.fonts ? document.fonts.ready : null)).catch(() => {});
  return { response, error: null };
}
