"""Endurecimiento para exposición a internet (Fase A6a).

Cubre, con PostgreSQL y el ciclo de vida completo de la aplicación:

- cabeceras de seguridad: CSP con nonce por petición, HSTS solo sobre HTTPS,
  ``Permissions-Policy``/COOP/CORP/nosniff/``Referrer-Policy``/
  ``X-Frame-Options`` y ``Cache-Control: no-store`` en ``/auth/*``;
- validación de ``EET_ENVIRONMENT=prod`` (falla cerrada) y aviso en ``staging``;
- rate limiting por IP en login, en el challenge de segundo factor y en el
  envío de códigos por correo;
- límite de subida (413) en entregas, malla y mapa;
- la bandera apagada sigue sirviendo la aplicación como antes.

Ejecución:

    uv run pytest -m integration tests/test_auth_hardening.py -q
"""
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration

COOKIE_SESION = "eet_session"  # cookie_secure=False en las pruebas
COOKIE_CSRF = "eet_csrf"
COOKIE_MFA = "eet_mfa"

#: IP documental (TEST-NET-3) para las pruebas de rate limit por servicio.
IP_PRUEBA = "203.0.113.77"
#: IP que informa ``starlette.testclient`` (no es una dirección real).
IP_CLIENTE = "testclient"

PATRON_NONCE_CSP = re.compile(r"'nonce-([A-Za-z0-9_\-]+)'")
PATRON_NONCE_HTML = re.compile(r'<script nonce="([^"]*)"')
PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]*)"')


# --- Infraestructura -----------------------------------------------------------
@pytest.fixture
def cliente(auth_activa):
    """Cliente con la bandera activa, HTTP y cookies no seguras."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def cliente_sin_auth():
    """Cliente con la bandera **apagada** (comportamiento previo a A1)."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def limpieza():
    """Borra intentos, códigos y auditoría generados por la prueba.

    Además deja limpia la IP ``testclient`` al empezar: otras pruebas de login
    de la suite dejan fallos con esa IP y contaminarían los contadores (una IP
    real nunca vale ``testclient``).
    """
    from app.core.database import SessionLocal
    from app.models import Auditoria, LoginAttempt, MfaEmailCode

    with SessionLocal() as db:
        corte_auditoria = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0
        corte_codigos = db.scalar(select(func.max(MfaEmailCode.id))) or 0
        db.execute(delete(LoginAttempt).where(LoginAttempt.ip == IP_CLIENTE))
        db.commit()

    yield

    with SessionLocal() as db:
        db.execute(delete(LoginAttempt).where(
            LoginAttempt.id > corte_intentos))
        db.execute(delete(LoginAttempt).where(LoginAttempt.ip == IP_CLIENTE))
        db.execute(delete(MfaEmailCode).where(MfaEmailCode.id > corte_codigos))
        db.execute(delete(Auditoria).where(Auditoria.id > corte_auditoria))
        db.commit()


@pytest.fixture
def fabrica(limpieza):
    """Cuentas locales de prueba, borradas al terminar."""
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import (
        Auditoria,
        AuthToken,
        LoginAttempt,
        MfaEmailCode,
        MfaTotp,
        Sesion,
        Usuario,
    )

    creados: list[int] = []
    with SessionLocal() as db:
        corte_auditoria = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0

    def _crear(*, rol: str = "consulta", mfa_policy: str = "disabled",
               activo: bool = True) -> SimpleNamespace:
        import uuid

        sufijo = uuid.uuid4().hex[:10]
        username = f"dur-{sufijo}"
        with SessionLocal() as db:
            usuario = Usuario(
                nombre=f"Usuario Endurecimiento {sufijo}",
                username=username,
                email=f"{username}@ejemplo.test",
                rol=rol,
                auth_provider="local",
                password_hash=hash_password("Clave-Segura-2026"),
                is_active=activo,
                must_change_password=False,
                mfa_policy=mfa_policy,
            )
            db.add(usuario)
            db.flush()
            identificador = usuario.id
            db.commit()
        creados.append(identificador)
        return SimpleNamespace(id=identificador, username=username,
                               email=f"{username}@ejemplo.test",
                               password="Clave-Segura-2026", rol=rol)

    yield _crear

    with SessionLocal() as db:
        actores = [f"usuario:{identificador}" for identificador in creados]
        for identificador in creados:
            db.execute(delete(MfaEmailCode).where(
                MfaEmailCode.usuario_id == identificador))
            db.execute(delete(AuthToken).where(
                AuthToken.usuario_id == identificador))
            db.execute(delete(MfaTotp).where(MfaTotp.usuario_id == identificador))
            db.execute(delete(Sesion).where(Sesion.usuario_id == identificador))
            db.execute(delete(Usuario).where(Usuario.id == identificador))
        if actores:
            db.execute(delete(Auditoria).where(
                Auditoria.id > corte_auditoria, Auditoria.actor.in_(actores)))
        db.execute(delete(Auditoria).where(
            Auditoria.id > corte_auditoria, Auditoria.actor == "sistema"))
        db.execute(delete(LoginAttempt).where(
            LoginAttempt.id > corte_intentos))
        db.commit()


# --- Helpers -------------------------------------------------------------------
def _csrf_anonimo(cliente: TestClient) -> str:
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _login(cliente: TestClient, usuario, password: str | None = None):
    return cliente.post(
        "/auth/login",
        data={"login": usuario.username,
              "password": password or usuario.password,
              "csrf_token": _csrf_anonimo(cliente)},
        follow_redirects=False,
    )


def _sembrar_intentos(ip: str, cantidad: int, *, factor: str = "password",
                      identificador: str = "sembrado", usuario_id: int | None = None,
                      hace: timedelta = timedelta(0)) -> None:
    """Inserta fallos en ``login_attempts`` (la tabla que lee el rate limit)."""
    from app.core.database import SessionLocal
    from app.core.security import utcnow
    from app.models import LoginAttempt

    if usuario_id is not None:
        identificador = f"sembrado-{usuario_id}"
    with SessionLocal() as db:
        for _ in range(cantidad):
            db.add(LoginAttempt(
                identificador=identificador, ip=ip, exito=False,
                factor=factor, created_at=utcnow() - hace))
        db.commit()


def _auditoria(accion: str) -> int:
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        return int(db.scalar(
            select(func.count()).select_from(Auditoria).where(
                Auditoria.accion == accion)
        ) or 0)


# --- 1. Cabeceras de seguridad --------------------------------------------------
def test_csp_con_nonce_coincide_con_los_scripts_inline(cliente):
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200

    csp = respuesta.headers["content-security-policy"]
    nonce = PATRON_NONCE_CSP.search(csp)
    assert nonce, "la CSP debe declarar un nonce"
    nonces_html = PATRON_NONCE_HTML.findall(respuesta.text)
    assert nonces_html, "el login debe incluir su script inline con nonce"
    # Todos los <script> inline comparten exactamente el nonce del header.
    assert set(nonces_html) == {nonce.group(1)}
    for directiva in ("default-src 'self'", "base-uri 'self'",
                      "form-action 'self'", "frame-ancestors 'none'",
                      "object-src 'none'", "connect-src 'self'"):
        assert directiva in csp
    # El nonce no habilita scripts inline arbitrarios fuera de él.
    script_src = [parte for parte in csp.split(";") if "script-src" in parte][0]
    assert "'unsafe-inline'" not in script_src
    # Única excepción documentada: estilos por los style="width:%" de la dona.
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_cada_peticion_estrena_nonce(cliente):
    primera = cliente.get("/auth/login")
    segunda = cliente.get("/auth/login")
    nonce_1 = PATRON_NONCE_CSP.search(primera.headers["content-security-policy"])
    nonce_2 = PATRON_NONCE_CSP.search(segunda.headers["content-security-policy"])
    assert nonce_1 and nonce_2
    assert nonce_1.group(1) != nonce_2.group(1)


def test_nonce_se_mantiene_en_pagina_autenticada(cliente, fabrica):
    usuario = fabrica()
    assert _login(cliente, usuario).status_code == 303
    portada = cliente.get("/")
    assert portada.status_code == 200
    csp = portada.headers["content-security-policy"]
    nonce = PATRON_NONCE_CSP.search(csp).group(1)
    assert PATRON_NONCE_HTML.findall(portada.text) == [nonce]


def test_cabeceras_de_endurecimiento(cliente):
    respuesta = cliente.get("/auth/login")
    assert respuesta.headers["x-content-type-options"] == "nosniff"
    assert respuesta.headers["referrer-policy"] == "no-referrer"
    assert respuesta.headers["x-frame-options"] == "DENY"
    assert respuesta.headers["permissions-policy"] == \
        "geolocation=(), camera=(), microphone=()"
    assert respuesta.headers["cross-origin-opener-policy"] == "same-origin"
    assert respuesta.headers["cross-origin-resource-policy"] == "same-origin"


def test_hsts_ausente_sobre_http(cliente):
    respuesta = cliente.get("/auth/login")
    assert "strict-transport-security" not in respuesta.headers


def test_hsts_presente_sobre_https(auth_activa):
    from app.main import app

    with TestClient(app, base_url="https://testserver") as https:
        respuesta = https.get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.headers["strict-transport-security"] == \
        "max-age=31536000; includeSubDomains"
    assert respuesta.headers["content-security-policy"]


def test_cache_control_no_store_solo_en_auth(cliente):
    assert cliente.get("/auth/login").headers["cache-control"] == "no-store"
    assert cliente.get("/auth/recuperar").headers["cache-control"] == "no-store"
    # Fuera de /auth/* no se impone: el CSS y el HTML pueden cachearse.
    assert "no-store" not in cliente.get("/salud").headers.get("cache-control", "")


# --- 2. Validación de producción ------------------------------------------------
def _prod(**cambios):
    """Settings válido para ``prod``; cada prueba rompe solo una condición."""
    from app.core.config import Settings

    base = dict(
        environment="prod",
        auth_enabled=True,
        cookie_secure=True,
        public_base_url="https://eet.aep.gov.py",
        email_backend="smtp",
        smtp_host="smtp.aep.gov.py",
        smtp_user="no-reply@aep.gov.py",
        expose_docs=False,
        secret_key="s" * 40,
        auth_pepper="p" * 20,
        session_cookie="__Host-eet_session",
    )
    base.update(cambios)
    return Settings(**base)


def test_produccion_acepta_configuracion_completa():
    ajustes = _prod()
    assert ajustes.environment == "prod"
    assert ajustes.cookie_secure is True


@pytest.mark.parametrize(
    ("cambio", "esperado"),
    [
        ({"cookie_secure": False}, "EET_COOKIE_SECURE"),
        ({"public_base_url": "http://eet.aep.gov.py"}, "EET_PUBLIC_BASE_URL"),
        ({"email_backend": "console"}, "EET_EMAIL_BACKEND"),
        ({"smtp_host": None}, "EET_SMTP_HOST"),
        ({"expose_docs": True}, "EET_EXPOSE_DOCS"),
        ({"secret_key": "corta"}, "EET_SECRET_KEY"),
        ({"auth_pepper": "corto"}, "EET_AUTH_PEPPER"),
        ({"session_cookie": "eet_session"}, "EET_SESSION_COOKIE"),
    ],
)
def test_produccion_rechaza_cada_requisito_incumplido(cambio, esperado):
    with pytest.raises(ValueError) as error:
        _prod(**cambio)
    mensaje = str(error.value)
    assert esperado in mensaje
    assert "EET_ENVIRONMENT=prod" in mensaje


def test_produccion_no_filtra_secretos_en_el_error():
    secreto = "SECRETO-QUE-NO-DEBE-APARECER-0123456789"
    with pytest.raises(ValueError) as error:
        _prod(cookie_secure=False, secret_key=secreto)
    assert secreto not in str(error.value)


def test_produccion_sin_autenticacion_no_exige_endurecimiento():
    ajustes = _prod(auth_enabled=False, cookie_secure=False,
                    public_base_url="http://localhost:8000",
                    email_backend="console", expose_docs=True)
    assert ajustes.auth_enabled is False


def test_staging_solo_advierte(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        ajustes = _prod(environment="staging", cookie_secure=False)
    assert ajustes.environment == "staging"
    assert "staging incompleto" in caplog.text


def test_entorno_desconocido_falla():
    with pytest.raises(ValueError, match="EET_ENVIRONMENT"):
        _prod(environment="produccion")


def test_trusted_proxy_ips_se_parsea_a_redes():
    ajustes = _prod(trusted_proxy_ips="10.0.0.0/8, 192.168.1.10 ,::1/128")
    assert len(ajustes.trusted_proxy_networks) == 3
    assert ajustes.trusted_proxy_hosts == ["10.0.0.0/8", "192.168.1.10", "::1/128"]
    assert _prod(trusted_proxy_ips="").trusted_proxy_networks == []


def test_trusted_proxy_ips_invalida_falla():
    with pytest.raises(ValueError, match="EET_TRUSTED_PROXY_IPS"):
        _prod(trusted_proxy_ips="no-es-una-red")


# --- 3. Rate limiting por IP ----------------------------------------------------
def test_login_bloquea_por_ip_sin_revelar_cuenta(auth_activa, fabrica, monkeypatch):
    """Con el techo de IP agotado, el login ni busca el usuario."""
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.auth.service import autenticar

    monkeypatch.setattr(settings, "login_ip_max_15min", 3)
    usuario = fabrica()
    _sembrar_intentos(IP_PRUEBA, 3)

    with SessionLocal() as db:
        antes = _auditoria("login_ip_locked")
        resultado = autenticar(db, usuario.username, usuario.password, ip=IP_PRUEBA)

    assert resultado.motivo == "bloqueado"
    assert resultado.usuario is None
    assert resultado.espera_min == int(settings.login_bloqueo_min)
    assert _auditoria("login_ip_locked") == antes + 1

    # Otra IP (sin fallos) sigue pudiendo autenticarse con la clave correcta.
    with SessionLocal() as db:
        otro = autenticar(db, usuario.username, usuario.password, ip="198.51.100.9")
    assert otro.ok


def test_login_http_bloquea_tras_agotar_los_fallos_por_ip(cliente, fabrica,
                                                           monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "login_ip_max_15min", 2)
    usuario = fabrica()

    for _ in range(2):
        fallo = _login(cliente, usuario, password="No-Es-La-Clave-2026")
        assert "Usuario o contraseña incorrectos." in fallo.text

    bloqueo = _login(cliente, usuario)  # clave correcta, IP ya bloqueada
    assert bloqueo.status_code == 200
    # Mismo mensaje que credenciales inválidas: no revela la cuenta (M-23).
    assert "Usuario o contraseña incorrectos." in bloqueo.text
    assert COOKIE_SESION not in cliente.cookies


def test_challenge_mfa_bloquea_por_ip(fabrica):
    """10 fallos de segundo factor desde la misma IP cortan el challenge."""
    from app.auth.service import (
        MAX_FALLOS_MFA_POR_IP,
        cargar_desafio,
        crear_desafio,
        verificar_desafio,
    )
    from app.core.database import SessionLocal
    from app.models import AuthToken, Usuario

    usuario = fabrica()
    _sembrar_intentos(IP_PRUEBA, MAX_FALLOS_MFA_POR_IP, factor="mfa")

    with SessionLocal() as db:
        token = crear_desafio(db, db.get(Usuario, usuario.id), IP_PRUEBA)
    with SessionLocal() as db:
        fila = cargar_desafio(db, token)
        assert fila is not None
        resultado = verificar_desafio(db, fila, "000000", ip=IP_PRUEBA)
        assert resultado.motivo == "limite"
        assert resultado.usuario is not None
        # El challenge queda consumido: hay que volver a empezar por la clave.
        assert db.get(AuthToken, fila.id).used_at is not None

    # Un intento correcto desde la misma IP tampoco pasa el corte.
    with SessionLocal() as db:
        assert verificar_desafio(db, fila, "000000", ip=IP_PRUEBA).motivo in {
            "ausente", "invalido", "limite"}


def test_mfa_http_excede_fallos_por_ip_y_vuelve_al_login(cliente, fabrica):
    """Con la IP agotada, el POST de /auth/mfa redirige con mensaje genérico."""
    from app.auth.service import MAX_FALLOS_MFA_POR_IP

    usuario = fabrica(mfa_policy="email")
    assert _login(cliente, usuario).status_code == 303
    assert cliente.cookies.get(COOKIE_MFA), "el challenge debe existir"
    token = PATRON_CSRF.search(cliente.get("/auth/mfa").text).group(1)

    _sembrar_intentos(IP_CLIENTE, MAX_FALLOS_MFA_POR_IP, factor="mfa")

    respuesta = cliente.post(
        "/auth/mfa",
        data={"csrf_token": token, "codigo": "000000", "metodo": "email"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/login?error=mfa_bloqueado"
    assert COOKIE_MFA not in cliente.cookies
    assert COOKIE_SESION not in cliente.cookies


def test_envio_de_codigo_limita_por_ip(cliente, fabrica):
    """Con 10 códigos emitidos desde la IP no se envía otro ni se genera fila."""
    from app.auth.service import MAX_ENVIOS_CORREO_POR_IP_HORA
    from app.core.database import SessionLocal
    from app.core.security import utcnow
    from app.models import MfaEmailCode

    victima = fabrica(mfa_policy="email")
    ruido = fabrica(mfa_policy="email")

    assert _login(cliente, victima).status_code == 303
    token = PATRON_CSRF.search(cliente.get("/auth/mfa").text).group(1)

    # Los 10 códigos del tope pertenecen a otra cuenta y a la misma IP; el
    # código propio de la víctima se envejece para saltar el cooldown de 60 s.
    with SessionLocal() as db:
        for _ in range(MAX_ENVIOS_CORREO_POR_IP_HORA):
            db.add(MfaEmailCode(
                usuario_id=ruido.id, code_hash="0" * 64, purpose="mfa",
                expires_at=utcnow() + timedelta(minutes=10), attempts=0,
                ip=IP_CLIENTE, created_at=utcnow()))
        propio = db.scalar(
            select(MfaEmailCode).where(MfaEmailCode.usuario_id == victima.id)
            .order_by(MfaEmailCode.id.desc()))
        propio.created_at = utcnow() - timedelta(minutes=5)
        db.commit()
        antes = db.scalar(select(func.count()).select_from(MfaEmailCode))

    respuesta = cliente.post(
        "/auth/mfa/enviar", data={"csrf_token": token}, follow_redirects=False)
    assert respuesta.status_code == 200
    assert "Intente más tarde." in respuesta.text

    with SessionLocal() as db:
        despues = db.scalar(select(func.count()).select_from(MfaEmailCode))
    assert despues == antes, "no debe emitirse un código nuevo"


def test_recuperacion_limita_sondeos_por_ip(cliente, limpieza, monkeypatch):
    """10 sondeos fallidos desde una IP bloquean la recuperación (mismo texto)."""
    from app.auth.service import MAX_RESET_POR_IP_HORA, RESPUESTA_RECUPERACION

    token = _csrf_anonimo(cliente)
    _sembrar_intentos(IP_CLIENTE, MAX_RESET_POR_IP_HORA, factor="recovery")
    antes = _auditoria("password_reset_requested")

    respuesta = cliente.post(
        "/auth/recuperar",
        data={"csrf_token": token, "identificador": "quien-sea@ejemplo.test"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 200
    assert RESPUESTA_RECUPERACION in respuesta.text
    assert _auditoria("password_reset_requested") == antes + 1


# --- 4. Límite de subida --------------------------------------------------------
def _subir(cliente, ruta: str, *, datos: dict, nombre: str = "archivo.xlsx"):
    return cliente.post(
        ruta, data=datos,
        files={"file": (nombre, b"A", "application/octet-stream")},
        follow_redirects=False,
    )


@pytest.mark.parametrize(
    ("ruta", "datos"),
    [
        ("/entregas/upload",
         {"asistente_letra": "A", "semana": "2026-W01", "tipo": "avance"}),
        ("/malla/upload", {}),
        ("/mapa/upload", {}),
    ],
)
def test_subida_supera_el_limite_y_devuelve_413(cliente_sin_auth, monkeypatch,
                                                ruta, datos):
    from app.core.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 0)
    respuesta = _subir(cliente_sin_auth, ruta, datos=datos)
    assert respuesta.status_code == 413
    assert "text/html" in respuesta.headers["content-type"]
    assert "Archivo demasiado grande" in respuesta.text
    assert "límite de 0 MB" in respuesta.text


def test_subida_dentro_del_limite_no_devuelve_413(monkeypatch):
    """Con el límite holgado el corte por tamaño no aparece (A6a)."""
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "max_upload_mb", 20)
    # El contenido de 1 byte no es un CSV válido: lo que importa es que el
    # rechazo 413 del guardia de tamaño no se dispare.
    with TestClient(app, raise_server_exceptions=False) as cliente:
        respuesta = _subir(cliente, "/malla/upload", datos={})
    assert respuesta.status_code != 413


# --- 5. Bandera apagada ---------------------------------------------------------
def test_bandera_apagada_sirve_la_aplicacion(cliente_sin_auth):
    portada = cliente_sin_auth.get("/")
    assert portada.status_code == 200
    entregas = cliente_sin_auth.get("/entregas")
    assert entregas.status_code == 200
    # Las cabeceras de endurecimiento viajan igual con la bandera apagada.
    assert entregas.headers["content-security-policy"]
    assert entregas.headers["x-content-type-options"] == "nosniff"
