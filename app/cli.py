"""Administración por línea de comandos (Fase A1).

Comandos:

    uv run python -m app.cli crear-admin --email ... --username ... --nombre ...
    uv run python -m app.cli crear-usuario --email ... --username ... --nombre ... --rol ...
    uv run python -m app.cli limpiar-sesiones --dias 30

Reglas:

- La contraseña se lee de ``EET_ADMIN_PASSWORD`` (``crear-admin``) o
  ``EET_USER_PASSWORD`` (``crear-usuario``) y, si no están definidas, se pide
  por terminal dos veces. Nunca hay contraseña por defecto y nunca se imprime.
- El comando no crea el esquema: en producción corresponde a ``alembic upgrade
  head`` (el contenedor lo hace al arrancar) y en desarrollo a ``init_db()``.
- Toda alta queda auditada con actor ``cli``.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import audit
from app.auth.service import limpiar_expiradas
from app.core.database import SessionLocal
from app.core.security import hash_password, validar_password
from app.models import Usuario

ROLES = ("admin", "supervision", "cargador", "asistente", "consulta")
LETRAS = ("A", "B", "C")
MFA_POLITICAS = ("disabled", "email", "totp", "any")
# Roles críticos: TOTP obligatorio por política (análisis §2 y §6.4).
MFA_POR_ROL = {"admin": "totp", "supervision": "totp"}


def _password_de_entorno(variable: str) -> str:
    return (os.environ.get(variable) or "").strip()


def _leer_password(variable: str) -> str:
    """Obtiene la contraseña del entorno o de la terminal (nunca por defecto)."""
    desde_entorno = _password_de_entorno(variable)
    if desde_entorno:
        return desde_entorno
    if not sys.stdin.isatty():
        raise SystemExit(
            f"No hay terminal interactiva: defina {variable} con la contraseña."
        )
    primera = getpass.getpass("Contraseña: ")
    segunda = getpass.getpass("Repita la contraseña: ")
    if primera != segunda:
        raise SystemExit("Las contraseñas no coinciden.")
    return primera


def _existe(db: Session, username: str, email: str) -> str | None:
    """Devuelve el campo duplicado, si lo hay (comparación en minúsculas)."""
    fila = db.scalar(
        select(Usuario).where(or_(
            func.lower(Usuario.username) == username.casefold(),
            func.lower(Usuario.email) == email.casefold(),
        )).limit(1)
    )
    if fila is None:
        return None
    if (fila.username or "").casefold() == username.casefold():
        return "username"
    return "email"


def _crear_usuario(db: Session, *, nombre: str, username: str, email: str,
                   rol: str, letra: str | None, password: str,
                   must_change: bool,
                   mfa_policy: str | None = None) -> Usuario:
    if rol not in ROLES:
        raise SystemExit(f"Rol inválido: {rol}. Use uno de: {', '.join(ROLES)}.")
    if letra and letra not in LETRAS:
        raise SystemExit("La letra de terna debe ser A, B o C.")
    if rol == "asistente" and not letra:
        raise SystemExit("El rol asistente exige --letra (A, B o C).")
    if rol != "asistente" and letra:
        raise SystemExit("Solo el rol asistente admite --letra.")
    if mfa_policy is not None and mfa_policy not in MFA_POLITICAS:
        raise SystemExit(
            f"Política 2FA inválida: {mfa_policy}. Use una de: "
            f"{', '.join(MFA_POLITICAS)}.")

    username = username.strip()
    email = email.strip()
    if not username or not email:
        raise SystemExit("Usuario y correo son obligatorios.")

    duplicado = _existe(db, username, email)
    if duplicado:
        raise SystemExit(f"Ya existe una cuenta con ese {duplicado}.")

    errores = validar_password(password, username, email)
    if errores:
        raise SystemExit("Contraseña rechazada:\n- " + "\n- ".join(errores))

    usuario = Usuario(
        nombre=nombre.strip(),
        username=username,
        email=email.casefold(),
        rol=rol,
        letra_terna=letra,
        auth_provider="local",
        password_hash=hash_password(password),
        password_changed_at=datetime.utcnow(),
        must_change_password=must_change,
        is_active=True,
        mfa_policy=mfa_policy or MFA_POR_ROL.get(rol, "any"),
        role_source="manual",
    )
    db.add(usuario)
    db.flush()
    audit.registrar(
        db, audit.ACTOR_CLI, "user_created", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"rol": rol, "username": username,
                 "must_change_password": must_change,
                 "mfa_policy": usuario.mfa_policy},
    )
    db.commit()
    return usuario


def _cmd_crear_admin(args: argparse.Namespace) -> int:
    password = _leer_password("EET_ADMIN_PASSWORD")
    with SessionLocal() as db:
        usuario = _crear_usuario(
            db, nombre=args.nombre, username=args.username, email=args.email,
            rol="admin", letra=None, password=password,
            must_change=bool(args.must_change),
        )
        # Se lee dentro de la sesión: tras `commit` los atributos quedan
        # expirados y fuera del bloque ya no hay conexión para refrescarlos.
        print(f"Administrador creado: id={usuario.id} usuario={usuario.username}")
    return 0


def _cmd_crear_usuario(args: argparse.Namespace) -> int:
    password = _leer_password("EET_USER_PASSWORD")
    with SessionLocal() as db:
        usuario = _crear_usuario(
            db, nombre=args.nombre, username=args.username, email=args.email,
            rol=args.rol, letra=args.letra, password=password,
            # Las cuentas creadas por administración deben estrenar contraseña.
            must_change=True,
            mfa_policy=args.mfa_policy,
        )
        print(f"Usuario creado: id={usuario.id} usuario={usuario.username} "
              f"rol={usuario.rol} mfa_policy={usuario.mfa_policy}")
    return 0


def _cmd_limpiar_sesiones(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        borradas = limpiar_expiradas(db, dias=args.dias)
    print(f"Sesiones eliminadas: {borradas}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Administración del Validador EET (Fase A1).",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    admin = sub.add_parser("crear-admin", help="Crea una cuenta administradora local.")
    admin.add_argument("--email", required=True)
    admin.add_argument("--username", required=True)
    admin.add_argument("--nombre", required=True)
    admin.add_argument("--must-change", action="store_true",
                       help="Exige cambiar la contraseña en el primer acceso.")
    admin.set_defaults(func=_cmd_crear_admin)

    usuario = sub.add_parser("crear-usuario", help="Crea una cuenta de operación.")
    usuario.add_argument("--email", required=True)
    usuario.add_argument("--username", required=True)
    usuario.add_argument("--nombre", required=True)
    usuario.add_argument("--rol", required=True, choices=ROLES)
    usuario.add_argument("--letra", choices=LETRAS,
                         help="Letra de terna; obligatoria para el rol asistente.")
    usuario.add_argument("--mfa-policy", choices=MFA_POLITICAS, default=None,
                         dest="mfa_policy",
                         help="Segundo factor exigido: disabled | email | totp | any. "
                              "Por defecto «any», salvo los roles críticos "
                              "(admin, supervisión), que quedan en «totp».")
    usuario.set_defaults(func=_cmd_crear_usuario)

    limpiar = sub.add_parser("limpiar-sesiones",
                             help="Borra sesiones muertas hace más de N días.")
    limpiar.add_argument("--dias", type=int, default=30)
    limpiar.set_defaults(func=_cmd_limpiar_sesiones)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.comando == "limpiar-sesiones" and args.dias < 0:
        raise SystemExit("--dias no puede ser negativo.")
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - punto de entrada
    raise SystemExit(main())
