"""Chequeos operativos de producción del Validador EET.

Sin dependencias de la aplicación: comprueba los endpoints públicos, la
readiness real (que incluye la base), el espacio del destino de backups, la
antigüedad del último backup cifrado y el vencimiento del certificado TLS.

Salida: una línea ``OK``/``FAIL`` por chequeo y código 1 si algo falla. Si se
definen ``EET_TELEGRAM_TOKEN`` y ``EET_TELEGRAM_CHAT_ID`` (o los argumentos
equivalentes), envía el resumen a Telegram cuando hay fallos.

Uso:
    .venv/bin/python scripts/healthcheck-production.py \
        --base-url https://eet.example.gov.py \
        --backup-dir /mnt/backup/eet \
        --cert deploy/certs/fullchain.pem
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    """Un 302 al login no debe contar como healthcheck exitoso."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_SinRedireccion)


def _agregar(checks: list, ok: bool, etiqueta: str, detalle: str = "") -> None:
    checks.append((ok, etiqueta, detalle))


def _http(url: str, timeout: float) -> tuple[int | None, str]:
    solicitud = urllib.request.Request(
        url, headers={"User-Agent": "EET-Healthcheck/1.0"})
    try:
        with _OPENER.open(solicitud, timeout=timeout) as respuesta:
            return respuesta.status, respuesta.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)


def _json(cuerpo: str) -> dict:
    try:
        datos = json.loads(cuerpo)
    except (TypeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _ultimo_backup(directorio: Path) -> Path | None:
    candidatos = []
    for patron in ("eet_*.dump", "eet_*.dump.enc"):
        candidatos.extend(directorio.glob(patron))
    if not candidatos:
        return None
    return max(candidatos, key=lambda ruta: ruta.stat().st_mtime)


def _dias_certificado(ruta: Path) -> int:
    from cryptography import x509

    certificado = x509.load_pem_x509_certificate(ruta.read_bytes())
    expira = certificado.not_valid_after_utc
    return (expira - datetime.now(timezone.utc)).days


def _enviar_telegram(token: str, chat_id: str, texto: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    datos = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": texto[:3900],
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=datos, timeout=15) as respuesta:
            return respuesta.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("EET_PUBLIC_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument(
        "--backup-dir", default=os.getenv("EET_BACKUP_DIR", "/mnt/backup/eet"))
    parser.add_argument("--max-backup-age-hours", type=float, default=26)
    parser.add_argument("--min-disk-free-mb", type=int, default=2048)
    parser.add_argument(
        "--cert", default=os.getenv("EET_TLS_CERT",
                                    str(RAIZ / "deploy" / "certs" / "fullchain.pem")))
    parser.add_argument("--min-cert-days", type=int, default=14)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--telegram-token", default=os.getenv("EET_TELEGRAM_TOKEN", ""))
    parser.add_argument("--telegram-chat-id",
                        default=os.getenv("EET_TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--skip-cert", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    checks: list[tuple[bool, str, str]] = []

    estado, cuerpo = _http(f"{base}/salud", args.timeout)
    datos = _json(cuerpo)
    _agregar(checks, estado == 200 and datos.get("estado") == "ok",
             "HTTP /salud", f"estado={estado} cuerpo={cuerpo[:80]}")

    estado, cuerpo = _http(f"{base}/ready", args.timeout)
    datos = _json(cuerpo)
    _agregar(checks,
             estado == 200 and datos.get("estado") == "listo"
             and datos.get("base") == "ok",
             "HTTP /ready (base de datos)",
             f"estado={estado} cuerpo={cuerpo[:80]}")

    destino = Path(args.backup_dir) if not args.skip_backup else RAIZ
    if not args.skip_backup and not destino.is_dir():
        _agregar(checks, False, "destino de backups", f"no existe {destino}")
    else:
        ultimo = _ultimo_backup(destino) if not args.skip_backup else None
        if not args.skip_backup:
            if ultimo is None:
                _agregar(checks, False, "backup reciente",
                         "no hay archivos eet_*.dump*")
            else:
                horas = (time.time() - ultimo.stat().st_mtime) / 3600
                _agregar(checks, horas <= args.max_backup_age_hours,
                         "backup reciente",
                         f"{ultimo.name} hace {horas:.1f} h "
                         f"(máximo {args.max_backup_age_hours:g} h)")
        try:
            uso = shutil.disk_usage(destino)
            libre_mb = uso.free // (1024 * 1024)
            _agregar(checks, libre_mb >= args.min_disk_free_mb,
                     "espacio en disco",
                     f"{libre_mb} MB libres (mínimo {args.min_disk_free_mb} MB)")
        except OSError as exc:
            _agregar(checks, False, "espacio en disco", str(exc))

    if not args.skip_cert:
        ruta_cert = Path(args.cert)
        if not ruta_cert.is_file():
            _agregar(checks, False, "certificado TLS", f"no existe {ruta_cert}")
        else:
            try:
                dias = _dias_certificado(ruta_cert)
                _agregar(checks, dias >= args.min_cert_days, "certificado TLS",
                         f"vence en {dias} días (mínimo {args.min_cert_days})")
            except Exception as exc:  # noqa: BLE001 - el detalle es local
                _agregar(checks, False, "certificado TLS", type(exc).__name__)

    fallos = [c for c in checks if not c[0]]
    for ok, etiqueta, detalle in checks:
        print(f"{'OK  ' if ok else 'FAIL'} {etiqueta}: {detalle}")

    if fallos:
        if args.telegram_token and args.telegram_chat_id:
            texto = "EET: fallo de healthcheck\n" + "\n".join(
                f"- {etiqueta}: {detalle}" for _, etiqueta, detalle in fallos)
            if not _enviar_telegram(args.telegram_token,
                                    args.telegram_chat_id, texto):
                print("ADVERTENCIA: no se pudo enviar la alerta a Telegram",
                      file=sys.stderr)
        print(f"HEALTHCHECK FALLÓ: {len(fallos)} problema(s).", file=sys.stderr)
        return 1

    print("HEALTHCHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
