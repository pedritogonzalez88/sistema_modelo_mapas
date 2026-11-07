#!/usr/bin/env python
"""Preflight de autenticación (A6.12).

Comprueba, **antes de dar por bueno un despliegue**, que la configuración real
y la base de datos cumplen el paquete de endurecimiento, y opcionalmente que el
servicio expuesto responde como se espera (salud, HSTS, CSP y redirección al
login).

Uso típico dentro del contenedor (la configuración ya está en el entorno)::

    docker compose -f docker-compose.staging.yml exec app \
      python scripts/preflight-auth.py

Uso desde el host contra el staging (la app vive en la red de Docker, así que
hay que indicar el puerto publicado de la base y del proxy)::

    uv run python scripts/preflight-auth.py \
      --env-file .env.staging \
      --database-url postgresql+psycopg://eet:eet@localhost:5434/eet \
      --url https://localhost:8443 --insecure

Salida legible o ``--json``. Sale con 1 si algún check crítico falla. **Nunca**
imprime contraseñas, claves ni tokens: solo nombres de variables, longitudes y
contadores.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

RAIZ = Path(__file__).resolve().parent.parent

# El script se ejecuta como archivo (``python scripts/preflight-auth.py``), así
# que Python agrega ``scripts/`` al path y no la raíz del repositorio.
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


@dataclass
class Resultado:
    """Resultado de una comprobación, sin datos sensibles."""

    nombre: str
    estado: str
    detalle: str = ""


# --- Utilidades -----------------------------------------------------------------
def _cargar_env(ruta: Path) -> None:
    """Carga ``KEY=VALUE`` de un env-file sin pisar el entorno ya definido."""
    if not ruta.exists():
        raise SystemExit(f"No existe el archivo de entorno: {ruta}")
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave, valor = limpia.split("=", 1)
        clave = clave.strip()
        valor = valor.strip()
        if not clave.startswith("EET_"):
            continue
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        os.environ.setdefault(clave, valor)


def _url_sin_secreto(url: str) -> str:
    """Muestra la URL de la base con la contraseña oculta."""
    partes = urlsplit(url)
    if "@" not in partes.netloc:
        return url
    credenciales, host = partes.netloc.rsplit("@", 1)
    usuario, _, _ = credenciales.partition(":")
    return urlunsplit(partes._replace(netloc=f"{usuario}:***@{host}"))


def _marca(estado: str) -> str:
    return {PASS: "[PASS]", FAIL: "[FAIL]", WARN: "[WARN]"}.get(estado, estado)


# --- Checks de configuración -----------------------------------------------------
def _checks_config(settings, *, url_https: bool) -> list[Resultado]:
    res: list[Resultado] = []

    res.append(Resultado(
        "auth_enabled", PASS if settings.auth_enabled else FAIL,
        f"EET_AUTH_ENABLED={str(settings.auth_enabled).lower()}"))
    res.append(Resultado(
        "environment", PASS if settings.environment in ("staging", "prod") else FAIL,
        f"EET_ENVIRONMENT={settings.environment}"))

    res.append(Resultado(
        "cookie_secure", PASS if settings.cookie_secure else FAIL,
        f"EET_COOKIE_SECURE={str(settings.cookie_secure).lower()}"))

    es_https = url_https or (settings.public_base_url or "").strip().casefold() \
        .startswith("https://")
    prefijo = (settings.session_cookie or "").startswith("__Host-")
    if es_https and not prefijo:
        res.append(Resultado(
            "session_cookie", FAIL,
            f"EET_SESSION_COOKIE={settings.session_cookie} sin prefijo '__Host-' "
            "sobre HTTPS"))
    elif not es_https and not prefijo:
        res.append(Resultado(
            "session_cookie", WARN,
            f"EET_SESSION_COOKIE={settings.session_cookie}: sin '__Host-' porque "
            "el despliegue no es HTTPS"))
    else:
        res.append(Resultado(
            "session_cookie", PASS, f"EET_SESSION_COOKIE={settings.session_cookie}"))

    res.append(Resultado(
        "public_base_url",
        PASS if es_https else FAIL,
        f"EET_PUBLIC_BASE_URL={settings.public_base_url}"))

    res.append(Resultado(
        "expose_docs", PASS if not settings.expose_docs else FAIL,
        f"EET_EXPOSE_DOCS={str(settings.expose_docs).lower()}"))

    largo_secreto = len((settings.secret_key or "").strip())
    res.append(Resultado(
        "secret_key", PASS if largo_secreto >= 32 else FAIL,
        f"EET_SECRET_KEY: {largo_secreto} caracteres (mínimo 32)"))

    largo_pepper = len((settings.auth_pepper or "").strip())
    res.append(Resultado(
        "auth_pepper", PASS if largo_pepper >= 16 else FAIL,
        f"EET_AUTH_PEPPER: {largo_pepper} caracteres (mínimo 16)"))

    backend = (settings.email_backend or "").strip().casefold()
    host_smtp = bool((settings.smtp_host or "").strip())
    if settings.environment == "prod":
        if backend == "smtp" and host_smtp:
            res.append(Resultado("email_backend", PASS, "smtp con host definido"))
        else:
            res.append(Resultado(
                "email_backend", FAIL,
                f"EET_EMAIL_BACKEND={backend or '(vacío)'} en prod; se exige "
                "smtp con EET_SMTP_HOST"))
    elif backend == "smtp" and host_smtp:
        res.append(Resultado("email_backend", PASS, "smtp con host definido"))
    else:
        res.append(Resultado(
            "email_backend", WARN,
            f"EET_EMAIL_BACKEND={backend or '(vacío)'}: no entrega correo real "
            "(aceptable solo fuera de prod)"))

    proxy = [crudo.strip() for crudo in (settings.trusted_proxy_ips or "").split(",")
             if crudo.strip()]
    if proxy:
        try:
            redes = [str(red) for red in settings.trusted_proxy_networks]
            res.append(Resultado(
                "trusted_proxy_ips", PASS, "CIDR de confianza: " + ", ".join(redes)))
        except ValueError as exc:
            res.append(Resultado("trusted_proxy_ips", FAIL, str(exc)))
    else:
        res.append(Resultado(
            "trusted_proxy_ips", WARN,
            "EET_TRUSTED_PROXY_IPS vacío: se ignoran X-Forwarded-Proto/For"))

    try:
        redes_internas = [str(red) for red in settings.internal_networks]
    except ValueError as exc:
        res.append(Resultado("internal_cidrs", FAIL, str(exc)))
    else:
        res.append(Resultado(
            "internal_cidrs", PASS, "CIDR internos: " + ", ".join(redes_internas)
            if redes_internas else "CIDR internos: (vacío)"))
    if settings.admin_internal_only:
        res.append(Resultado(
            "admin_internal_only", PASS, "administración restringida por CIDR"))
    else:
        res.append(Resultado(
            "admin_internal_only", WARN,
            "EET_ADMIN_INTERNAL_ONLY=false: administración abierta fuera de la "
            "red interna"))

    try:
        claves = settings.fernet_key_list
    except ValueError as exc:
        res.append(Resultado("fernet_keys", FAIL, str(exc)))
    else:
        identificadores = ", ".join(identificador for identificador, _ in claves)
        res.append(Resultado(
            "fernet_keys", PASS if claves else WARN,
            f"claves disponibles: {identificadores}" if claves
            else "sin claves de cifrado: los secretos TOTP no se podrán leer"))
    return res


# --- Checks de base de datos -----------------------------------------------------
def _checks_bd() -> list[Resultado]:
    from sqlalchemy import func, select, text
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.database import SessionLocal, engine
    from app.models import MfaTotp, Usuario

    res: list[Resultado] = []
    try:
        with engine.connect() as conexion:
            conexion.execute(text("select 1"))
    except SQLAlchemyError as exc:
        # Sin base no tiene sentido seguir: se informa el tipo de error, no la
        # cadena de conexión (que lleva credenciales).
        return [Resultado("base_datos", FAIL,
                          f"no se pudo conectar ({type(exc).__name__})")]

    res.append(Resultado("base_datos", PASS, "conexión y consulta simple OK"))

    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        guion = ScriptDirectory.from_config(Config(str(RAIZ / "alembic.ini")))
        cabezas = sorted(guion.get_heads())
        with engine.connect() as conexion:
            actual = MigrationContext.configure(conexion).get_current_revision()
        if not cabezas:
            res.append(Resultado("alembic", FAIL, "no hay revisiones en migrations/"))
        elif actual is None:
            res.append(Resultado("alembic", FAIL,
                                 f"sin versión aplicada; head={cabezas[-1]}"))
        elif actual in cabezas and len(cabezas) == 1:
            res.append(Resultado("alembic", PASS, f"revisión {actual} == head"))
        else:
            res.append(Resultado(
                "alembic", FAIL,
                f"revisión {actual} distinta de head ({', '.join(cabezas)})"))
    except Exception as exc:
        res.append(Resultado("alembic", FAIL, f"error al leer revisiones: "
                                              f"{type(exc).__name__}"))

    ldap = 0
    try:
        with SessionLocal() as db:
            admins = db.scalar(
                select(func.count()).select_from(Usuario).where(
                    Usuario.rol == "admin", Usuario.is_active.is_(True))) or 0
            res.append(Resultado(
                "admin_activo", PASS if admins else FAIL,
                f"{admins} administrador(es) activo(s)"))

            admins_totp = db.scalar(
                select(func.count(func.distinct(MfaTotp.usuario_id)))
                .select_from(MfaTotp)
                .join(Usuario, Usuario.id == MfaTotp.usuario_id)
                .where(Usuario.rol == "admin", Usuario.is_active.is_(True),
                       MfaTotp.confirmed_at.is_not(None))) or 0
            res.append(Resultado(
                "admin_totp", PASS if admins_totp else FAIL,
                f"{admins_totp} administrador(es) con TOTP confirmado"))

            ldap = db.scalar(
                select(func.count()).select_from(Usuario).where(
                    Usuario.auth_provider == "ldap")) or 0
    except SQLAlchemyError as exc:
        res.append(Resultado(
            "esquema", FAIL,
            f"no se pudieron consultar usuarios ({type(exc).__name__}); "
            "aplique las migraciones antes del preflight"))
        return res

    from app.core.config import settings

    if ldap and not settings.ldap_enabled:
        res.append(Resultado(
            "usuarios_ldap", WARN,
            f"{ldap} cuenta(s) LDAP con el directorio apagado: no podrán "
            "ingresar"))
    else:
        res.append(Resultado(
            "usuarios_ldap", PASS,
            f"{ldap} cuenta(s) LDAP · directorio "
            f"{'habilitado' if settings.ldap_enabled else 'apagado'}"))
    return res


# --- Checks HTTP -----------------------------------------------------------------
def _checks_http(base: str, insecure: bool) -> list[Resultado]:
    import httpx

    base = base.rstrip("/")
    res: list[Resultado] = []
    with httpx.Client(verify=not insecure, follow_redirects=False, timeout=10.0,
                      headers={"user-agent": "eet-preflight/1.0"}) as cliente:
        try:
            salud = cliente.get(f"{base}/salud")
        except httpx.HTTPError as exc:
            return [Resultado("http_salud", FAIL,
                              f"{base}/salud inaccesible ({type(exc).__name__})")]
        res.append(Resultado(
            "http_salud", PASS if salud.status_code == 200 else FAIL,
            f"GET /salud -> {salud.status_code}"))

        hsts = salud.headers.get("strict-transport-security", "")
        res.append(Resultado(
            "http_hsts", PASS if hsts else FAIL,
            f"Strict-Transport-Security: {hsts or '(ausente)'}"))

        csp = salud.headers.get("content-security-policy", "")
        res.append(Resultado(
            "http_csp", PASS if "nonce-" in csp else FAIL,
            "CSP con nonce por petición" if "nonce-" in csp
            else "CSP ausente o sin nonce"))

        porta = cliente.get(f"{base}/")
        destino = porta.headers.get("location", "")
        res.append(Resultado(
            "http_redireccion_login",
            PASS if porta.status_code in (303, 302) and "/auth/login" in destino
            else FAIL,
            f"GET / -> {porta.status_code} {destino or '(sin Location)'}"))
    return res


# --- Salida ----------------------------------------------------------------------
def _imprimir(res: list[Resultado]) -> None:
    ancho = max(len(item.nombre) for item in res) + 2
    for item in res:
        print(f"{_marca(item.estado):<7}{item.nombre:<{ancho}}{item.detalle}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preflight-auth.py",
        description="Verifica configuración, base de datos y servicio del "
                    "paquete de autenticación.",
    )
    parser.add_argument("--env-file", dest="env_file", default=None,
                        help="Carga EET_* de un env-file antes de leer Settings.")
    parser.add_argument("--database-url", dest="database_url", default=None,
                        help="Sobrescribe EET_DATABASE_URL (ensayo desde el host).")
    parser.add_argument("--url", default=None,
                        help="Base HTTPS a verificar (p. ej. https://localhost:8443).")
    parser.add_argument("--insecure", action="store_true",
                        help="No valida el certificado TLS (solo certificados "
                             "locales de ensayo).")
    parser.add_argument("--json", action="store_true",
                        help="Salida JSON para automatización.")
    args = parser.parse_args(argv)

    if args.env_file:
        _cargar_env(Path(args.env_file))
    if args.database_url:
        os.environ["EET_DATABASE_URL"] = args.database_url

    # Importar después de cargar el entorno: `Settings` valida al importarse.
    from app.core.config import settings

    if not args.json:
        instante = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        print(f"Preflight de autenticación — {instante}")
        print(f"Entorno: {settings.environment} · auth_enabled="
              f"{str(settings.auth_enabled).lower()} · base de datos: "
              f"{_url_sin_secreto(settings.database_url)}")
        print()

    url_https = bool(args.url and args.url.strip().casefold().startswith("https://"))
    resultados = _checks_config(settings, url_https=url_https)
    resultados += _checks_bd()
    if args.url:
        resultados += _checks_http(args.url, args.insecure)

    fallos = [item for item in resultados if item.estado == FAIL]
    avisos = [item for item in resultados if item.estado == WARN]
    exitoso = not fallos

    if args.json:
        print(json.dumps({
            "ok": exitoso,
            "resumen": {
                "total": len(resultados),
                "pass": len(resultados) - len(fallos) - len(avisos),
                "warn": len(avisos),
                "fail": len(fallos),
            },
            "checks": [asdict(item) for item in resultados],
        }, ensure_ascii=False, indent=2))
    else:
        _imprimir(resultados)
        print()
        print(f"Resumen: {len(resultados) - len(fallos) - len(avisos)} PASS · "
              f"{len(avisos)} WARN · {len(fallos)} FAIL")
        if fallos:
            print("Revise los FAIL antes de exponer el servicio.")
        elif avisos:
            print("Sin fallos: los WARN deben resolverse antes de producción.")
        else:
            print("Todos los checks en verde.")
    return 0 if exitoso else 1


if __name__ == "__main__":  # pragma: no cover - punto de entrada
    raise SystemExit(main())
