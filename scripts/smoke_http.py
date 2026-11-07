"""Smoke HTTP funcional contra un despliegue local o de staging.

Comprueba `/salud`, `/ready`, login real con CSRF, acceso a las páginas que
cada rol puede ver y denegación (403) de las que no, además del XLSX
consolidado. No imprime contraseñas ni tokens.

Uso::

    python scripts/smoke_http.py \
        --admin-user demo.admin --admin-password ... \
        --consulta-user demo.consulta --consulta-password ...
"""
from __future__ import annotations

import argparse
import os
import re

import httpx

PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')

ADMIN_PERMITIDAS = (
    "/", "/entregas", "/malla", "/mapa", "/auth/usuarios", "/auth/auditoria",
)
ADMIN_DENEGADAS = ("/cruces", "/qaqc", "/reemplazos", "/exportar")
CONSULTA_PERMITIDAS = (
    "/", "/entregas", "/cruces", "/qaqc", "/reemplazos", "/malla", "/mapa",
    "/exportar",
)
CONSULTA_DENEGADAS = ("/auth/usuarios", "/auth/auditoria")


def _login(cliente: httpx.Client, usuario: str, password: str) -> bool:
    formulario = cliente.get("/auth/login")
    if formulario.status_code != 200:
        print(f"FAIL {usuario}: /auth/login -> {formulario.status_code}")
        return False
    coincidencia = PATRON_CSRF.search(formulario.text)
    if coincidencia is None:
        print(f"FAIL {usuario}: sin token CSRF en /auth/login")
        return False
    respuesta = cliente.post("/auth/login", data={
        "login": usuario,
        "password": password,
        "csrf_token": coincidencia.group(1),
        "next": "/",
    })
    if respuesta.status_code != 303:
        print(f"FAIL {usuario}: login -> {respuesta.status_code}")
        return False
    print(f"OK   {usuario}: login")
    return True


def _comprobar(cliente: httpx.Client, ruta: str, esperado: int) -> bool:
    respuesta = cliente.get(ruta)
    ok = respuesta.status_code == esperado
    marca = "OK" if ok else "FAIL"
    print(f"{marca:4} {ruta:28} -> {respuesta.status_code} (esperado {esperado})")
    if (ok and esperado == 200
            and "text/html" not in respuesta.headers.get("content-type", "")):
        print(f"FAIL {ruta}: content-type no es HTML")
        return False
    return ok


def _recorrer(cliente: httpx.Client, usuario: str, password: str,
              permitidas: tuple[str, ...], denegadas: tuple[str, ...]) -> bool:
    if not _login(cliente, usuario, password):
        return False
    todo_ok = True
    for ruta in permitidas:
        if not _comprobar(cliente, ruta, 200):
            todo_ok = False
    for ruta in denegadas:
        if not _comprobar(cliente, ruta, 403):
            todo_ok = False
    return todo_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("EET_BASE_URL",
                                                       "http://localhost:8000"))
    parser.add_argument("--admin-user", default="demo.admin")
    parser.add_argument("--admin-password",
                        default=os.getenv("EET_DEMO_ADMIN_PASSWORD"))
    parser.add_argument("--consulta-user", default="demo.consulta")
    parser.add_argument("--consulta-password",
                        default=os.getenv("EET_DEMO_CONSULTA_PASSWORD"))
    args = parser.parse_args()

    if not args.admin_password or not args.consulta_password:
        print("Faltan credenciales: use --admin-password/--consulta-password o "
              "EET_DEMO_ADMIN_PASSWORD/EET_DEMO_CONSULTA_PASSWORD.")
        return 2

    fallos = 0
    with httpx.Client(base_url=args.base_url, follow_redirects=False,
                      timeout=30) as cliente:
        # Salud.
        for ruta in ("/salud", "/ready"):
            respuesta = cliente.get(ruta)
            if respuesta.status_code != 200:
                print(f"FAIL {ruta} -> {respuesta.status_code}")
                fallos += 1
            else:
                print(f"OK   {ruta:28} -> {respuesta.status_code}")

        # Admin.
        cliente.cookies.clear()
        if not _recorrer(cliente, args.admin_user, args.admin_password,
                         ADMIN_PERMITIDAS, ADMIN_DENEGADAS):
            fallos += 1

        # Consulta.
        cliente.cookies.clear()
        if not _recorrer(cliente, args.consulta_user, args.consulta_password,
                         CONSULTA_PERMITIDAS, CONSULTA_DENEGADAS):
            fallos += 1

        # Exportación XLSX.
        cliente.cookies.clear()
        if _login(cliente, args.consulta_user, args.consulta_password):
            respuesta = cliente.get("/exportar/consolidado.xlsx")
            if (respuesta.status_code == 200
                    and respuesta.content[:2] == b"PK"):
                print("OK   /exportar/consolidado.xlsx -> 200 (XLSX válido)")
            else:
                print("FAIL /exportar/consolidado.xlsx -> "
                      f"{respuesta.status_code}")
                fallos += 1

    if fallos:
        print(f"\nSMOKE_HTTP_FAIL: {fallos} bloque(s) con errores.")
        return 1
    print("\nSMOKE_HTTP_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
