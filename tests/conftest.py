"""Fixtures compartidas para la batería de pruebas de Evalua5."""
import uuid
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import delete


def _crear_libro(hojas):
    """Construye un libro .xlsx en memoria y devuelve su contenido en bytes.

    Cada hoja se especifica como un diccionario con las claves
    ``titulo``, ``encabezado`` y ``filas``.
    """
    libro = Workbook()
    libro.remove(libro.active)
    for hoja in hojas:
        ws = libro.create_sheet(title=hoja["titulo"])
        ws.append(hoja["encabezado"])
        for fila in hoja.get("filas", []):
            ws.append(fila)
    buffer = BytesIO()
    libro.save(buffer)
    libro.close()
    return buffer.getvalue()


@pytest.fixture
def crear_libro():
    """Factory que arma libros xlsx en memoria para el importador."""
    return _crear_libro


# --- Autenticación (Fase A1) ---------------------------------------------------
# Estas fixtures NO son autouse: las pruebas que no tocan autenticación siguen
# corriendo con la bandera apagada y el comportamiento anterior a la Fase A1.


@pytest.fixture
def auth_activa(monkeypatch):
    """Activa la bandera de autenticación con cookies no seguras.

    `cookie_secure=False` obliga a usar ``eet_session`` (los prefijos
    ``__Host-`` exigen Secure y el cliente de pruebas navega por http).
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "secret_key", "clave-de-pruebas-a1-solo-tests")
    monkeypatch.setattr(settings, "auth_pepper", "pepper-de-pruebas-a1")
    monkeypatch.setattr(settings, "login_max_intentos", 5)
    monkeypatch.setattr(settings, "login_bloqueo_min", 15)
    return settings


@pytest.fixture
def fabrica_usuarios():
    """Crea cuentas locales de prueba y las elimina al terminar.

    Devuelve un ``SimpleNamespace`` con ``id``, ``username``, ``email``,
    ``password`` y ``rol``. El teardown borra sesiones, auditoría e intentos
    asociados: la base queda como estaba.
    """
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import Auditoria, LoginAttempt, Sesion, Usuario
    from sqlalchemy import func, select

    creados: list[int] = []

    with SessionLocal() as db:
        # Corte para borrar solo lo que generen estas pruebas (el login de un
        # usuario inexistente se audita con actor "sistema", sin id de usuario).
        corte = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0

    def _crear(*, username: str | None = None, email: str | None = None,
               password: str = "Clave-Segura-2026", rol: str = "consulta",
               activo: bool = True, must_change: bool = False,
               ) -> SimpleNamespace:
        sufijo = uuid.uuid4().hex[:10]
        username = username or f"prueba-{sufijo}"
        email = email or f"{username}@ejemplo.test"
        with SessionLocal() as db:
            usuario = Usuario(
                nombre=f"Usuario {sufijo}",
                username=username,
                email=email,
                rol=rol,
                auth_provider="local",
                password_hash=hash_password(password),
                is_active=activo,
                must_change_password=must_change,
                # La fixture es genérica de login: sin segundo factor para no
                # desviar al challenge por correo (Fase A4).
                mfa_policy="disabled",
            )
            db.add(usuario)
            db.flush()
            identificador = usuario.id
            db.commit()
        creados.append(identificador)
        return SimpleNamespace(id=identificador, username=username, email=email,
                               password=password, rol=rol)

    yield _crear

    with SessionLocal() as db:
        actores = [f"usuario:{identificador}" for identificador in creados]
        for identificador in creados:
            db.execute(delete(Sesion).where(Sesion.usuario_id == identificador))
            db.execute(delete(Usuario).where(Usuario.id == identificador))
        if actores:
            db.execute(delete(Auditoria).where(
                Auditoria.id > corte, Auditoria.actor.in_(actores)))
        # Intentos de acceso a identificadores inexistentes: actor "sistema".
        db.execute(delete(Auditoria).where(
            Auditoria.id > corte, Auditoria.actor == "sistema"))
        db.execute(delete(LoginAttempt).where(LoginAttempt.id > corte_intentos))
        db.commit()

