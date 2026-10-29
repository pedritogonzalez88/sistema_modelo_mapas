"""CSRF con la bandera de autenticación activa.

Cubre el modelo double-submit (cookie ``eet_csrf`` para el login anónimo) y el
token por sesión (``sesion.csrf_token``), incluida la validación de origen.

    uv run pytest -m integration tests/test_auth_csrf.py -q
"""
import re

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

COOKIE_CSRF = "eet_csrf"
PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]*)"')
NUEVA = "Nueva-Clave-2026"


@pytest.fixture
def cliente(auth_activa, fabrica_usuarios):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _token_anonimo(cliente: TestClient) -> str:
    cliente.get("/auth/login")
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _autenticado(cliente: TestClient, usuario) -> str:
    """Inicia sesión y devuelve el token CSRF de esa sesión."""
    cliente.post("/auth/login",
                 data={"login": usuario.username, "password": usuario.password,
                       "csrf_token": _token_anonimo(cliente)},
                 follow_redirects=False)
    encontrado = PATRON_CSRF.search(cliente.get("/").text)
    assert encontrado
    return encontrado.group(1)


def _token_de_otra_sesion(usuario) -> str:
    """Token de una segunda sesión válida del mismo usuario."""
    from app.auth.service import crear_sesion
    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        fila = db.get(Usuario, usuario.id)
        _, sesion = crear_sesion(db, fila, "127.0.0.1", "pytest-otra-sesion")
        return sesion.csrf_token


def _cambio(cliente: TestClient, token: str, **extra):
    datos = {"csrf_token": token, "actual": "x", "nueva": NUEVA,
             "confirmacion": NUEVA}
    datos.update(extra)
    return cliente.post("/auth/cambiar-password", data=datos,
                        follow_redirects=False)


# --- Login anónimo (double submit) ---------------------------------------------
def test_post_de_login_sin_token_es_403_html(cliente):
    respuesta = cliente.post("/auth/login",
                             data={"login": "alguien", "password": "algo"},
                             follow_redirects=False)
    assert respuesta.status_code == 403
    assert "text/html" in respuesta.headers["content-type"]
    assert "Token CSRF inválido o ausente." in respuesta.text


def test_post_de_login_con_token_ajeno_es_403(cliente):
    _token_anonimo(cliente)
    respuesta = cliente.post("/auth/login",
                             data={"login": "alguien", "password": "algo",
                                   "csrf_token": "token-inventado-de-otro-cliente"},
                             follow_redirects=False)
    assert respuesta.status_code == 403


def test_login_con_token_de_la_cookie_funciona(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    respuesta = cliente.post(
        "/auth/login",
        data={"login": usuario.username, "password": usuario.password,
              "csrf_token": _token_anonimo(cliente)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303


# --- Sesión autenticada (token por sesión) -------------------------------------
def test_cambio_de_password_sin_token_es_403(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    _autenticado(cliente, usuario)
    respuesta = cliente.post("/auth/cambiar-password",
                             data={"actual": usuario.password, "nueva": NUEVA,
                                   "confirmacion": NUEVA},
                             follow_redirects=False)
    assert respuesta.status_code == 403
    assert "Token CSRF inválido o ausente." in respuesta.text


def test_token_de_otra_sesion_es_403(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    _autenticado(cliente, usuario)
    respuesta = _cambio(cliente, _token_de_otra_sesion(usuario),
                        actual=usuario.password)
    assert respuesta.status_code == 403


def test_token_de_la_cookie_anonima_no_sirve_con_sesion(cliente,
                                                        fabrica_usuarios):
    usuario = fabrica_usuarios()
    _autenticado(cliente, usuario)
    respuesta = _cambio(cliente, cliente.cookies.get(COOKIE_CSRF),
                        actual=usuario.password)
    assert respuesta.status_code == 403


def test_origin_distinto_es_403(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    token = _autenticado(cliente, usuario)
    respuesta = cliente.post(
        "/auth/cambiar-password",
        data={"csrf_token": token, "actual": usuario.password, "nueva": NUEVA,
              "confirmacion": NUEVA},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 403


def test_referer_distinto_es_403(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    token = _autenticado(cliente, usuario)
    respuesta = cliente.post(
        "/auth/cambiar-password",
        data={"csrf_token": token, "actual": usuario.password, "nueva": NUEVA,
              "confirmacion": NUEVA},
        headers={"Referer": "http://otro-host.example/form"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 403


def test_origin_propio_es_aceptado(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    token = _autenticado(cliente, usuario)
    respuesta = cliente.post(
        "/auth/cambiar-password",
        data={"csrf_token": token, "actual": usuario.password, "nueva": NUEVA,
              "confirmacion": NUEVA},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303


def test_token_valido_permite_el_post(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    token = _autenticado(cliente, usuario)
    respuesta = _cambio(cliente, token, actual=usuario.password)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/cambiar-password?ok=password_changed"


def test_logout_exige_csrf(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    token = _autenticado(cliente, usuario)
    sin_token = cliente.post("/auth/logout", follow_redirects=False)
    assert sin_token.status_code == 403
    con_token = cliente.post("/auth/logout", data={"csrf_token": token},
                             follow_redirects=False)
    assert con_token.status_code == 303
