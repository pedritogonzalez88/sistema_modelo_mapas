/* =========================================================================
   Validador EET · AEP — app.js
   Mejoras progresivas sin dependencias. Todo debe seguir funcionando sin JS:
   el marcado y el servidor nunca dependen de este archivo.
   ========================================================================= */
(function () {
  "use strict";

  /* ---------------------------------------------------------------------
     1) Drawer móvil: abrir/cerrar, Esc, overlay, devolución del foco y
     contención del foco (patrón APG para diálogos modales).
     Sin JS el marcado ya degrada: el drawer se muestra como bloque estático
     porque el CSS solo lo oculta bajo `.js`.
     --------------------------------------------------------------------- */
  var toggle = document.querySelector("[data-drawer-toggle]");
  var drawer = document.querySelector("[data-drawer]");
  var overlay = document.querySelector("[data-overlay]");
  var closeBtn = document.querySelector("[data-drawer-close]");
  var ultimoFoco = null;

  /* Elementos que pueden recibir foco con Tab dentro del drawer. */
  var ENFOCABLES = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");

  function drawerAbierto() {
    return !!drawer && drawer.getAttribute("data-open") === "true";
  }

  function enfocablesDelDrawer() {
    if (!drawer) return [];
    return Array.prototype.filter.call(drawer.querySelectorAll(ENFOCABLES), function (el) {
      return el.getClientRects().length > 0 && el.getAttribute("aria-hidden") !== "true";
    });
  }

  function abrirDrawer() {
    if (!drawer) return;
    ultimoFoco = document.activeElement;
    drawer.setAttribute("data-open", "true");
    if (overlay) overlay.setAttribute("data-open", "true");
    if (toggle) toggle.setAttribute("aria-expanded", "true");
    document.body.style.overflow = "hidden";
    if (closeBtn) closeBtn.focus();
  }

  function cerrarDrawer(devolverFoco) {
    if (!drawer) return;
    drawer.setAttribute("data-open", "false");
    if (overlay) overlay.setAttribute("data-open", "false");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";
    if (devolverFoco !== false) {
      var destino =
        ultimoFoco && ultimoFoco !== document.body && document.contains(ultimoFoco)
          ? ultimoFoco
          : toggle;
      if (destino && typeof destino.focus === "function") destino.focus();
    }
    ultimoFoco = null;
  }

  /* Tab y Shift+Tab ciclan dentro del drawer mientras está abierto: el foco
     nunca alcanza la página de fondo, que con `aria-modal="true"` queda
     presentada como inerte. Escape cierra y devuelve el foco al disparador. */
  function trampaDeFoco(ev) {
    if (!drawerAbierto()) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      cerrarDrawer();
      return;
    }
    if (ev.key !== "Tab") return;

    var lista = enfocablesDelDrawer();
    if (!lista.length) {
      ev.preventDefault();
      (closeBtn || drawer).focus();
      return;
    }

    var primero = lista[0];
    var ultimo = lista[lista.length - 1];
    var activo = document.activeElement;

    if (!drawer.contains(activo)) {
      ev.preventDefault();
      (ev.shiftKey ? ultimo : primero).focus();
      return;
    }
    if (ev.shiftKey && activo === primero) {
      ev.preventDefault();
      ultimo.focus();
    } else if (!ev.shiftKey && activo === ultimo) {
      ev.preventDefault();
      primero.focus();
    }
  }

  if (toggle && drawer) {
    toggle.addEventListener("click", function () {
      drawerAbierto() ? cerrarDrawer() : abrirDrawer();
    });
    if (closeBtn) closeBtn.addEventListener("click", function () { cerrarDrawer(); });
    if (overlay) overlay.addEventListener("click", function () { cerrarDrawer(); });
    drawer.addEventListener("click", function (ev) {
      if (ev.target.closest("a")) cerrarDrawer(false);
    });
    /* En captura: la contención se decide antes de que cualquier otro
       manejador de teclado pueda mover el foco. */
    document.addEventListener("keydown", trampaDeFoco, true);
    window.addEventListener("resize", function () {
      if (window.innerWidth >= 1024 && drawerAbierto()) cerrarDrawer(false);
    });
  }

  /* ---------------------------------------------------------------------
     2) Bloqueo de doble envío: deshabilita el botón y avisa "Guardando…".
     No se deshabilita un botón con `name` propio (los POST de transición
     dependen de su valor); en ese caso se marca como ocupado.
     --------------------------------------------------------------------- */
  function bloqueaEnvio(form, submitter) {
    if (form.getAttribute("data-submitting") === "1") return true;
    form.setAttribute("data-submitting", "1");
    form.setAttribute("aria-busy", "true");

    var boton = submitter || form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
    if (!boton) return false;

    boton.setAttribute("data-label-original", boton.textContent || boton.value || "");
    if (!boton.hasAttribute("name")) {
      boton.disabled = true;
    } else {
      boton.setAttribute("aria-disabled", "true");
      boton.classList.add("opacity-50");
    }
    if (boton.tagName === "INPUT") {
      boton.value = "Guardando…";
    } else {
      boton.textContent = "Guardando…";
    }

    /* Si el navegador no navegó (error de red, validación del servidor en
       otra pestaña, etc.) se restaura el botón para no dejarlo bloqueado. */
    window.setTimeout(function () {
      form.setAttribute("data-submitting", "0");
      form.removeAttribute("aria-busy");
      boton.disabled = false;
      boton.removeAttribute("aria-disabled");
      boton.classList.remove("opacity-50");
      var original = boton.getAttribute("data-label-original");
      if (original !== null) {
        if (boton.tagName === "INPUT") boton.value = original;
        else boton.textContent = original;
      }
    }, 15000);
    return false;
  }

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.getAttribute("data-no-lock") === "1") return;
    var otroEnvio = bloqueaEnvio(form, ev.submitter || null);
    if (otroEnvio) ev.preventDefault();
  });

  /* ---------------------------------------------------------------------
     3) Banners de feedback: cierre manual (el servidor los vuelve a pintar
     en la siguiente navegación, sin estado en cliente).
     --------------------------------------------------------------------- */
  document.addEventListener("click", function (ev) {
    var boton = ev.target.closest("[data-banner-close]");
    if (!boton) return;
    var banner = boton.closest(".banner");
    if (banner) banner.remove();
  });

  /* ---------------------------------------------------------------------
     4) "Cambios sin guardar": activa .is-visible en el indicador declarado
     con data-dirty-indicator dentro del formulario o de la página.
     --------------------------------------------------------------------- */
  function marcarSucio(ev) {
    var form = ev.target.closest ? ev.target.closest("form") : null;
    var indicador = (form && form.querySelector("[data-dirty-indicator]")) ||
      document.querySelector("[data-dirty-indicator]");
    if (!indicador) return;
    indicador.classList.add("is-visible");
    indicador.removeAttribute("hidden");
  }

  document.addEventListener("input", marcarSucio);
  document.addEventListener("change", marcarSucio);
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!(form instanceof HTMLFormElement)) return;
    var indicador = form.querySelector("[data-dirty-indicator]");
    if (indicador) indicador.classList.remove("is-visible");
  });

  /* ---------------------------------------------------------------------
     5) "Volver" de la página de error: usa el historial si existe y cae a
     la portada cuando no hay navegación previa. Sin JS el enlace ya
     apunta a "/", que es el fallback.
     --------------------------------------------------------------------- */
  document.addEventListener("click", function (ev) {
    var volver = ev.target.closest("[data-back]");
    if (!volver) return;
    if (window.history.length > 1) {
      ev.preventDefault();
      window.history.back();
    }
  });
})();
