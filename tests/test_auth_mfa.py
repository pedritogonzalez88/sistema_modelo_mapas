"""Segundo factor TOTP, códigos de respaldo y sesiones revocables (Fase A3).

Requieren PostgreSQL y el ciclo de vida completo de la aplicación:

    uv run pytest -m integration tests/test_auth_mfa.py -q

Cobertura:

- el login con TOTP confirmado **no** crea sesión: deja un challenge de 5 min;
- código correcto, código incorrecto (5 intentos), replay y código de respaldo;
- la política ``totp`` sin enrolar deja la sesión abierta pero el gate solo
  permite ``/auth/seguridad``;
- revocación de sesiones ajenas y rechazo de la sesión en curso;
- el QR y la clave manual del enrolamiento; los códigos se muestran una vez.

Los helpers descifran el secreto con :class:`SecretBox` (la prueba se comporta
como la aplicación autenticadora del usuario) y generan los códigos con
``pyotp``: nunca se leen secretos de la respuesta HTTP.
"""
import re
import time
import uuid
from types import SimpleNamespace

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration

COOKIE_SESION = "eet_session"  # cookie_secure=False en las pruebas
COOKIE_CSRF = "eet_csrf"
COOKIE_MFA = "eet_mfa"

PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]*)"')
# Códigos de respaldo: XXXX-XXXX con el alfabeto sin caracteres ambiguos,
# tal como los pinta la lista de la pantalla de seguridad.
PATRON_CODIGO_RESPALDO = re.compile(
    r'codigos-lista__codigo">([A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4})<')

PASSWORD = "Clave-Segura-2026"


# --- Infraestructura -----------------------------------------------------------
@pytest.fixture
def cliente(auth_activa):
    """Cliente con la bandera activa y cookies no seguras."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def fabrica():
    """Crea cuentas locales con política de 2FA y limpia todo al terminar."""
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import (
        Auditoria,
        AuthToken,
        LoginAttempt,
        MfaTotp,
        RecoveryCode,
        Sesion,
        Usuario,
    )

    creados: list[int] = []
    with SessionLocal() as db:
        corte = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0

    def _crear(*, rol: str = "consulta", mfa_policy: str = "any",
               activo: bool = True, must_change: bool = False,
               password: str = PASSWORD) -> SimpleNamespace:
        sufijo = uuid.uuid4().hex[:10]
        username = f"mfa-{sufijo}"
        email = f"{username}@ejemplo.test"
        with SessionLocal() as db:
            usuario = Usuario(
                nombre=f"Usuario MFA {sufijo}",
                username=username,
                email=email,
                rol=rol,
                auth_provider="local",
                password_hash=hash_password(password),
                is_active=activo,
                must_change_password=must_change,
                mfa_policy=mfa_policy,
            )
            db.add(usuario)
            db.flush()
            identificador = usuario.id
            db.commit()
        creados.append(identificador)
        return SimpleNamespace(id=identificador, username=username, email=email,
                               password=password, rol=rol, mfa_policy=mfa_policy)

    yield _crear

    with SessionLocal() as db:
        actores = [f"usuario:{identificador}" for identificador in creados]
        for identificador in creados:
            db.execute(delete(AuthToken).where(AuthToken.usuario_id == identificador))
            db.execute(delete(RecoveryCode).where(RecoveryCode.usuario_id == identificador))
            db.execute(delete(MfaTotp).where(MfaTotp.usuario_id == identificador))
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


# --- Helpers HTTP --------------------------------------------------------------
def _csrf_anonimo(cliente: TestClient) -> str:
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _csrf_sesion(cliente: TestClient, ruta: str = "/auth/seguridad") -> str:
    html = cliente.get(ruta).text
    encontrado = PATRON_CSRF.search(html)
    assert encontrado, f"{ruta} debe incluir el campo CSRF"
    return encontrado.group(1)


def _login(cliente: TestClient, usuario, password: str | None = None,
           siguiente: str = ""):
    return cliente.post(
        "/auth/login",
        data={"login": usuario.username,
              "password": password or usuario.password,
              "csrf_token": _csrf_anonimo(cliente), "next": siguiente},
        follow_redirects=False,
    )


def _logout(cliente: TestClient) -> None:
    token = _csrf_sesion(cliente, "/")
    respuesta = cliente.post("/auth/logout", data={"csrf_token": token},
                             follow_redirects=False)
    assert respuesta.status_code == 303


# --- Helpers de TOTP -----------------------------------------------------------
def _secreto(usuario_id: int) -> str:
    """Descifra el secreto TOTP como lo haría la aplicación autenticadora."""
    from app.core.database import SessionLocal
    from app.core.security import secret_box
    from app.models import MfaTotp

    with SessionLocal() as db:
        fila = db.get(MfaTotp, usuario_id)
        assert fila is not None, "el usuario debe tener una fila mfa_totp"
        return secret_box().decrypt(fila.secret_enc, fila.key_id)


def _paso_usado(usuario_id: int) -> int | None:
    from app.core.database import SessionLocal
    from app.models import MfaTotp

    with SessionLocal() as db:
        fila = db.get(MfaTotp, usuario_id)
        return None if fila is None else fila.last_used_step


def _codigo_valido(usuario_id: int) -> str:
    """Código de un paso posterior al último aceptado (evita el replay).

    El servidor acepta ±1 paso, así que tras confirmar el enrolamiento (que
    consume el paso actual) el siguiente código válido es el del paso +1.
    """
    secreto = _secreto(usuario_id)
    ultimo = _paso_usado(usuario_id)
    ahora = int(time.time()) // 30
    for paso in (ahora, ahora + 1):
        if ultimo is None or paso > int(ultimo):
            return pyotp.TOTP(secreto).at(paso * 30)
    raise AssertionError("no quedan pasos válidos en la ventana (±1)")


def _codigo_ajeno() -> str:
    """Código de 6 dígitos de un secreto distinto: nunca es válido."""
    return pyotp.TOTP(pyotp.random_base32()).now()


def _enrolar(cliente: TestClient, usuario) -> list[str]:
    """Enrola TOTP por HTTP y devuelve los códigos de respaldo mostrados."""
    assert _login(cliente, usuario).status_code == 303
    token = _csrf_sesion(cliente)

    inicio = cliente.post("/auth/seguridad/totp/iniciar",
                          data={"csrf_token": token}, follow_redirects=False)
    assert inicio.status_code == 200
    assert "codigos-lista" not in inicio.text

    secreto = _secreto(usuario.id)
    confirmacion = cliente.post(
        "/auth/seguridad/totp/confirmar",
        data={"csrf_token": token, "codigo": pyotp.TOTP(secreto).now()},
        follow_redirects=False,
    )
    assert confirmacion.status_code == 200
    assert "codigos-lista" in confirmacion.text
    codigos = PATRON_CODIGO_RESPALDO.findall(confirmacion.text)
    assert len(codigos) == 10
    return codigos


def _desafio(cliente: TestClient, usuario) -> str:
    """Login con contraseña hasta el challenge y devuelve el CSRF anónimo."""
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/auth/mfa")
    form = cliente.get("/auth/mfa")
    assert form.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _challenge_actual(usuario_id: int):
    from app.core.database import SessionLocal
    from app.models import AuthToken

    with SessionLocal() as db:
        return db.scalar(
            select(AuthToken).where(
                AuthToken.usuario_id == usuario_id,
                AuthToken.purpose == "mfa",
            ).order_by(AuthToken.id.desc())
        )


def _sesiones(usuario_id: int) -> list:
    from app.core.database import SessionLocal
    from app.models import Sesion

    with SessionLocal() as db:
        return list(db.scalars(
            select(Sesion).where(Sesion.usuario_id == usuario_id)
            .order_by(Sesion.created_at.desc())))


def _sesiones_vivas(usuario_id: int) -> list:
    return [s for s in _sesiones(usuario_id) if s.revoked_at is None]


def _auditoria(usuario_id: int, accion: str) -> int:
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        return int(db.scalar(
            select(func.count()).select_from(Auditoria).where(
                Auditoria.actor == f"usuario:{usuario_id}",
                Auditoria.accion == accion,
            )
        ) or 0)


# --- 1. Challenge de login -----------------------------------------------------
def test_login_con_totp_confirmado_no_crea_sesion(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    _logout(cliente)

    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/auth/mfa")
    # Sin sesión: la cookie de sesión no existe y la del challenge sí.
    assert COOKIE_SESION not in cliente.cookies
    assert cliente.cookies.get(COOKIE_MFA)
    assert "HttpOnly" in respuesta.headers.get("set-cookie", "")

    desafio = _challenge_actual(usuario.id)
    assert desafio is not None
    assert desafio.purpose == "mfa"
    assert desafio.used_at is None
    assert desafio.mfa_attempts == 0
    assert _sesiones_vivas(usuario.id) == []
    # El token en claro nunca se guarda: la columna es un hash de 64 hex.
    assert re.fullmatch(r"[0-9a-f]{64}", desafio.token_hash)


def test_mfa_sin_desafio_vuelve_al_login(cliente):
    respuesta = cliente.get("/auth/mfa", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/login?error=desafio_expirado"


def test_formulario_mfa_no_se_cachea(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    _logout(cliente)
    _desafio(cliente, usuario)
    respuesta = cliente.get("/auth/mfa")
    assert respuesta.status_code == 200
    assert respuesta.headers["cache-control"] == "no-store"
    assert 'autocomplete="one-time-code"' in respuesta.text
    assert 'inputmode="numeric"' in respuesta.text
    assert 'pattern="[0-9]{6}"' in respuesta.text
    assert "usar un código de respaldo" in respuesta.text.lower()


# --- 2. Código correcto --------------------------------------------------------
def test_codigo_correcto_abre_sesion_y_marca_mfa_at(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    _logout(cliente)

    token = _desafio(cliente, usuario)
    respuesta = cliente.post(
        "/auth/mfa",
        data={"csrf_token": token, "codigo": _codigo_valido(usuario.id),
              "next": "/qaqc"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/qaqc"
    assert cliente.cookies.get(COOKIE_SESION)
    assert COOKIE_MFA not in cliente.cookies

    sesiones = _sesiones_vivas(usuario.id)
    assert len(sesiones) == 1
    assert sesiones[0].mfa_at is not None
    assert _paso_usado(usuario.id) is not None
    assert _challenge_actual(usuario.id).used_at is not None
    assert _auditoria(usuario.id, "mfa_ok") >= 1
    # La sesión nueva navega normalmente.
    assert cliente.get("/", follow_redirects=False).status_code == 200


# --- 3. Intentos fallidos ------------------------------------------------------
def test_cinco_codigos_incorrectos_invalidan_el_desafio(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    _logout(cliente)

    token = _desafio(cliente, usuario)
    # Los primeros cuatro fallos conservan el challenge con el error genérico.
    for _ in range(4):
        respuesta = cliente.post(
            "/auth/mfa",
            data={"csrf_token": token, "codigo": _codigo_ajeno()},
            follow_redirects=False,
        )
        assert respuesta.status_code == 200
        assert "Código incorrecto o expirado." in respuesta.text
        assert COOKIE_SESION not in cliente.cookies
    assert _challenge_actual(usuario.id).used_at is None

    # El quinto fallo invalida el challenge y devuelve al login.
    quinto = cliente.post(
        "/auth/mfa",
        data={"csrf_token": token, "codigo": _codigo_ajeno()},
        follow_redirects=False,
    )
    assert quinto.status_code == 303
    assert quinto.headers["location"] == "/auth/login?error=mfa_bloqueado"
    assert COOKIE_MFA not in cliente.cookies
    assert _challenge_actual(usuario.id).used_at is not None
    assert _auditoria(usuario.id, "mfa_fail") == 5

    # Sin cookie de challenge ya no se puede seguir intentando.
    despues = cliente.post("/auth/mfa", data={"csrf_token": token,
                                              "codigo": _codigo_ajeno()},
                           follow_redirects=False)
    assert despues.status_code == 303
    assert despues.headers["location"] == "/auth/login?error=desafio_expirado"
    assert _sesiones_vivas(usuario.id) == []


def test_limite_bloquea_aunque_el_ultimo_codigo_sea_correcto(cliente, fabrica):
    """Tras 5 fallos hace falta empezar de nuevo por la contraseña."""
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    _logout(cliente)

    token = _desafio(cliente, usuario)
    for _ in range(5):
        cliente.post("/auth/mfa", data={"csrf_token": token,
                                        "codigo": _codigo_ajeno()},
                     follow_redirects=False)
    respuesta = cliente.post(
        "/auth/mfa",
        data={"csrf_token": token, "codigo": _codigo_valido(usuario.id)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/auth/login")
    assert _sesiones_vivas(usuario.id) == []


# --- 4. Replay -----------------------------------------------------------------
def test_replay_del_mismo_codigo_se_rechaza(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    _logout(cliente)

    codigo = _codigo_valido(usuario.id)
    token = _desafio(cliente, usuario)
    primero = cliente.post("/auth/mfa",
                           data={"csrf_token": token, "codigo": codigo},
                           follow_redirects=False)
    assert primero.status_code == 303
    _logout(cliente)

    token = _desafio(cliente, usuario)
    repetido = cliente.post("/auth/mfa",
                            data={"csrf_token": token, "codigo": codigo},
                            follow_redirects=False)
    assert repetido.status_code == 200
    assert "Código incorrecto o expirado." in repetido.text
    assert COOKIE_SESION not in cliente.cookies


# --- 5. Códigos de respaldo ----------------------------------------------------
def test_codigo_de_respaldo_sirve_una_sola_vez(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    codigos = _enrolar(cliente, usuario)
    _logout(cliente)

    token = _desafio(cliente, usuario)
    primero = cliente.post("/auth/mfa",
                           data={"csrf_token": token, "respaldo": codigos[0]},
                           follow_redirects=False)
    assert primero.status_code == 303
    assert cliente.cookies.get(COOKIE_SESION)
    assert _auditoria(usuario.id, "mfa_ok") >= 1
    _logout(cliente)

    token = _desafio(cliente, usuario)
    segundo = cliente.post("/auth/mfa",
                           data={"csrf_token": token, "respaldo": codigos[0]},
                           follow_redirects=False)
    assert segundo.status_code == 200
    assert "Código incorrecto o expirado." in segundo.text
    assert COOKIE_SESION not in cliente.cookies


def test_codigo_de_respaldo_admite_minusculas_y_espacios(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    codigos = _enrolar(cliente, usuario)
    _logout(cliente)

    desprolijo = " " + codigos[1].lower().replace("-", " ") + " "
    token = _desafio(cliente, usuario)
    respuesta = cliente.post("/auth/mfa",
                             data={"csrf_token": token, "respaldo": desprolijo},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert cliente.cookies.get(COOKIE_SESION)


# --- 6. Política TOTP sin enrolar ---------------------------------------------
def test_politica_totp_sin_enrolar_obliga_a_seguridad(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp", rol="supervision")
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/seguridad"
    assert cliente.cookies.get(COOKIE_SESION)

    portada = cliente.get("/", follow_redirects=False)
    assert portada.status_code == 303
    assert portada.headers["location"] == "/auth/seguridad"

    for ruta in ("/entregas", "/cruces", "/qaqc", "/exportar"):
        prohibida = cliente.get(ruta, follow_redirects=False)
        assert prohibida.status_code == 303, ruta
        assert prohibida.headers["location"] == "/auth/seguridad"

    pantalla = cliente.get("/auth/seguridad")
    assert pantalla.status_code == 200
    assert "Activar autenticador" in pantalla.text
    assert "hasta completar la activación" in pantalla.text


def test_politica_any_sin_enrolar_no_bloquea(cliente, fabrica):
    # ``disabled`` es la política "sin segundo factor": el login no se desvía.
    # Con ``any`` y correo registrado el flujo pasa a /auth/mfa (Fase A4).
    usuario = fabrica(mfa_policy="disabled")
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/"
    assert cliente.get("/", follow_redirects=False).status_code == 200


# --- 7. Sesiones revocables ----------------------------------------------------
def _sesion_ajena(usuario_id: int):
    """Crea una sesión viva del usuario como si viniera de otro dispositivo."""
    from datetime import datetime, timedelta

    from app.core.database import SessionLocal
    from app.core.security import new_token, sha256_hex
    from app.models import Sesion

    with SessionLocal() as db:
        fila = Sesion(
            token_hash=sha256_hex(new_token(32)),
            usuario_id=usuario_id,
            created_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            ip="10.0.0.99",
            user_agent="Otro dispositivo de prueba",
        )
        db.add(fila)
        db.commit()
        db.refresh(fila)
        return fila.id


def test_revocar_otra_sesion_la_cierra(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    propia = _sesiones(usuario.id)[0]
    ajena_id = _sesion_ajena(usuario.id)

    token = _csrf_sesion(cliente)
    # La otra sesión aparece en el listado con su botón de revocación.
    assert f"/auth/sesiones/{ajena_id}/revocar" in cliente.get("/auth/seguridad").text
    respuesta = cliente.post(f"/auth/sesiones/{ajena_id}/revocar",
                             data={"csrf_token": token},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/seguridad?ok=session_revoked"

    revocada = [s for s in _sesiones(usuario.id) if s.id == ajena_id][0]
    assert revocada.revoked_at is not None
    assert [s for s in _sesiones(usuario.id) if s.id == propia.id][0].revoked_at is None
    assert _auditoria(usuario.id, "session_revoked") == 1

    # La sesión revocada desaparece del listado.
    pantalla = cliente.get("/auth/seguridad")
    assert str(ajena_id) not in pantalla.text


def test_no_se_puede_revocar_la_sesion_actual(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    propia = _sesiones(usuario.id)[0]

    token = _csrf_sesion(cliente)
    respuesta = cliente.post(f"/auth/sesiones/{propia.id}/revocar",
                             data={"csrf_token": token},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/seguridad?error=sesion_actual"
    # La sesión sigue viva: se cierra con «Salir», no desde el listado.
    assert cliente.get("/", follow_redirects=False).status_code == 200
    assert [s for s in _sesiones(usuario.id) if s.id == propia.id][0].revoked_at is None


def test_no_se_puede_revocar_la_sesion_de_otro_usuario(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    otro = fabrica(mfa_policy="any")
    _enrolar(cliente, usuario)
    ajena_id = _sesion_ajena(otro.id)

    token = _csrf_sesion(cliente)
    respuesta = cliente.post(f"/auth/sesiones/{ajena_id}/revocar",
                             data={"csrf_token": token},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/auth/seguridad?error=sesion_no_encontrada"
    assert [s for s in _sesiones(otro.id) if s.id == ajena_id][0].revoked_at is None


# --- 8. Enrolamiento: QR, clave manual y códigos de un solo uso ----------------
def test_iniciar_muestra_qr_y_clave_manual(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    assert _login(cliente, usuario).status_code == 303
    token = _csrf_sesion(cliente)

    respuesta = cliente.post("/auth/seguridad/totp/iniciar",
                             data={"csrf_token": token}, follow_redirects=False)
    assert respuesta.status_code == 200
    assert respuesta.headers["cache-control"] == "no-store"
    assert "<svg" in respuesta.text
    assert "clave manual" in respuesta.text.lower()

    secreto = _secreto(usuario.id)
    agrupado = " ".join(secreto[i:i + 4] for i in range(0, len(secreto), 4))
    assert agrupado in respuesta.text
    # Ni el secreto crudo ni el otpauth:// completo salen en el HTML.
    assert f'value="{secreto}"' not in respuesta.text
    assert "otpauth://" not in respuesta.text


def test_codigo_invalido_conserva_el_secreto_pendiente(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    assert _login(cliente, usuario).status_code == 303
    token = _csrf_sesion(cliente)
    cliente.post("/auth/seguridad/totp/iniciar", data={"csrf_token": token},
                 follow_redirects=False)
    secreto = _secreto(usuario.id)

    respuesta = cliente.post("/auth/seguridad/totp/confirmar",
                             data={"csrf_token": token, "codigo": _codigo_ajeno()},
                             follow_redirects=False)
    assert respuesta.status_code == 200
    assert "no es válido" in respuesta.text
    assert "<svg" in respuesta.text  # el QR sigue disponible
    assert _secreto(usuario.id) == secreto
    assert _sesiones_vivas(usuario.id)[0].mfa_at is None


def test_codigos_de_respaldo_se_muestran_una_sola_vez(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    codigos = _enrolar(cliente, usuario)
    assert len(set(codigos)) == 10

    pantalla = cliente.get("/auth/seguridad")
    assert pantalla.status_code == 200
    assert pantalla.headers["cache-control"] == "no-store"
    for codigo in codigos:
        assert codigo not in pantalla.text
    assert PATRON_CODIGO_RESPALDO.findall(pantalla.text) == []
    # El conteo sí se muestra.
    assert "10" in pantalla.text

    # En la base solo hay HMAC: ningún código en claro.
    from app.core.database import SessionLocal
    from app.models import RecoveryCode

    with SessionLocal() as db:
        filas = list(db.scalars(
            select(RecoveryCode).where(RecoveryCode.usuario_id == usuario.id)))
    assert len(filas) == 10
    assert all(re.fullmatch(r"[0-9a-f]{64}", fila.code_hash) for fila in filas)
    assert not any(codigo in fila.code_hash for fila in filas for codigo in codigos)


def test_regenerar_respaldo_exige_totp_y_muestra_codigos_nuevos(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    codigos = _enrolar(cliente, usuario)
    token = _csrf_sesion(cliente)

    fallido = cliente.post("/auth/seguridad/respaldo/regenerar",
                           data={"csrf_token": token, "codigo": _codigo_ajeno()},
                           follow_redirects=False)
    assert fallido.status_code == 200
    assert "Código incorrecto o ya utilizado" in fallido.text

    ok = cliente.post("/auth/seguridad/respaldo/regenerar",
                      data={"csrf_token": token,
                            "codigo": _codigo_valido(usuario.id)},
                      follow_redirects=False)
    assert ok.status_code == 200
    nuevos = PATRON_CODIGO_RESPALDO.findall(ok.text)
    assert len(nuevos) == 10
    assert set(nuevos).isdisjoint(codigos)
    assert _auditoria(usuario.id, "mfa_recovery_regenerated") == 1

    # Los códigos anteriores quedaron invalidados.
    _logout(cliente)
    token = _desafio(cliente, usuario)
    viejo = cliente.post("/auth/mfa",
                         data={"csrf_token": token, "respaldo": codigos[0]},
                         follow_redirects=False)
    assert viejo.status_code == 200
    assert COOKIE_SESION not in cliente.cookies


def test_seguridad_exige_sesion(cliente):
    respuesta = cliente.get("/auth/seguridad", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/auth/login")


# --- 9. CSRF -------------------------------------------------------------------
def test_los_post_nuevos_exigen_csrf(cliente, fabrica):
    # Política «totp» ya enrolada: el gate deja pasar todas las rutas y lo
    # único que puede rechazar el POST es el CSRF.
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)

    for ruta in ("/auth/seguridad/totp/iniciar",
                 "/auth/seguridad/totp/confirmar",
                 "/auth/seguridad/respaldo/regenerar",
                 f"/auth/sesiones/{uuid.uuid4()}/revocar"):
        respuesta = cliente.post(ruta, data={"codigo": "123456"},
                                 follow_redirects=False)
        assert respuesta.status_code == 403, ruta


def test_el_post_del_desafio_exige_csrf(cliente, fabrica):
    usuario = fabrica(mfa_policy="totp")
    _enrolar(cliente, usuario)
    _logout(cliente)
    _desafio(cliente, usuario)

    respuesta = cliente.post("/auth/mfa", data={"codigo": _codigo_ajeno()},
                             follow_redirects=False)
    assert respuesta.status_code == 403
