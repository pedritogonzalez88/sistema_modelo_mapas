#!/usr/bin/env python
"""Genera una CA local y un certificado de servidor para el staging (A6.12).

Produce, en ``certs/``:

- ``ca.crt``     — certificado de la CA (para confiar en el navegador/cliente).
- ``server.crt`` — certificado del servidor + cadena de la CA.
- ``server.key`` — clave privada del servidor (permisos restrictivos).

El certificado cubre ``localhost`` y ``127.0.0.1`` (SAN), dura 365 días y es
idempotente: si los tres archivos existen, son legibles y no están por vencer,
no se reescribe nada. ``--force`` obliga a regenerar.

Uso::

    uv run python scripts/gen-dev-cert.py
    uv run python scripts/gen-dev-cert.py --force --days 90

Los archivos **no** se versionan (``certs/`` está en ``.gitignore`` y en
``.dockerignore``). Este certificado es solo para ensayo local: en producción se
usa el certificado emitido por la CA institucional.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "deploy" / "certs"

#: Margen para considerar un certificado "todavía válido" al reutilizarlo.
_MARGEN_DIAS = 7


def _nombre(comun: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Validador EET (ensayo)"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Staging local"),
        x509.NameAttribute(NameOID.COMMON_NAME, comun),
    ])


def _clave_privada() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _ca(dias: int) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """CA autofirmada con `keyCertSign`; sirve para firmar el servidor."""
    clave = _clave_privada()
    nombre = _nombre("EET Staging Local CA")
    ahora = datetime.now(UTC)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=5))
        .not_valid_after(ahora + timedelta(days=dias))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(clave.public_key()),
            critical=False,
        )
        .sign(clave, hashes.SHA256())
    )
    return certificado, clave


def _servidor(ca: x509.Certificate, clave_ca: rsa.RSAPrivateKey,
              dias: int) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Certificado de servidor para `localhost` y `127.0.0.1`."""
    clave = _clave_privada()
    ahora = datetime.now(UTC)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(_nombre("localhost"))
        .issuer_name(ca.subject)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=5))
        .not_valid_after(ahora + timedelta(days=dias))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(clave_ca.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(clave.public_key()),
            critical=False,
        )
        .sign(clave_ca, hashes.SHA256())
    )
    return certificado, clave


def _pem_certificado(certificado: x509.Certificate) -> bytes:
    return certificado.public_bytes(serialization.Encoding.PEM)


def _pem_clave(clave: rsa.RSAPrivateKey) -> bytes:
    return clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _vigente(ruta: Path) -> bool:
    """``True`` si el PEM existe, se parsea y le quedan más de 7 días."""
    try:
        certificado = x509.load_pem_x509_certificate(ruta.read_bytes())
    except (OSError, ValueError):
        return False
    limite = datetime.now(UTC) + timedelta(days=_MARGEN_DIAS)
    return certificado.not_valid_after_utc > limite


def _permisos_clave(ruta: Path) -> None:
    """Restringe la clave privada al propietario (no-op real en Windows)."""
    try:
        os.chmod(ruta, 0o600)
    except OSError:  # pragma: no cover - Windows no implementa POSIX bits
        pass


def generar(destino: Path = DESTINO, dias: int = 365, forzar: bool = False) -> int:
    """Crea (o reutiliza) la CA y el certificado de servidor. Devuelve 0/1."""
    destino = Path(destino)
    rutas = {nombre: destino / nombre for nombre in ("ca.crt", "server.crt",
                                                     "server.key")}
    if not forzar and all(ruta.exists() for ruta in rutas.values()) \
            and _vigente(rutas["ca.crt"]) and _vigente(rutas["server.crt"]):
        print(f"Certificados vigentes en {destino}: no se regeneran "
              f"(use --force para reemplazarlos).")
        return 0

    destino.mkdir(parents=True, exist_ok=True)
    ca, clave_ca = _ca(dias)
    servidor, clave_servidor = _servidor(ca, clave_ca, dias)

    rutas["ca.crt"].write_bytes(_pem_certificado(ca))
    # `server.crt` lleva la cadena (hoja + CA): nginx la sirve tal cual.
    rutas["server.crt"].write_bytes(
        _pem_certificado(servidor) + _pem_certificado(ca))
    rutas["server.key"].write_bytes(_pem_clave(clave_servidor))
    _permisos_clave(rutas["server.key"])

    print(f"Certificados generados en {destino} (válidos {dias} días):")
    for nombre, ruta in rutas.items():
        print(f"  {nombre:<11} {ruta}")
    print("  SAN: DNS:localhost, IP:127.0.0.1")
    print()
    print("Para que el navegador confíe, importe ca.crt como autoridad raíz "
          "local (solo en el equipo de ensayo).")
    print("Estos archivos no se versionan: certs/ está en .gitignore.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen-dev-cert.py",
        description="Genera CA y certificado TLS locales para el staging.",
    )
    parser.add_argument("--destino", default=str(DESTINO),
                        help="Carpeta de salida (por defecto certs).")
    parser.add_argument("--dias", type=int, default=365,
                        help="Días de validez (por defecto 365).")
    parser.add_argument("--force", action="store_true",
                        help="Regenera aunque existan certificados vigentes.")
    args = parser.parse_args(argv)
    if args.dias < 1:
        print("--dias debe ser positivo.", file=sys.stderr)
        return 2
    return generar(Path(args.destino), dias=args.dias, forzar=args.force)


if __name__ == "__main__":  # pragma: no cover - punto de entrada
    raise SystemExit(main())
