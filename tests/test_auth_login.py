"""Login, logout y sesiones con la bandera de autenticación activa.

Requieren PostgreSQL y el ciclo de vida completo de la aplicación, por eso se
marcan como ``integration``:

    uv run pytest -m integration tests/test_auth_login.py -q
"""
import re

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

COOKIE_SESION = "eet_session"  # cookie_secure=False en las pruebas
COOKIE_CSRF = "eet_csrf"
PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]*)"')


@pytest.fixture
def cliente(auth_activa, fabrica_usuarios):
    """Cliente con la bandera activa; el apagado/encendido es por petición."""
    from app.main import app

    with TestClient(app) as c:
        yield c


def _csrf_anonimo(cliente: TestClient) -> str:
    """Token CSRF de la cookie tras abrir el formulario de login."""
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _csrf_de_sesion(cliente: TestClient) -> str:
    """Token del formulario rendereado con la sesión activa (shell)."""
    html = cliente.get("/").text
    encontrado = PATRON_CSRF.search(html)
    assert encontrado, "la página autenticada debe incluir el campo CSRF"
    return encontrado.group(1)


def _login(cliente: TestClient, usuario, password: str | None = None,
           siguiente: str = ""):
    return cliente.post(
        "/auth/login",
        data={"login": usuario.username, "password": password or usuario.password,
              "csrf_token": _csrf_anonimo(cliente), "next": siguiente},
        follow_redirects=False,
    )


# --- Acceso --------------------------------------------------------------------
def test_raiz_sin_sesion_redirige_al_login(cliente):
    respuesta = cliente.get("/", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/auth/login?next=%2F")


def test_login_muestra_formulario_y_cookie_csrf(cliente):
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200
    assert "text/html" in respuesta.headers["content-type"]
    assert 'action="/auth/login"' in respuesta.text
    assert 'name="csrf_token"' in respuesta.text
    assert cliente.cookies.get(COOKIE_CSRF)
    # Cookie HttpOnly: no debe quedar accesible a scripts.
    cabecera = respuesta.headers.get("set-cookie", "")
    assert "HttpOnly" in cabecera
    assert "SameSite=lax" in cabecera or "samesite=lax" in cabecera.lower()


def test_credenciales_incorrectas_responden_generico_sin_sesion(cliente,
                                                                fabrica_usuarios):
    usuario = fabrica_usuarios()
    respuesta = _login(cliente, usuario, password="No-Es-La-Clave-2026")
    assert respuesta.status_code == 200
    assert "Usuario o contraseña incorrectos." in respuesta.text
    assert COOKIE_SESION not in cliente.cookies


def test_usuario_inexistente_responde_lo_mismo(cliente):
    respuesta = cliente.post(
        "/auth/login",
        data={"login": "no-existe-en-el-sistema", "password": "lo-que-sea-123",
              "csrf_token": _csrf_anonimo(cliente)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 200
    assert "Usuario o contraseña incorrectos." in respuesta.text
    assert COOKIE_SESION not in cliente.cookies


def test_login_correcto_crea_sesion_y_permite_navegar(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/"
    token = cliente.cookies.get(COOKIE_SESION)
    assert token

    portada = cliente.get("/", follow_redirects=False)
    assert portada.status_code == 200
    # El shell muestra el nombre y el rol del usuario autenticado.
    assert usuario.username not in portada.text  # nunca se imprime el usuario
    assert "Cambiar contraseña" in portada.text
    assert "Salir" in portada.text

    # La cookie de sesión es HttpOnly y no guarda el token en claro en la base.
    cabecera = respuesta.headers.get("set-cookie", "")
    assert "HttpOnly" in cabecera
    assert "Secure" not in cabecera  # cookie_secure=False en pruebas


def test_login_acepta_correo_como_identificador(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    respuesta = cliente.post(
        "/auth/login",
        data={"login": usuario.email.upper(), "password": usuario.password,
              "csrf_token": _csrf_anonimo(cliente)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert cliente.cookies.get(COOKIE_SESION)


def test_login_respeta_next_interno(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    respuesta = _login(cliente, usuario, siguiente="/qaqc")
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/qaqc"


@pytest.mark.parametrize("destino", ["https://evil.example/x", "//evil.example",
                                     "/\\evil.example", "javascript:alert(1)"])
def test_login_descarta_next_externo(cliente, fabrica_usuarios, destino):
    usuario = fabrica_usuarios()
    respuesta = _login(cliente, usuario, siguiente=destino)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/"


def test_logout_revoca_y_vuelve_a_pedir_login(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    assert _login(cliente, usuario).status_code == 303
    token_csrf = _csrf_de_sesion(cliente)

    salida = cliente.post("/auth/logout", data={"csrf_token": token_csrf},
                          follow_redirects=False)
    assert salida.status_code == 303
    assert salida.headers["location"] == "/auth/login?ok=sesion_cerrada"
    assert COOKIE_SESION not in cliente.cookies

    portada = cliente.get("/", follow_redirects=False)
    assert portada.status_code == 303
    assert portada.headers["location"].startswith("/auth/login")


def test_cinco_fallos_bloquean_la_cuenta(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    for _ in range(5):
        respuesta = _login(cliente, usuario, password="Falla-Segura-000")
        assert respuesta.status_code == 200

    # Con la contraseña correcta sigue bloqueada hasta que expire el plazo.
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 200
    # Mismo mensaje que credenciales inválidas: no revela la cuenta (M-23).
    assert "Usuario o contraseña incorrectos." in respuesta.text
    assert COOKIE_SESION not in cliente.cookies


def test_usuario_inactivo_no_entra(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios(activo=False)
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 200
    assert "Usuario o contraseña incorrectos." in respuesta.text
    assert COOKIE_SESION not in cliente.cookies


def test_must_change_password_obliga_a_cambiarla(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios(must_change=True)
    assert _login(cliente, usuario).status_code == 303

    portada = cliente.get("/", follow_redirects=False)
    assert portada.status_code == 303
    assert portada.headers["location"] == "/auth/cambiar-password?next=%2F"

    formulario = cliente.get("/auth/cambiar-password")
    assert formulario.status_code == 200
    assert "Cambiar contraseña" in formulario.text
    # Las páginas públicas y el logout siguen disponibles.
    assert cliente.get("/salud").status_code == 200


def test_must_change_prioriza_cambio_sobre_enrolamiento(cliente,
                                                        fabrica_usuarios):
    """El cambio obligatorio va antes que el enrolamiento TOTP (sin salto doble)."""
    usuario = fabrica_usuarios(must_change=True, mfa_policy="totp")
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/cambiar-password?next=%2F"

    portada = cliente.get("/", follow_redirects=False)
    assert portada.headers["location"] == "/auth/cambiar-password?next=%2F"

    formulario = cliente.get("/auth/cambiar-password")
    token = PATRON_CSRF.search(formulario.text).group(1)
    cambio = cliente.post(
        "/auth/cambiar-password",
        data={"csrf_token": token, "actual": usuario.password,
              "nueva": "Nueva-Clave-2026", "confirmacion": "Nueva-Clave-2026",
              "next": "/"},
        follow_redirects=False,
    )
    assert cambio.status_code == 303
    assert cambio.headers["location"] == "/auth/seguridad?ok=password_changed"

    # Sin TOTP confirmado, cualquier vista sigue exigiendo el enrolamiento.
    assert cliente.get("/", follow_redirects=False).headers["location"] == "/auth/seguridad"


def test_cambio_conserva_el_destino_solicitado(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios(must_change=True)
    respuesta = _login(cliente, usuario, siguiente="/entregas")
    assert respuesta.headers["location"] == "/auth/cambiar-password?next=%2Fentregas"

    formulario = cliente.get("/auth/cambiar-password?next=%2Fentregas")
    token = PATRON_CSRF.search(formulario.text).group(1)
    cambio = cliente.post(
        "/auth/cambiar-password",
        data={"csrf_token": token, "actual": usuario.password,
              "nueva": "Nueva-Clave-2026", "confirmacion": "Nueva-Clave-2026",
              "next": "/entregas"},
        follow_redirects=False,
    )
    assert cambio.status_code == 303
    assert cambio.headers["location"] == "/entregas?ok=password_changed"
    assert cliente.get("/entregas", follow_redirects=False).status_code == 200


def test_accept_json_devuelve_401(cliente):
    respuesta = cliente.get("/", headers={"Accept": "application/json"},
                            follow_redirects=False)
    assert respuesta.status_code == 401
    assert "application/json" in respuesta.headers["content-type"]
    assert respuesta.json()["detail"] == "Sesión requerida."


def test_estaticos_y_salud_siguen_publicos(cliente):
    assert cliente.get("/salud", follow_redirects=False).status_code == 200
    assert cliente.get("/static/css/app.css",
                       follow_redirects=False).status_code == 200


def test_vistas_de_accion_comparten_las_cabeceras_de_seguridad(cliente):
    respuesta = cliente.get("/", follow_redirects=False)
    assert respuesta.headers["x-content-type-options"] == "nosniff"
    assert respuesta.headers["referrer-policy"] == "no-referrer"
    assert respuesta.headers["x-frame-options"] == "DENY"


def test_cambiar_password_valida_y_aplica(cliente, fabrica_usuarios):
    usuario = fabrica_usuarios()
    assert _login(cliente, usuario).status_code == 303
    token_csrf = _csrf_de_sesion(cliente)

    corta = cliente.post("/auth/cambiar-password",
                         data={"csrf_token": token_csrf, "actual": usuario.password,
                               "nueva": "corta", "confirmacion": "corta"},
                         follow_redirects=False)
    assert corta.status_code == 200
    assert "al menos 12 caracteres" in corta.text

    mala_actual = cliente.post("/auth/cambiar-password",
                               data={"csrf_token": token_csrf,
                                     "actual": "Otra-Clave-2026",
                                     "nueva": "Nueva-Clave-2026",
                                     "confirmacion": "Nueva-Clave-2026"},
                               follow_redirects=False)
    assert mala_actual.status_code == 200
    assert "La contraseña actual no es correcta." in mala_actual.text

    ok = cliente.post("/auth/cambiar-password",
                      data={"csrf_token": token_csrf, "actual": usuario.password,
                            "nueva": "Nueva-Clave-2026",
                            "confirmacion": "Nueva-Clave-2026"},
                      follow_redirects=False)
    assert ok.status_code == 303
    assert ok.headers["location"] == "/?ok=password_changed"

    # La sesión en curso se conserva; con la contraseña vieja ya no se entra.
    assert cliente.get("/", follow_redirects=False).status_code == 200
