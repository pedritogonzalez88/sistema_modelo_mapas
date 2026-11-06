"""Crea el administrador inicial de producción con TOTP obligatorio.

Se ejecuta dentro del contenedor de la app, después de las migraciones:

    docker compose --env-file deploy/.env.production \
      -f deploy/docker-compose.prod.yml run --rm --no-deps app \
      .venv/bin/python scripts/bootstrap_admin.py

Solicita usuario, nombre, correo y contraseña de forma interactiva. También
acepta EET_BOOTSTRAP_ADMIN_USERNAME/NOMBRE/EMAIL/PASSWORD para automatización.
No imprime la contraseña.
"""
from __future__ import annotations

import getpass
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.security import hash_password, utcnow, validar_password
from app.models import Usuario

_PATRON_USUARIO = re.compile(r"^[A-Za-z0-9._-]{3,100}$")


def _dato(variable: str, etiqueta: str) -> str:
    valor = (os.getenv(variable) or "").strip()
    if valor:
        return valor
    return input(f"{etiqueta}: ").strip()


def main() -> int:
    username = _dato("EET_BOOTSTRAP_ADMIN_USERNAME", "Usuario administrador")
    nombre = _dato("EET_BOOTSTRAP_ADMIN_NOMBRE", "Nombre completo")
    email = _dato("EET_BOOTSTRAP_ADMIN_EMAIL", "Correo institucional")
    password = os.getenv("EET_BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass(
        "Contraseña inicial (no se muestra): ")

    if not _PATRON_USUARIO.fullmatch(username):
        print("El usuario debe tener 3–100 caracteres [A-Za-z0-9._-].")
        return 1
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        print("El correo no tiene un formato válido.")
        return 1
    if not nombre:
        print("El nombre es obligatorio.")
        return 1

    errores = validar_password(password, username, email)
    if errores:
        for error in errores:
            print(f"- {error}")
        return 1

    with SessionLocal() as db:
        existentes = int(db.scalar(select(func.count()).select_from(Usuario).where(
            Usuario.rol == "admin", Usuario.is_active.is_(True))) or 0)
        if existentes and os.getenv("EET_BOOTSTRAP_FORCE") != "1":
            print("Ya existe al menos un administrador activo. No se crea otro.")
            print("Use EET_BOOTSTRAP_FORCE=1 solo para un caso excepcional.")
            return 1
        if db.scalar(select(Usuario).where(
                func.lower(Usuario.username) == username.casefold())) is not None:
            print("Ese nombre de usuario ya existe.")
            return 1
        if db.scalar(select(Usuario).where(
                func.lower(Usuario.email) == email.casefold())) is not None:
            print("Ese correo ya está registrado.")
            return 1

        db.add(Usuario(
            nombre=nombre[:200],
            username=username,
            email=email,
            rol="admin",
            letra_terna=None,
            auth_provider="local",
            password_hash=hash_password(password),
            password_changed_at=utcnow(),
            must_change_password=True,
            is_active=True,
            # El primer login obliga a enrolar TOTP.
            mfa_policy="totp",
            mfa_enrolled_at=None,
            role_source="manual",
        ))
        db.commit()

    print(f"Administrador '{username}' creado.")
    print("El primer login exigirá cambiar la contraseña y enrolar TOTP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
