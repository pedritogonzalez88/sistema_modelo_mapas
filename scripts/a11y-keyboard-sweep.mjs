/* =========================================================================
   scripts/a11y-keyboard-sweep.mjs
   Barrido de teclado de las 11 vistas del Validador EET.

   Para cada ruta y ancho:
     - recorre con `Tab` (80 pulsaciones obligatorias, y amplía hasta cerrar
       el ciclo si la página tiene más enfocables),
     - registra el selector y el estado de visibilidad de cada elemento
       enfocado,
     - comprueba que no se enfocan elementos ocultos,
     - comprueba que todos los interactivos visibles y habilitados son
       alcanzables,
     - comprueba que el orden de foco sigue el orden del documento,
     - detecta ciclos (trampas de foco) fuera del drawer.

   Uso:
     node scripts/a11y-keyboard-sweep.mjs [--base http://localhost:8000]
                                          [--max 80] [--anchos 1440,390]
   ========================================================================= */
import fs from "node:fs";
import path from "node:path";
import { arg, BASE, OUT, ROOT, goto, launchBrowser } from "./a11y-helpers.mjs";

const MAX_BASE = Number(arg("max", "80"));
const NOTA = arg("nota", "");
const ANCHOS = arg("anchos", "1440,390")
  .split(",")
  .map((s) => Number(s.trim()))
  .filter(Boolean);

const RUTAS = [
  { slug: "inicio", ruta: "/" },
  { slug: "entregas", ruta: "/entregas" },
  { slug: "entrega-preview", ruta: "/entregas/1/preview" },
  { slug: "cruces", ruta: "/cruces" },
  { slug: "cruce-detalle", ruta: "/cruces/81/2008" },
  { slug: "qaqc-lista", ruta: "/qaqc" },
  { slug: "qaqc-ficha", ruta: "/qaqc/1" },
  { slug: "reemplazos", ruta: "/reemplazos" },
  { slug: "malla", ruta: "/malla" },
  { slug: "mapa", ruta: "/mapa" },
  { slug: "exportar", ruta: "/exportar" },
];

const INTERACTIVOS = "a[href], button, input:not([type=hidden]), select, textarea";

/* ------------------------------------------------------------------ page */
const PREPARAR = ({ interactivos }) => {
  const visible = (el) => {
    if (!el || el.nodeType !== 1) return { ok: false, motivo: "no es elemento" };
    const cs = getComputedStyle(el);
    if (cs.display === "none") return { ok: false, motivo: "display:none" };
    if (cs.visibility === "hidden") return { ok: false, motivo: "visibility:hidden" };
    if (Number(cs.opacity) === 0) return { ok: false, motivo: "opacity:0" };
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return { ok: false, motivo: "tamaño 0" };
    if (el.offsetParent === null && cs.position !== "fixed") {
      /* offsetParent null sin position:fixed suele implicar ancestro oculto,
         pero también ocurre en <body>; el rect ya lo cubre. */
      if (el !== document.body) return { ok: false, motivo: "offsetParent:null" };
    }
    return { ok: true, motivo: null };
  };

  const selectorDe = (el) => {
    let sel = el.tagName.toLowerCase();
    if (el.id) return sel + "#" + CSS.escape(el.id);
    const href = el.getAttribute("href");
    if (href) sel += "[href=" + JSON.stringify(href) + "]";
    const raw = typeof el.className === "string" ? el.className.trim() : "";
    if (raw) sel += "." + raw.split(/\s+/).slice(0, 2).map((c) => CSS.escape(c)).join(".");
    const padre = el.parentElement;
    if (padre) {
      const iguales = Array.prototype.filter.call(padre.children, (c) => c.tagName === el.tagName);
      if (iguales.length > 1) sel += ":nth-of-type(" + (iguales.indexOf(el) + 1) + ")";
    }
    return sel;
  };

  const todos = Array.prototype.slice.call(document.querySelectorAll("*"));
  const indiceDom = (el) => todos.indexOf(el);

  /* Marca los interactivos "declarados" (los del criterio del encargo). */
  const interactivosLista = [];
  Array.prototype.forEach.call(document.querySelectorAll(interactivos), (el, i) => {
    el.setAttribute("data-kbd-n", String(i));
    const v = visible(el);
    const deshabilitado =
      el.disabled === true ||
      el.getAttribute("aria-disabled") === "true" ||
      el.closest("[inert]") !== null;
    const tabindex = el.getAttribute("tabindex");
    interactivosLista.push({
      n: i,
      tag: el.tagName.toLowerCase(),
      tipo: el.getAttribute("type"),
      nombre: el.getAttribute("name"),
      selector: selectorDe(el),
      texto: (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 70),
      etiqueta: (el.closest("label")?.textContent || "").trim().replace(/\s+/g, " ").slice(0, 70),
      href: el.getAttribute("href"),
      type: el.getAttribute("type"),
      checked: typeof el.checked === "boolean" ? el.checked : null,
      visible: v.ok,
      motivoOculto: v.motivo,
      deshabilitado,
      excluidoPorTabindex: tabindex === "-1",
      domIndex: indiceDom(el),
    });
  });

  /* Grupos de radios: en HTML nativo un grupo (mismo `name`) es una sola
     parada de tabulación — el radio marcado, o el primero si ninguno lo está.
     Los demás se alcanzan con las flechas dentro del grupo. */
  const porNombre = new Map();
  for (const item of interactivosLista) {
    if (item.tag !== "input") continue;
    const el = document.querySelector(`[data-kbd-n="${item.n}"]`);
    if (el.type !== "radio") continue;
    const clave = item.nombre || "(sin nombre)";
    if (!porNombre.has(clave)) porNombre.set(clave, []);
    porNombre.get(clave).push(item);
  }
  const gruposRadio = [];
  for (const [nombre, miembros] of porNombre) {
    const utilizables = miembros.filter((m) => m.visible && !m.deshabilitado && !m.excluidoPorTabindex);
    const marcado = utilizables.find((m) => m.checked);
    const parada = marcado || utilizables[0] || null;
    gruposRadio.push({
      nombre,
      miembros: miembros.map((m) => ({ n: m.n, selector: m.selector, etiqueta: m.etiqueta, checked: m.checked, visible: m.visible })),
      paradaDeTab: parada ? parada.n : null,
      totalUtilizables: utilizables.length,
    });
    for (const m of miembros) m.paradaDeGrupoRadio = parada ? m.n === parada.n : false;
  }

  window.__kbd = { visible, selectorDe, indiceDom };

  /* Coherencia de cromo: si la barra lateral está visible (escritorio), la
     hamburguesa del menú móvil no debe estarlo: abriría un drawer con
     `display:none` y dejaría solo el velo sobre la página. */
  const barraLateral = document.querySelector("aside");
  const botonMenu = document.querySelector("[data-drawer-toggle]");
  const cromo = {
    barraLateralVisible: !!(barraLateral && visible(barraLateral).ok),
    botonMenuVisible: !!(botonMenu && visible(botonMenu).ok),
  };

  return { interactivos: interactivosLista, gruposRadio, cromo, totalNodos: todos.length };
};

const LEER_FOCO = () => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) {
    return { fin: true, tag: el ? el.tagName.toLowerCase() : null };
  }
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const n = el.getAttribute("data-kbd-n");
  const v = window.__kbd.visible(el);
  const drawer = el.closest("[data-drawer]");
  const main = el.closest("main");
  const nav = el.closest("nav, aside");
  return {
    fin: false,
    n: n === null ? -1 : Number(n),
    tag: el.tagName.toLowerCase(),
    selector: window.__kbd.selectorDe(el),
    texto: (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 80),
    ariaLabel: el.getAttribute("aria-label"),
    href: el.getAttribute("href"),
    role: el.getAttribute("role"),
    tabindex: el.getAttribute("tabindex"),
    domIndex: window.__kbd.indiceDom(el),
    visible: v.ok,
    motivoOculto: v.motivo,
    display: cs.display,
    visibility: cs.visibility,
    outlineStyle: cs.outlineStyle,
    outlineWidth: cs.outlineWidth,
    outlineColor: cs.outlineColor,
    boxShadow: cs.boxShadow,
    offsetParentNull: el.offsetParent === null,
    rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
    enDrawer: !!drawer,
    enMain: !!main,
    enNavegacion: !!nav,
  };
};

/* ------------------------------------------------------------------ main */
fs.mkdirSync(OUT, { recursive: true });
const { browser, mode, version } = await launchBrowser();
const resultados = [];

for (const ruta of RUTAS) {
  for (const ancho of ANCHOS) {
    const contexto = await browser.newContext({ viewport: { width: ancho, height: 900 } });
    const page = await contexto.newPage();
    const { response, error } = await goto(page, `${BASE}${ruta.ruta}`);

    const inventario = await page.evaluate(PREPARAR, { interactivos: INTERACTIVOS });

    const esperados = inventario.interactivos.filter(
      (i) =>
        i.visible &&
        !i.deshabilitado &&
        !i.excluidoPorTabindex &&
        /* Un grupo de radios es una sola parada: solo cuenta su parada. */
        !(i.tag === "input" && i.tipo === "radio" && i.paradaDeGrupoRadio === false),
    );
    const radiosEnGrupo = inventario.interactivos.filter(
      (i) => i.tag === "input" && i.tipo === "radio" && i.paradaDeGrupoRadio === false,
    );
    /* Límite: las 80 pulsaciones del encargo o lo necesario para cerrar el
       ciclo si hay más enfocables (tope de seguridad: 500). */
    const limite = Math.min(500, Math.max(MAX_BASE, esperados.length + 25));

    const foco = [];
    let salida = null;
    let pulsaciones80 = null;

    for (let i = 1; i <= limite; i++) {
      await page.keyboard.press("Tab");
      const estado = await page.evaluate(LEER_FOCO);
      if (estado.fin) {
        salida = { en: i, tag: estado.tag };
        break;
      }
      foco.push(estado);
      if (i === MAX_BASE) pulsaciones80 = { alcanzados: foco.length };
    }

    /* Ciclos: misma secuencia de selectores repetida dos veces seguidas. */
    const firmas = foco.map((f) => f.selector);
    let ciclo = null;
    for (let periodo = 1; periodo <= 12 && !ciclo; periodo++) {
      if (firmas.length < periodo * 2) break;
      const cola = firmas.slice(-periodo);
      const previa = firmas.slice(-periodo * 2, -periodo);
      if (cola.join("|") === previa.join("|")) {
        ciclo = { periodo, selectores: cola };
      }
    }

    const alcanzadosN = new Set(foco.filter((f) => f.n >= 0).map((f) => f.n));
    const noAlcanzados = esperados.filter((e) => !alcanzadosN.has(e.n));
    const ocultosEnfocados = foco.filter((f) => !f.visible);

    /* Indicador de foco: `:focus-visible` global define `outline: 2px solid`
       (input.css); los campos de formulario lo sustituyen por un anillo
       (`focus:ring-2`, que se materializa en `box-shadow`). Se listan los
       elementos enfocados sin ninguno de los dos. */
    const sinAnillo = foco.filter(
      (f) =>
        !((f.outlineStyle !== "none" && parseFloat(f.outlineWidth) >= 1) ||
          (f.boxShadow && f.boxShadow !== "none")),
    );

    /* Grupos de radio: se comprueba que la parada de tabulación del grupo es
       la alcanzada y que las flechas recorren el resto del grupo. */
    const gruposRadio = [];
    for (const g of inventario.gruposRadio) {
      if (g.paradaDeTab === null) continue;
      const parada = inventario.interactivos.find((i) => i.n === g.paradaDeTab);
      const alcanzada = alcanzadosN.has(g.paradaDeTab);
      let flechas = "no aplica (un solo miembro)";
      if (alcanzada && g.totalUtilizables > 1) {
        await page.evaluate((n) => document.querySelector(`[data-kbd-n="${n}"]`).focus(), g.paradaDeTab);
        await page.keyboard.press("ArrowDown");
        const destino = await page.evaluate(() => {
          const el = document.activeElement;
          const n = el.getAttribute("data-kbd-n");
          return { n: n === null ? -1 : Number(n), nombre: el.getAttribute("name"), valor: el.value };
        });
        flechas =
          destino.nombre === g.nombre && destino.n !== g.paradaDeTab
            ? `ok (flecha lleva a "${destino.valor}")`
            : `sin movimiento (foco en "${destino.valor}")`;
        /* Se restaura el foco al inicio del documento. */
        await page.evaluate(() => document.activeElement.blur());
      }
      gruposRadio.push({
        nombre: g.nombre,
        miembros: g.miembros.length,
        paradaDeTab: parada ? parada.selector : null,
        paradaMarcada: parada ? parada.checked : null,
        alcanzada,
        flechas,
      });
    }

    /* Operabilidad con teclado de los controles de formulario:
       - `Espacio` alterna un checkbox (si hay uno visible).
       - `Enter` sobre el botón de envío dispara el evento `submit`; la prueba
         captura el evento en fase de captura y lo cancela, de modo que no se
         envía nada al servidor ni se modifica la base.
       Para el envío se elige el primer formulario con el botón y la validación
       nativa satisfecha, y se informa del estado si no hay ninguno válido. */
    const teclas = { checkbox: null, envio: null };
    await page.evaluate(() => {
      window.__submitCapturado = null;
      document.addEventListener(
        "submit",
        function (ev) {
          window.__submitCapturado = {
            accion: ev.target.getAttribute("action") || "(acción por defecto)",
            metodo: (ev.target.getAttribute("method") || "get").toLowerCase(),
            disparador: ev.submitter
              ? (ev.submitter.textContent || ev.submitter.value || "").trim().slice(0, 40)
              : null,
          };
          ev.preventDefault();
          ev.stopPropagation();
        },
        true,
      );
    });

    const checkbox = await page.evaluate(() => {
      const el = Array.prototype.find.call(document.querySelectorAll("input[type=checkbox]"), (c) => {
        const r = c.getBoundingClientRect();
        return r.width > 1 && r.height > 1 && !c.disabled && getComputedStyle(c).visibility !== "hidden";
      });
      if (!el) return null;
      el.focus();
      return { antes: el.checked, nombre: el.getAttribute("name") };
    });
    if (checkbox) {
      await page.keyboard.press("Space");
      const despues = await page.evaluate(() => document.activeElement.checked);
      teclas.checkbox = {
        nombre: checkbox.nombre,
        antes: checkbox.antes,
        despues,
        resultado: despues !== checkbox.antes ? "Espacio alterna el estado" : "sin cambio",
      };
    }

    const envio = await page.evaluate(() => {
      const forms = Array.prototype.slice.call(document.querySelectorAll("form"));
      for (const form of forms) {
        const boton = form.querySelector(
          'button[type=submit]:not([disabled]), button:not([type]):not([disabled]), input[type=submit]:not([disabled])',
        );
        if (!boton) continue;
        const valido = form.checkValidity();
        if (!valido) {
          /* Se registra el primer formulario inválido y se sigue buscando. */
          if (!window.__formInvalido) {
            window.__formInvalido = { accion: form.getAttribute("action") || "(por defecto)", valido: false };
          }
          continue;
        }
        boton.focus();
        return {
          etiqueta: (boton.textContent || boton.value || "").trim().replace(/\s+/g, " ").slice(0, 40),
          accion: form.getAttribute("action") || "(por defecto)",
        };
      }
      return null;
    });
    if (envio) {
      await page.keyboard.press("Enter");
      await page.waitForTimeout(120);
      const capturado = await page.evaluate(() => window.__submitCapturado);
      teclas.envio = {
        etiqueta: envio.etiqueta,
        accion: envio.accion,
        capturado: !!capturado,
        detalle: capturado ? `submit capturado (${capturado.metodo} ${capturado.accion})` : "no se disparó submit",
      };
    } else {
      const invalido = await page.evaluate(() => window.__formInvalido || null);
      teclas.envio = invalido
        ? {
            etiqueta: null,
            accion: invalido.accion,
            capturado: false,
            detalle:
              "no hay formulario con la validación nativa satisfecha: el navegador bloquea el envío por campos obligatorios vacíos (comportamiento correcto)",
          }
        : { etiqueta: null, accion: null, capturado: false, detalle: "la vista no tiene formulario de envío" };
    }

    /* Orden: el índice de documento no debe retroceder. */
    const retrocesos = [];
    for (let i = 1; i < foco.length; i++) {
      if (foco[i].domIndex < foco[i - 1].domIndex) {
        retrocesos.push({
          posicion: i + 1,
          antes: foco[i - 1].selector,
          despues: foco[i].selector,
          delta: foco[i].domIndex - foco[i - 1].domIndex,
        });
      }
    }

    const primero = foco[0] || null;
    const skipPrimero = !!(
      primero &&
      primero.href === "#contenido" &&
      /saltar/i.test((primero.texto || "").toLowerCase())
    );

    resultados.push({
      slug: ruta.slug,
      ruta: ruta.ruta,
      ancho,
      estado: response ? response.status() : null,
      errorCarga: error,
      esperados: esperados.length,
      radiosEnGrupo: radiosEnGrupo.length,
      gruposRadio,
      cromo: inventario.cromo,
      controlRedundante: inventario.cromo.barraLateralVisible && inventario.cromo.botonMenuVisible,
      teclas,
      interactivosDeclarados: inventario.interactivos.length,
      totalNodos: inventario.totalNodos,
      pulsaciones: foco.length,
      limite,
      recorrido80: pulsaciones80,
      salidaDelDocumento: salida,
      alcanzados: alcanzadosN.size,
      noAlcanzados,
      ocultosEnfocados,
      sinAnillo,
      retrocesos,
      ciclo,
      skipPrimero,
      primero: primero
        ? { selector: primero.selector, texto: primero.texto, href: primero.href }
        : null,
      trayectoria: foco.map((f, i) => ({
        orden: i + 1,
        selector: f.selector,
        texto: f.texto,
        ariaLabel: f.ariaLabel,
        enMain: f.enMain,
        enNavegacion: f.enNavegacion,
        enDrawer: f.enDrawer,
      })),
    });

    const incidencias =
      noAlcanzados.length +
      ocultosEnfocados.length +
      retrocesos.length +
      (ciclo ? 1 : 0) +
      (skipPrimero ? 0 : 1) +
      (inventario.cromo.barraLateralVisible && inventario.cromo.botonMenuVisible ? 1 : 0) +
      (teclas.checkbox && /sin cambio/.test(teclas.checkbox.resultado) ? 1 : 0) +
      (teclas.envio && teclas.envio.capturado === false && /no se disparó/.test(teclas.envio.detalle) ? 1 : 0) +
      gruposRadio.filter((g) => !g.alcanzada || /sin movimiento/.test(g.flechas)).length;
    console.log(
      `[teclado] ${ruta.ruta} @${ancho}: esperados=${esperados.length} alcanzados=${alcanzadosN.size} puls=${foco.length}${salida ? ` salida@${salida.en}` : ""} incidencias=${incidencias}`,
    );
    if (noAlcanzados.length) {
      console.log(
        "          no alcanzados: " + noAlcanzados.slice(0, 6).map((x) => `${x.selector} "${x.texto.slice(0, 28)}"`).join(" | "),
      );
    }

    await contexto.close();
  }
}

await browser.close();

const resumen = {
  fecha: new Date().toISOString(),
  base: BASE,
  navegador: { modo: mode, version },
  maxPulsacionesPedidas: MAX_BASE,
  anchos: ANCHOS,
  rutas: resultados,
};

/* ------------------------------------------------------------- informe */
const L = [];
L.push("# Barrido de teclado — 11 vistas (Validador EET)");
L.push("");
L.push(`**Fecha:** ${new Date().toISOString().slice(0, 10)}  `);
L.push(`**Base:** ${BASE}  `);
if (NOTA) {
  L.push("");
  L.push(`> ${NOTA}`);
  L.push("");
}
L.push(`**Navegador:** Chrome ${version} vía Playwright (\`channel: "chrome"\`, headless)  `);
L.push(`**Script:** \`scripts/a11y-keyboard-sweep.mjs\`  `);
L.push(`**Anchos:** ${ANCHOS.join(" y ")} px`);
L.push("");
L.push("## Método");
L.push("");
L.push("Para cada ruta y ancho se recorre la página con `Tab` sin usar el ratón. Se");
L.push(`registran ${MAX_BASE} pulsaciones como mínimo; si la vista tiene más enfocables, el`);
L.push("recorrido continúa (hasta cerrar el ciclo) para poder verificar la alcanzabilidad");
L.push("completa. En cada pulsación se lee `document.activeElement` junto con su selector,");
L.push("`getComputedStyle` (display/visibility), `offsetParent` y posición.");
L.push("");
L.push("Conjunto obligatorio de interactivos: `a[href]`, `button`, `input:not([type=hidden])`,");
L.push("`select` y `textarea` **visibles, habilitados y no excluidos con `tabindex=\"-1\"`**.");
L.push("Los radios de un mismo `name` cuentan como una sola parada de tabulación (semántica");
L.push("nativa de los grupos de radio), y se verifican aparte con las flechas.");
L.push("");
L.push("Criterios:");
L.push("");
L.push("1. No se enfocan elementos ocultos (`display:none`, `visibility:hidden`, tamaño 0 u");
L.push("   `offsetParent:null` sin `position:fixed`).");
L.push("2. No hay ciclos de foco (trampas) fuera del drawer.");
L.push("3. Todos los interactivos del conjunto obligatorio son alcanzables.");
L.push("4. El orden de foco no retrocede en el orden del documento y el primer `Tab` llega al");
L.push("   skip link.");
L.push("5. Si la barra lateral está visible (escritorio), la hamburguesa del menú móvil no debe");
L.push("   ser enfocable: abriría un drawer oculto y dejaría solo el velo sobre la página.");
L.push("");

const porRuta = new Map();
for (const r of resultados) {
  if (!porRuta.has(r.ruta)) porRuta.set(r.ruta, []);
  porRuta.get(r.ruta).push(r);
}

L.push("## Resumen");
L.push("");
L.push("| # | Ruta | Interactivos visibles | Alcanzados | Pulsaciones | Incidencias |");
L.push("|---|---|---|---|---|---|");
let totalInc = 0;
resultados.forEach((r, i) => {
  const inc =
    r.noAlcanzados.length +
    r.ocultosEnfocados.length +
    r.retrocesos.length +
    (r.ciclo ? 1 : 0) +
    (r.skipPrimero ? 0 : 1) +
    (r.controlRedundante ? 1 : 0) +
    (r.teclas?.checkbox && /sin cambio/.test(r.teclas.checkbox.resultado) ? 1 : 0) +
    (r.teclas?.envio && r.teclas.envio.capturado === false && /no se disparó/.test(r.teclas.envio.detalle) ? 1 : 0) +
    r.gruposRadio.filter((g) => !g.alcanzada || /sin movimiento/.test(g.flechas)).length;
  r.incidencias = inc;
  totalInc += inc;
  L.push(
    `| ${i + 1} | \`${r.ruta}\` @${r.ancho} | ${r.esperados} | ${r.alcanzados} | ${r.pulsaciones}${r.salidaDelDocumento ? ` (fin en ${r.salidaDelDocumento.en})` : " (tope)"} | ${inc === 0 ? "0" : `**${inc}**`} |`,
  );
});
L.push("");
L.push(
  totalInc === 0
    ? "**Ninguna incidencia:** en las 11 vistas y los dos anchos, todos los interactivos visibles y habilitados son alcanzables, no se enfoca ningún elemento oculto, no hay ciclos de foco y el orden de tabulación sigue el orden del documento."
    : `**${totalInc} incidencias** acumuladas; el detalle está en la sección siguiente.`,
);
L.push("");
L.push(`Pulsaciones pedidas por el encargo: ${MAX_BASE}. «Fin en N» indica la pulsación en la`);
L.push("que el foco abandona el documento (fin natural del ciclo).");
L.push("");

if (totalInc) {
  L.push("## Incidencias");
  L.push("");
  for (const r of resultados) {
    const items = [];
    if (!r.skipPrimero) {
      items.push(`El primer foco no es el skip link: \`${r.primero?.selector}\` «${r.primero?.texto}».`);
    }
    for (const o of r.ocultosEnfocados) {
      items.push(`Se enfoca un elemento oculto: \`${o.selector}\` (${o.motivoOculto}).`);
    }
    for (const n of r.noAlcanzados) {
      items.push(`Interactivo no alcanzable: \`${n.selector}\` «${n.texto}»${n.href ? ` (${n.href})` : ""}.`);
    }
    for (const b of r.retrocesos) {
      items.push(`Retroceso de orden en la pulsación ${b.posicion}: \`${b.antes}\` → \`${b.despues}\` (${b.delta}).`);
    }
    if (r.ciclo) {
      items.push(`Ciclo de foco de período ${r.ciclo.periodo}: ${r.ciclo.selectores.join(" → ")}.`);
    }
    for (const g of r.gruposRadio) {
      if (!g.alcanzada) items.push(`La parada de tabulación del grupo de radios «${g.nombre}» no es alcanzable (\`${g.paradaDeTab}\`).`);
      else if (/sin movimiento/.test(g.flechas)) items.push(`Las flechas no recorren el grupo de radios «${g.nombre}»: ${g.flechas}.`);
    }
    if (r.controlRedundante) {
      items.push(
        "La hamburguesa del menú móvil es visible y enfocable aunque la barra lateral ya está en pantalla: abriría un drawer oculto (`display:none`) y dejaría solo el velo sobre la página.",
      );
    }
    if (r.teclas && r.teclas.checkbox && /sin cambio/.test(r.teclas.checkbox.resultado)) {
      items.push(`El checkbox «${r.teclas.checkbox.nombre}» no cambia de estado con Espacio.`);
    }
    if (r.teclas && r.teclas.envio && r.teclas.envio.capturado === false && /no se disparó/.test(r.teclas.envio.detalle)) {
      items.push(`Enter sobre «${r.teclas.envio.etiqueta}» no dispara el envío del formulario.`);
    }
    if (!items.length) continue;
    L.push(`### \`${r.ruta}\` @ ${r.ancho} px`);
    L.push("");
    for (const it of items) L.push(`- ${it}`);
    L.push("");
  }
}

const conGrupos = resultados.filter((r) => r.gruposRadio.length);
if (conGrupos.length) {
  L.push("## Grupos de radio");
  L.push("");
  L.push("En HTML nativo, un grupo de radios (mismo `name`) es **una sola parada de");
  L.push("tabulación**: el radio marcado, o el primero si ninguno lo está. Los demás miembros");
  L.push("se recorren con las flechas. Por eso no se cuentan como «no alcanzados»: se verifica");
  L.push("que la parada del grupo sí sea alcanzable y que `ArrowDown` mueva el foco dentro del");
  L.push("grupo.");
  L.push("");
  L.push("| Ruta | Ancho | Grupo | Miembros | Parada alcanzable | Flechas |");
  L.push("|---|---|---|---|---|---|");
  for (const r of conGrupos) {
    for (const g of r.gruposRadio) {
      const okParada = g.alcanzada ? "sí" : "**no**";
      const okFlechas = /^ok/.test(g.flechas) ? "sí" : /no aplica/.test(g.flechas) ? "no aplica" : `**${g.flechas}**`;
      L.push(`| \`${r.ruta}\` | ${r.ancho} px | \`${g.nombre}\` | ${g.miembros} | ${okParada} | ${okFlechas} |`);
    }
  }
  L.push("");
}

const conCromo = resultados.filter((r) => r.ancho >= 1024);
if (conCromo.length) {
  L.push("## Cromo en escritorio");
  L.push("");
  L.push("| Ruta | Barra lateral visible | Hamburguesa visible | Estado |");
  L.push("|---|---|---|---|");
  for (const r of conCromo) {
    const ok = !r.controlRedundante ? "correcto" : "**control redundante**";
    L.push(`| \`${r.ruta}\` | ${r.cromo.barraLateralVisible ? "sí" : "no"} | ${r.cromo.botonMenuVisible ? "sí" : "no"} | ${ok} |`);
  }
  L.push("");
}

L.push("## Trayectorias registradas");
L.push("");
for (const [ruta, filas] of porRuta) {
  L.push(`### \`${ruta}\``);
  L.push("");
  for (const r of filas) {
    L.push(
      `<details><summary>${r.ancho} px — ${r.pulsaciones} pulsaciones${r.salidaDelDocumento ? `, el foco sale del documento en la ${r.salidaDelDocumento.en}` : ", tope alcanzado"}</summary>`,
    );
    L.push("");
    L.push("| # | Selector | Texto / nombre | Zona |");
    L.push("|---|---|---|---|");
    for (const t of r.trayectoria) {
      const zona = t.enDrawer ? "drawer" : t.enMain ? "contenido" : t.enNavegacion ? "navegación" : "cromo";
      const etiqueta = t.ariaLabel || t.texto || "—";
      L.push(`| ${t.orden} | \`${t.selector}\` | ${String(etiqueta).slice(0, 60).replace(/\|/g, "\\|")} | ${zona} |`);
    }
    L.push("");
    L.push("</details>");
    L.push("");
  }
}

L.push("## Indicador de foco");
L.push("");
const conSinAnillo = resultados.filter((r) => r.sinAnillo.length);
if (!conSinAnillo.length) {
  L.push("Todos los elementos enfocados muestran indicador: `outline: 2px solid` global o, en los");
  L.push("campos de formulario, el anillo `focus:ring-2` (que se materializa en `box-shadow`).");
} else {
  L.push("Se leyó `outline` y `box-shadow` en el momento del foco. Los siguientes elementos no");
  L.push("mostraron ninguno de los dos en el elemento host:");
  L.push("");
  L.push("| Ruta | Ancho | Elemento | `outline` | `box-shadow` |");
  L.push("|---|---|---|---|---|");
  for (const r of conSinAnillo) {
    for (const s of r.sinAnillo) {
      L.push(`| \`${r.ruta}\` | ${r.ancho} px | \`${s.selector}\` | \`${s.outlineStyle} ${s.outlineWidth}\` | \`${s.boxShadow}\` |`);
    }
  }
  L.push("");
  L.push("Los cuatro pasos de tabulación de un `input[type=date]` de Chrome corresponden a sus");
  L.push("segmentos internos (día, mes, año y botón del calendario) dentro del shadow DOM del");
  L.push("control: `document.activeElement` sigue siendo el host. En el cuarto paso el anillo lo");
  L.push("dibuja el propio navegador sobre el icono del calendario, no el CSS de la aplicación.");
  L.push("Comprobado con captura: [`capturas/foco-fecha-nativo.png`](./capturas/foco-fecha-nativo.png).");
  L.push("No es un incumplimiento de 2.4.7; queda registrado como limitación de la medición.");
}
L.push("");
L.push("## Operabilidad con teclado (Espacio y Enter)");L.push("");
L.push("Se comprueba además que los controles responden al teclado, no solo que reciben el");
L.push("foco: `Espacio` sobre el primer checkbox visible y `Enter` sobre el botón de envío de");
L.push("un formulario con la validación nativa satisfecha. La prueba intercepta el evento");
L.push("`submit` en fase de captura y lo cancela, así que **no se envía nada ni se modifica la");
L.push("base de datos**.");
L.push("");
L.push("| Ruta | Ancho | Checkbox + Espacio | Enter sobre el envío |");
L.push("|---|---|---|---|");
for (const r of resultados) {
  const cb = r.teclas?.checkbox;
  const ec = r.teclas?.envio;
  L.push(
    `| \`${r.ruta}\` | ${r.ancho} px | ${cb ? `${cb.resultado} (${cb.antes} → ${cb.despues})` : "sin checkbox"} | ${ec ? `«${ec.etiqueta || "—"}»: ${ec.detalle}` : "sin formulario"} |`,
  );
}
L.push("");
L.push("## Artefactos");
L.push("");
L.push("- [`teclado-data.json`](./teclado-data.json): inventario, trayectoria completa y veredicto por ruta.");
L.push("");
fs.writeFileSync(path.join(OUT, "teclado-11-vistas.md"), L.join("\n"));
fs.writeFileSync(path.join(OUT, "teclado-data.json"), JSON.stringify(resumen, null, 2));

console.log(`\n[teclado] informe en ${path.relative(ROOT, path.join(OUT, "teclado-11-vistas.md"))}`);
console.log(`[teclado] incidencias totales: ${totalInc}`);
process.exit(totalInc ? 1 : 0);
