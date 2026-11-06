"""Smoke HTTP de autenticación contra un despliegue local.

Comprueba login real con CSRF, sesión, acceso de administrador y denegación de
un rol de consulta. No imprime contraseñas ni tokens.

Uso:
    python scripts/login_smoke.py \
        --admin-user demo.admin --admin-password ... \
        --consulta-user demo.consulta --consulta-password ...
"""
from __future__ import annotations

import argparse
import re
import sys

import httpx

PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def _login(cliente: httpx.Client, usuario: str, password: str) -> httpx.Response:
    formulario = cliente.get("/auth/login")
    if formulario.status_code != 200:
        raise AssertionError(
            f"/auth/login devolvió {formulario.status_code}, se esperaba 200")
    coincidencia = PATRON_CSRF.search(formulario.text)
    if coincidencia is None:
        raise AssertionError("No se encontró el token CSRF en /auth/login")
    return cliente.post("/auth/login", data={
        "login": usuario,
        "password": password,
        "csrf_token": coincidencia.group(1),
        "next": "/",
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--admin-user", default="demo.admin")
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--consulta-user", default="demo.consulta")
    parser.add_argument("--consulta-password", required=True)
    args = parser.parse_args()

    fallos: list[str] = []
    with httpx.Client(base_url=args.base_url, follow_redirects=False,
                      timeout=20) as cliente:
        try:
            respuesta = _login(cliente, args.admin_user, args.admin_password)
            if respuesta.status_code != 303:
                fallos.append(
                    f"{args.admin_user}: login {respuesta.status_code} "
                    f"(se esperaba 303)")
            else:
                portada = cliente.get("/")
                if portada.status_code != 200:
                    fallos.append(
                        f"{args.admin_user}: / -> {portada.status_code}")
                usuarios = cliente.get("/auth/usuarios")
                if usuarios.status_code != 200:
                    fallos.append(
                        f"{args.admin_user}: /auth/usuarios -> "
                        f"{usuarios.status_code} (se esperaba 200)")
                else:
                    print(f"OK {args.admin_user}: login y administración")
        except Exception as exc:
            fallos.append(f"{args.admin_user}: {exc}")

    with httpx.Client(base_url=args.base_url, follow_redirects=False,
                      timeout=20) as cliente:
        try:
            respuesta = _login(cliente, args.consulta_user,
                               args.consulta_password)
            if respuesta.status_code != 303:
                fallos.append(
                    f"{args.consulta_user}: login {respuesta.status_code} "
                    f"(se esperaba 303)")
            else:
                usuarios = cliente.get("/auth/usuarios")
                if usuarios.status_code != 403:
                    fallos.append(
                        f"{args.consulta_user}: /auth/usuarios -> "
                        f"{usuarios.status_code} (se esperaba 403)")
                else:
                    print(f"OK {args.consulta_user}: login y 403 esperado")
        except Exception as exc:
            fallos.append(f"{args.consulta_user}: {exc}")

    for fallo in fallos:
        print(f"FALLO: {fallo}", file=sys.stderr)
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
