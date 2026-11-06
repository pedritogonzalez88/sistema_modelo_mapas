#!/usr/bin/env python
"""Smoke test del staging con autenticación activa (A6.12).

Recorre el camino real de un operador contra el proxy con TLS:

1. ``http://…:8080`` responde **301** a HTTPS.
2. ``/salud`` responde 200.
3. ``/`` redirige a ``/auth/login``.
4. El formulario de login trae HSTS, CSP con nonce, ``nosniff`` y cookie CSRF.
5. ``/docs`` no queda accesible.
6. El POST de login (con CSRF) responde 303 y emite la cookie de sesión
   ``__Host-eet_session`` con ``Secure``, ``HttpOnly``, ``SameSite=lax``, ``Path=/``
   y **sin** ``Domain``.
7. La sesión sirve una página autenticada.
8. El logout cierra la sesión; después ``/`` vuelve a mandar al login.

Nunca imprime contraseñas ni tokens: de la cookie se muestra el nombre y los
atributos, con el valor enmascarado.

Uso::

    uv run python scripts/smoke-staging.py --base https://localhost:8443 --insecure \
      --usuario staging-admin --password '<clave del ensayo>'

Las credenciales también se aceptan por entorno (``EET_SMOKE_USER``,
``EET_SMOKE_PASSWORD``) para no dejarlas en el historial del shell. Sin
credenciales se omite el bloque de login y el resto se verifica igual.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

COOKIE_SESION = "__Host-eet_session"
COOKIE_SESION_DESARROLLO = "eet_session"
COOKIE_CSRF = "eet_csrf"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

_RE_CSRF = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


@dataclass
class Resultado:
    nombre: str
    estado: str
    detalle: str = ""


def _marca(estado: str) -> str:
    return {PASS: "[PASS]", FAIL: "[FAIL]", SKIP: "[SKIP]"}.get(estado, estado)


def _cookie_de(respuesta, nombre: str) -> str | None:
    """Devuelve la cabecera ``Set-Cookie`` de ``nombre`` (valor incluido)."""
    for cabecera in respuesta.headers.get_list("set-cookie"):
        if cabecera.split("=", 1)[0].strip() == nombre:
            return cabecera
    return None


def _enmascarar(cabecera: str) -> str:
    """Nombre y atributos de la cookie, con el valor oculto."""
    partes = [parte.strip() for parte in cabecera.split(";")]
    nombre = partes[0].split("=", 1)[0]
    atributos = "; ".join(partes[1:])
    return f"{nombre}=***" + (f"; {atributos}" if atributos else "")


def _csrf_de(html: str) -> str | None:
    coincidencia = _RE_CSRF.search(html)
    return coincidencia.group(1) if coincidencia else None


def _checks_http(cliente, http_base: str) -> list[Resultado]:
    """Redirección 80 -> 443 emitida por el proxy."""
    try:
        respuesta = cliente.get(f"{http_base}/")
    except Exception as exc:  # noqa: BLE001 - se informa el tipo, no la traza
        return [Resultado("http_redirect_301", FAIL,
                          f"{http_base} inaccesible ({type(exc).__name__})")]
    destino = respuesta.headers.get("location", "")
    ok = respuesta.status_code == 301 and destino.lower().startswith("https://")
    return [Resultado(
        "http_redirect_301", PASS if ok else FAIL,
        f"GET {http_base}/ -> {respuesta.status_code} {destino or '(sin Location)'}")]


def _checks(cliente, base: str, usuario: str | None,
            password: str | None) -> list[Resultado]:
    res: list[Resultado] = []

    # --- Salud ---------------------------------------------------------------
    salud = cliente.get(f"{base}/salud")
    res.append(Resultado(
        "salud_200", PASS if salud.status_code == 200 else FAIL,
        f"GET /salud -> {salud.status_code}"))

    # --- Redirección al login ------------------------------------------------
    portada = cliente.get(f"{base}/")
    destino = portada.headers.get("location", "")
    ok_redireccion = portada.status_code in (302, 303) and "/auth/login" in destino
    res.append(Resultado(
        "raiz_redirige_login", PASS if ok_redireccion else FAIL,
        f"GET / -> {portada.status_code} {destino or '(sin Location)'}"))

    # --- Formulario de login y cabeceras -------------------------------------
    login = cliente.get(f"{base}/auth/login")
    res.append(Resultado(
        "login_200", PASS if login.status_code == 200 else FAIL,
        f"GET /auth/login -> {login.status_code}"))

    hsts = login.headers.get("strict-transport-security", "")
    res.append(Resultado(
        "hsts", PASS if hsts else FAIL,
        f"Strict-Transport-Security: {hsts or '(ausente)'}"))

    csp = login.headers.get("content-security-policy", "")
    coincidencia = re.search(r"nonce-([A-Za-z0-9_\-]+)", csp)
    nonce_html = coincidencia.group(1) if coincidencia else None
    if nonce_html:
        # El nonce del cuerpo debe coincidir con el de la cabecera.
        mismo_nonce = nonce_html in login.text
        res.append(Resultado(
            "csp_con_nonce",
            PASS if mismo_nonce else FAIL,
            "CSP con nonce y mismo valor en el HTML"
            if mismo_nonce else "CSP con nonce pero el HTML no lo reutiliza"))
    else:
        res.append(Resultado("csp_con_nonce", FAIL, "CSP ausente o sin nonce"))

    nosniff = login.headers.get("x-content-type-options", "").lower()
    res.append(Resultado(
        "nosniff", PASS if nosniff == "nosniff" else FAIL,
        f"X-Content-Type-Options: {nosniff or '(ausente)'}"))

    csrf_cookie = _cookie_de(login, COOKIE_CSRF)
    if csrf_cookie is None and cliente.cookies.get(COOKIE_CSRF):
        # La cookie puede venir de una respuesta anterior (la redirección de
        # `/` ya la emite); lo que importa es que el navegador la tenga.
        csrf_cookie = f"{COOKIE_CSRF}=***"
    campo_csrf = _csrf_de(login.text) is not None
    res.append(Resultado(
        "cookie_csrf", PASS if csrf_cookie and campo_csrf else FAIL,
        f"cookie {COOKIE_CSRF} "
        + ("presente" if csrf_cookie else "ausente")
        + " · campo csrf_token "
        + ("presente" if campo_csrf else "ausente")))

    # --- Documentación oculta ------------------------------------------------
    docs = cliente.get(f"{base}/docs")
    res.append(Resultado(
        "docs_no_expuesto", PASS if docs.status_code != 200 else FAIL,
        f"GET /docs -> {docs.status_code} (no debe ser 200)"))

    # --- Login ---------------------------------------------------------------
    if not usuario or not password:
        res.append(Resultado(
            "login_post", SKIP,
            "sin credenciales (--usuario/--password o EET_SMOKE_*): bloque omitido"))
        return res

    csrf = cliente.cookies.get(COOKIE_CSRF) or _csrf_de(login.text) or ""
    entrada = cliente.post(
        f"{base}/auth/login",
        data={"login": usuario, "password": password,
              "next": "/", "csrf_token": csrf},
    )
    res.append(Resultado(
        "login_post", PASS if entrada.status_code == 303 else FAIL,
        f"POST /auth/login -> {entrada.status_code} "
        f"{entrada.headers.get('location', '')}"))

    cabecera_sesion = _cookie_de(entrada, COOKIE_SESION)
    if cabecera_sesion is None:
        res.append(Resultado(
            "cookie_sesion", FAIL,
            f"no se emitió {COOKIE_SESION}"))
    else:
        atributos = cabecera_sesion.casefold()
        problemas = []
        if "secure" not in atributos:
            problemas.append("falta Secure")
        if "httponly" not in atributos:
            problemas.append("falta HttpOnly")
        if "samesite=lax" not in atributos:
            problemas.append("falta SameSite=lax")
        if "domain=" in atributos:
            problemas.append("tiene Domain")
        if "path=/" not in atributos:
            problemas.append("falta Path=/")
        res.append(Resultado(
            "cookie_sesion", PASS if not problemas else FAIL,
            _enmascarar(cabecera_sesion)
            + ("" if not problemas else " · " + ", ".join(problemas))))
        desarrollo = _cookie_de(entrada, COOKIE_SESION_DESARROLLO)
        res.append(Resultado(
            "cookie_sin_prefijo_host", PASS if desarrollo is None else FAIL,
            f"no se emite {COOKIE_SESION_DESARROLLO} sin prefijo `__Host-`"
            if desarrollo is None else f"se emitió {COOKIE_SESION_DESARROLLO}"))

    # --- Sesión autenticada --------------------------------------------------
    protegida = cliente.get(f"{base}/auth/seguridad")
    res.append(Resultado(
        "sesion_autenticada", PASS if protegida.status_code == 200 else FAIL,
        f"GET /auth/seguridad -> {protegida.status_code}"))

    # --- Logout --------------------------------------------------------------
    csrf_sesion = _csrf_de(protegida.text) or ""
    salida = cliente.post(f"{base}/auth/logout",
                          data={"csrf_token": csrf_sesion})
    res.append(Resultado(
        "logout", PASS if salida.status_code == 303 else FAIL,
        f"POST /auth/logout -> {salida.status_code} "
        f"{salida.headers.get('location', '')}"))

    despues = cliente.get(f"{base}/")
    cerrada = despues.status_code in (302, 303) and \
        "/auth/login" in despues.headers.get("location", "")
    res.append(Resultado(
        "sesion_cerrada", PASS if cerrada else FAIL,
        f"GET / tras logout -> {despues.status_code} "
        f"{despues.headers.get('location', '')}"))
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smoke-staging.py",
        description="Smoke test HTTPS del staging con autenticación activa.",
    )
    parser.add_argument("--base", default="https://localhost:8443",
                        help="Base HTTPS publicada por el proxy.")
    parser.add_argument("--http", dest="http_base", default="http://localhost:8080",
                        help="Base HTTP del proxy (redirección 301). Vacío = omitir.")
    parser.add_argument("--insecure", action="store_true",
                        help="No valida el certificado TLS (solo ensayo local).")
    parser.add_argument("--usuario", default=os.environ.get("EET_SMOKE_USER"),
                        help="Usuario administrador del ensayo.")
    parser.add_argument("--password", default=os.environ.get("EET_SMOKE_PASSWORD"),
                        help="Contraseña del ensayo (o variable EET_SMOKE_PASSWORD).")
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    if not base.casefold().startswith("https://"):
        print("--base debe ser una URL HTTPS.", file=sys.stderr)
        return 2

    import httpx

    instante = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Smoke del staging — {instante}")
    print(f"Proxy: {base} · TLS: {'sin validar (ensayo)' if args.insecure else 'validado'}")
    print()

    with httpx.Client(verify=not args.insecure, follow_redirects=False,
                      timeout=15.0,
                      headers={"user-agent": "eet-smoke/1.0"}) as cliente:
        resultados: list[Resultado] = []
        if args.http_base:
            resultados += _checks_http(cliente, args.http_base.rstrip("/"))
        resultados += _checks(cliente, base, args.usuario, args.password)

    ancho = max(len(item.nombre) for item in resultados) + 2
    for item in resultados:
        print(f"{_marca(item.estado):<7}{item.nombre:<{ancho}}{item.detalle}")

    fallos = [item for item in resultados if item.estado == FAIL]
    omitidos = [item for item in resultados if item.estado == SKIP]
    login_omitido = any(
        item.nombre == "login_post" and item.estado == SKIP
        for item in resultados)
    print()
    print(f"Resumen: {len(resultados) - len(fallos) - len(omitidos)} PASS · "
          f"{len(omitidos)} SKIP · {len(fallos)} FAIL")
    if fallos or login_omitido:
        print("Hay fallos o verificaciones omitidas: el entorno no está listo.")
        if login_omitido:
            print("Defina --usuario/--password o EET_SMOKE_* para verificar "
                  "la sesión real.")
        return 1
    print("Smoke en verde: TLS, proxy, cabeceras y cookie de sesión correctos.")
    return 0


if __name__ == "__main__":  # pragma: no cover - punto de entrada
    raise SystemExit(main())
