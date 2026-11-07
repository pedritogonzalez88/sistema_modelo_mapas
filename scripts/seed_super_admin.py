"""Siembra un administrador inicial ("super admin") para el arranque en producción.

A diferencia de ``bootstrap_admin.py`` (interactivo), este script es idempotente y
no bloquea: si faltan las variables de entorno o ya existe un administrador
activo, sale con código 0 sin tocar la base. Pensado para ejecutarse en el
contenedor antes de Uvicorn, o manualmente:

    EET_SUPER_ADMIN_USERNAME=admin \\
    EET_SUPER_ADMIN_NOMBRE="Administrador EET" \\
    EET_SUPER_ADMIN_EMAIL=admin@aep.gov.py \\
    EET_SUPER_ADMIN_PASSWORD='...' \\
      .venv/bin/python scripts/seed_super_admin.py

El primer login exigirá cambiar la contraseña y enrolar TOTP. No imprime la
contraseña.
"""
from __future__ import annotations

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


def _env(nombre: str) -> str:
    return (os.getenv(nombre) or "").strip()


def main() -> int:
    username = _env("EET_SUPER_ADMIN_USERNAME")
    nombre = _env("EET_SUPER_ADMIN_NOMBRE")
    email = _env("EET_SUPER_ADMIN_EMAIL")
    password = _env("EET_SUPER_ADMIN_PASSWORD")

    # Sin variables: nada que hacer (arranque normal sin seed).
    if not any((username, nombre, email, password)):
        print("seed_super_admin: sin variables EET_SUPER_ADMIN_*; se omite.")
        return 0

    if not username or not nombre or not email or not password:
        print("seed_super_admin: faltan variables (username/nombre/email/password).")
        return 1

    if not _PATRON_USUARIO.fullmatch(username):
        print("seed_super_admin: usuario inválido (3-100 chars [A-Za-z0-9._-]).")
        return 1
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        print("seed_super_admin: correo con formato inválido.")
        return 1

    errores = validar_password(password, username, email)
    if errores:
        for error in errores:
            print(f"seed_super_admin: - {error}")
        return 1

    force = os.getenv("EET_SUPER_ADMIN_FORCE") == "1"

    with SessionLocal() as db:
        existente = db.scalar(select(Usuario).where(
            func.lower(Usuario.username) == username.casefold()))
        if existente is not None:
            print(f"seed_super_admin: '{username}' ya existe; no se modifica.")
            return 0

        activos = int(db.scalar(select(func.count()).select_from(Usuario).where(
            Usuario.rol == "admin", Usuario.is_active.is_(True))) or 0)
        if activos and not force:
            print("seed_super_admin: ya existe un administrador activo; se omite. "
                  "Use EET_SUPER_ADMIN_FORCE=1 para forzar.")
            return 0

        if db.scalar(select(Usuario).where(
                func.lower(Usuario.email) == email.casefold())) is not None:
            print("seed_super_admin: ese correo ya está registrado.")
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
            mfa_policy="totp",
            mfa_enrolled_at=None,
            role_source="seed",
        ))
        db.commit()

    print(f"seed_super_admin: administrador '{username}' creado.")
    print("El primer login exigirá cambiar la contraseña y enrolar TOTP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
