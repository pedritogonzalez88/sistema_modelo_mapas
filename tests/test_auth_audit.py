"""Auditoría del flujo de acceso: actor real, IP y ausencia de secretos.

Requieren PostgreSQL (``uv run pytest -m integration tests/test_auth_audit.py``).
"""
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = pytest.mark.integration

COOKIE_CSRF = "eet_csrf"
PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]*)"')


@pytest.fixture
def cliente(auth_activa, fabrica_usuarios):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(cliente: TestClient, usuario, password: str | None = None):
    cliente.get("/auth/login")
    return cliente.post(
        "/auth/login",
        data={"login": usuario.username,
              "password": password or usuario.password,
              "csrf_token": cliente.cookies.get(COOKIE_CSRF)},
        follow_redirects=False,
    )


def _auditoria(usuario_id: int, accion: str | None = None):
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        consulta = select(Auditoria).where(
            Auditoria.actor == f"usuario:{usuario_id}")
        if accion:
            consulta = consulta.where(Auditoria.accion == accion)
        return db.scalars(consulta.order_by(Auditoria.id)).all()


def test_login_correcto_audita_actor_ip_y_agente(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    assert _login(cliente, usuario).status_code == 303

    filas = _auditoria(usuario.id, "login_ok")
    assert len(filas) == 1
    fila = filas[0]
    assert fila.actor == f"usuario:{usuario.id}"
    assert fila.entidad == "usuario"
    assert fila.entidad_id == str(usuario.id)
    assert fila.ip == "testclient"
    assert fila.user_agent
    assert usuario.password not in json.dumps(fila.detalle)


def test_login_fallido_audita_sin_filtrar_el_secreto(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    respuesta = _login(cliente, usuario, password="Clave-Incorrecta-2026")
    assert respuesta.status_code == 200

    filas = _auditoria(usuario.id, "login_fail")
    assert filas, "un fallo de credenciales debe quedar auditado"
    detalle = json.dumps(filas[-1].detalle, ensure_ascii=False)
    assert "Clave-Incorrecta-2026" not in detalle
    assert "password" not in detalle.casefold()
    assert filas[-1].ip == "testclient"


def test_bloqueo_queda_auditado(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    for _ in range(5):
        _login(cliente, usuario, password="Falla-Constante-000")

    filas = _auditoria(usuario.id, "login_locked")
    assert filas, "el bloqueo por intentos debe quedar auditado"
    assert filas[-1].detalle.get("espera_min") == 15


def test_cambio_de_password_audita_y_no_guarda_la_clave(cliente,
                                                         fabrica_usuarios):
    usuario = fabrica_usuarios()
    assert _login(cliente, usuario).status_code == 303
    token_csrf = PATRON_CSRF.search(cliente.get("/").text).group(1)
    nueva = "Nueva-Clave-2026"

    respuesta = cliente.post(
        "/auth/cambiar-password",
        data={"csrf_token": token_csrf, "actual": usuario.password,
              "nueva": nueva, "confirmacion": nueva},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303

    filas = _auditoria(usuario.id, "password_changed")
    assert len(filas) == 1
    fila = filas[0]
    detalle = json.dumps(fila.detalle, ensure_ascii=False)
    assert nueva not in detalle
    assert usuario.password not in detalle
    assert "password" not in detalle.casefold()
    assert fila.ip == "testclient"
    assert fila.detalle.get("sesiones_revocadas") == 0


def test_la_sesion_solo_guarda_el_hash_del_token(cliente, fabrica_usuarios):
    from app.core.database import SessionLocal
    from app.core.security import sha256_hex
    from app.models import Sesion

    usuario = fabrica_usuarios()
    assert _login(cliente, usuario).status_code == 303
    token = cliente.cookies.get("eet_session")
    assert token

    with SessionLocal() as db:
        filas = db.scalars(
            select(Sesion).where(Sesion.usuario_id == usuario.id)).all()
    assert filas
    hashes = {fila.token_hash for fila in filas}
    assert sha256_hex(token) in hashes
    assert token not in hashes
