"""Código por correo como segundo factor (Fase A4).

Requieren PostgreSQL y el ciclo de vida completo de la aplicación:

    uv run pytest -m integration tests/test_auth_email.py -q

El backend de correo se cambia a ``file`` sobre un ``tmp_path``: cada prueba
lee el ``.eml`` real, extrae el código del cuerpo y lo usa como lo haría el
usuario. Nunca se consulta el código desde la base (solo existe su HMAC) ni se
lee de un log.
"""
import logging
import re
import uuid
from datetime import datetime, timedelta
from email import message_from_bytes
from email import policy as email_policy
from types import SimpleNamespace

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration

COOKIE_SESION = "eet_session"
COOKIE_CSRF = "eet_csrf"
COOKIE_MFA = "eet_mfa"

PATRON_CODIGO = re.compile(r"\b(\d{6})\b")
PASSWORD = "Clave-Segura-2026"


# --- Infraestructura -----------------------------------------------------------
@pytest.fixture
def carpeta_correo(tmp_path, monkeypatch):
    """Backend ``file`` apuntando a una carpeta temporal por prueba.

    También limpia las filas de ``email_outbox`` creadas durante la prueba
    (algunas no pasan por la fábrica de usuarios).
    """
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import EmailOutbox
    from sqlalchemy import delete, func, select

    destino = tmp_path / "mail"
    monkeypatch.setattr(settings, "email_backend", "file")
    monkeypatch.setattr(settings, "email_dir", str(destino))
    monkeypatch.setattr(settings, "public_base_url", "http://pruebas.local")
    with SessionLocal() as db:
        corte = db.scalar(select(func.max(EmailOutbox.id))) or 0
    yield destino
    with SessionLocal() as db:
        db.execute(delete(EmailOutbox).where(EmailOutbox.id > corte))
        db.commit()


@pytest.fixture
def cliente(auth_activa, carpeta_correo):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def fabrica(carpeta_correo):
    """Crea cuentas locales y limpia todo lo generado al terminar."""
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import (
        Auditoria,
        AuthToken,
        LoginAttempt,
        MfaEmailCode,
        EmailOutbox,
        MfaTotp,
        Sesion,
        Usuario,
    )

    creados: list[int] = []
    with SessionLocal() as db:
        corte_auditoria = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0
        corte_correo = db.scalar(select(func.max(EmailOutbox.id))) or 0

    def _crear(*, rol: str = "consulta", mfa_policy: str = "email",
               activo: bool = True, email: str | None = None,
               con_email: bool = True) -> SimpleNamespace:
        sufijo = uuid.uuid4().hex[:10]
        username = f"correo-{sufijo}"
        correo = email or (f"{username}@ejemplo.test" if con_email else None)
        with SessionLocal() as db:
            usuario = Usuario(
                nombre=f"Usuario Correo {sufijo}",
                username=username,
                email=correo,
                rol=rol,
                auth_provider="local",
                password_hash=hash_password(PASSWORD),
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
                               email=correo, password=PASSWORD, rol=rol,
                               mfa_policy=mfa_policy)

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
        db.execute(delete(EmailOutbox).where(EmailOutbox.id > corte_correo))
        db.commit()


# --- Helpers HTTP --------------------------------------------------------------
def _csrf_anonimo(cliente: TestClient) -> str:
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _login(cliente: TestClient, usuario, password: str | None = None,
           siguiente: str = ""):
    return cliente.post(
        "/auth/login",
        data={"login": usuario.username,
              "password": password or usuario.password,
              "csrf_token": _csrf_anonimo(cliente), "next": siguiente},
        follow_redirects=False,
    )


def _archivos(carpeta) -> list:
    return sorted(carpeta.glob("*.eml"),
                  key=lambda p: (p.stat().st_mtime_ns, p.name))


def _ultimo(carpeta):
    archivos = _archivos(carpeta)
    assert archivos, "el backend file debe haber escrito un .eml"
    return message_from_bytes(archivos[-1].read_bytes(),
                              policy=email_policy.default)


def _texto(mensaje) -> str:
    parte = mensaje.get_body(preferencelist=("plain",))
    assert parte is not None, "el correo debe tener una parte de texto"
    return parte.get_content()


def _codigo(mensaje) -> str:
    encontrado = PATRON_CODIGO.search(_texto(mensaje))
    assert encontrado, "el cuerpo del correo debe incluir el código de 6 dígitos"
    return encontrado.group(1)


def _asegurar_totp(usuario, *, confirmado: bool = True) -> str:
    """Crea la fila TOTP directamente (la app no la expone por API)."""
    from app.core.database import SessionLocal
    from app.core.security import secret_box
    from app.models import MfaTotp, Usuario

    secreto = pyotp.random_base32()
    blob, key_id = secret_box().encrypt(secreto)
    with SessionLocal() as db:
        db.add(MfaTotp(
            usuario_id=usuario.id, secret_enc=blob, key_id=key_id,
            confirmed_at=datetime.utcnow() if confirmado else None,
            last_used_step=None,
        ))
        fila = db.get(Usuario, usuario.id)
        if confirmado and fila.mfa_enrolled_at is None:
            fila.mfa_enrolled_at = datetime.utcnow()
        db.commit()
    return secreto


def _codigos_bd(usuario_id: int) -> list:
    from app.core.database import SessionLocal
    from app.models import MfaEmailCode

    with SessionLocal() as db:
        return list(db.scalars(
            select(MfaEmailCode)
            .where(MfaEmailCode.usuario_id == usuario_id)
            .order_by(MfaEmailCode.id)))


def _outbox_ultimo():
    from app.core.database import SessionLocal
    from app.models import EmailOutbox

    with SessionLocal() as db:
        return db.scalar(
            select(EmailOutbox).order_by(EmailOutbox.id.desc()).limit(1))


def _sesiones_vivas(usuario_id: int) -> list:
    from app.core.database import SessionLocal
    from app.models import Sesion

    with SessionLocal() as db:
        return list(db.scalars(
            select(Sesion).where(Sesion.usuario_id == usuario_id,
                                 Sesion.revoked_at.is_(None))))


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


# --- 1. Flujo completo ---------------------------------------------------------
def test_login_con_politica_email_envia_codigo_y_abre_sesion(cliente, fabrica,
                                                             carpeta_correo):
    usuario = fabrica(mfa_policy="email")
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/auth/mfa")
    assert "metodo=email" in respuesta.headers["location"]
    assert cliente.cookies.get(COOKIE_MFA)
    assert COOKIE_SESION not in cliente.cookies
    assert len(_archivos(carpeta_correo)) == 1

    codigo = _codigo(_ultimo(carpeta_correo))
    assert len(codigo) == 6 and codigo.isdigit()

    formulario = cliente.get("/auth/mfa?metodo=email")
    assert formulario.status_code == 200
    assert formulario.headers["cache-control"] == "no-store"
    assert "Verificación por correo" in formulario.text
    assert 'autocomplete="one-time-code"' in formulario.text
    assert codigo not in formulario.text
    assert "@ejemplo.test" in formulario.text  # destino enmascarado

    token = cliente.cookies.get(COOKIE_CSRF)
    ingreso = cliente.post(
        "/auth/mfa",
        data={"csrf_token": token, "metodo": "email", "codigo": codigo,
              "next": "/qaqc"},
        follow_redirects=False,
    )
    assert ingreso.status_code == 303
    assert ingreso.headers["location"] == "/qaqc"
    assert cliente.cookies.get(COOKIE_SESION)
    assert COOKIE_MFA not in cliente.cookies

    sesiones = _sesiones_vivas(usuario.id)
    assert len(sesiones) == 1 and sesiones[0].mfa_at is not None
    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        # El código por correo no marca el enrolamiento TOTP (M-25).
        assert db.get(Usuario, usuario.id).mfa_enrolled_at is None
    assert _auditoria(usuario.id, "email_otp_ok") == 1
    assert _codigos_bd(usuario.id)[-1].consumed_at is not None
    assert cliente.get("/", follow_redirects=False).status_code == 200


# --- 2. Intentos y expiración --------------------------------------------------
def test_codigo_incorrecto_cinco_veces_invalida_el_codigo(cliente, fabrica,
                                                          carpeta_correo):
    usuario = fabrica(mfa_policy="email")
    assert _login(cliente, usuario).status_code == 303
    token = cliente.cookies.get(COOKIE_CSRF)
    real = _codigo(_ultimo(carpeta_correo))
    falso = "000000" if real != "000000" else "111111"

    for indice in range(4):
        respuesta = cliente.post(
            "/auth/mfa",
            data={"csrf_token": token, "metodo": "email", "codigo": falso},
            follow_redirects=False,
        )
        assert respuesta.status_code == 200
        assert "Código incorrecto o expirado." in respuesta.text
        assert COOKIE_SESION not in cliente.cookies

    quinto = cliente.post(
        "/auth/mfa",
        data={"csrf_token": token, "metodo": "email", "codigo": falso},
        follow_redirects=False,
    )
    assert quinto.status_code == 303
    assert quinto.headers["location"] == "/auth/login?error=mfa_bloqueado"
    assert COOKIE_MFA not in cliente.cookies

    fila = _codigos_bd(usuario.id)[-1]
    assert fila.attempts == 5
    assert fila.consumed_at is not None
    assert _auditoria(usuario.id, "email_otp_fail") == 5
    assert _sesiones_vivas(usuario.id) == []


def test_codigo_expirado_no_abre_sesion(cliente, fabrica, carpeta_correo):
    from app.core.database import SessionLocal
    from app.models import MfaEmailCode

    usuario = fabrica(mfa_policy="email")
    assert _login(cliente, usuario).status_code == 303
    codigo = _codigo(_ultimo(carpeta_correo))

    with SessionLocal() as db:
        fila = db.scalar(select(MfaEmailCode).where(
            MfaEmailCode.usuario_id == usuario.id).order_by(MfaEmailCode.id.desc()))
        fila.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()

    respuesta = cliente.post(
        "/auth/mfa",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF),
              "metodo": "email", "codigo": codigo},
        follow_redirects=False,
    )
    assert respuesta.status_code == 200
    assert "Código incorrecto o expirado." in respuesta.text
    assert COOKIE_SESION not in cliente.cookies
    assert _sesiones_vivas(usuario.id) == []


def test_metodo_email_sin_codigo_emitido_no_valida(cliente, fabrica,
                                                   carpeta_correo):
    """Pedir ?metodo=email no basta: se exige un código emitido en la base."""
    from app.core.database import SessionLocal
    from app.models import MfaEmailCode

    usuario = fabrica(mfa_policy="email")
    assert _login(cliente, usuario).status_code == 303

    with SessionLocal() as db:
        db.execute(
            MfaEmailCode.__table__.update()
            .where(MfaEmailCode.usuario_id == usuario.id)
            .values(consumed_at=datetime.utcnow())
        )
        db.commit()

    respuesta = cliente.post(
        "/auth/mfa",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF),
              "metodo": "email", "codigo": "123456"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 200
    assert "Código incorrecto o expirado." in respuesta.text
    assert COOKIE_SESION not in cliente.cookies


# --- 3. Reenvío: cooldown y límite --------------------------------------------
def test_reenvio_respeta_cooldown_y_limite_horario(cliente, fabrica,
                                                   carpeta_correo):
    from app.core.database import SessionLocal
    from app.models import MfaEmailCode

    usuario = fabrica(mfa_policy="email")
    assert _login(cliente, usuario).status_code == 303
    token = cliente.cookies.get(COOKIE_CSRF)
    assert len(_archivos(carpeta_correo)) == 1

    # Cooldown: un segundo envío inmediato se rechaza y no genera correo.
    inmediato = cliente.post("/auth/mfa/enviar",
                             data={"csrf_token": token},
                             follow_redirects=False)
    assert inmediato.status_code == 200
    assert "Espere unos segundos" in inmediato.text
    assert len(_archivos(carpeta_correo)) == 1

    # Pasado el minuto, el reenvío funciona.
    with SessionLocal() as db:
        db.execute(
            MfaEmailCode.__table__.update()
            .where(MfaEmailCode.usuario_id == usuario.id)
            .values(created_at=datetime.utcnow() - timedelta(minutes=2))
        )
        db.commit()
    segundo = cliente.post("/auth/mfa/enviar",
                           data={"csrf_token": token},
                           follow_redirects=False)
    assert segundo.status_code == 200
    assert "Le enviamos un código" in segundo.text
    assert len(_archivos(carpeta_correo)) == 2

    # Límite: cinco códigos en la última hora (los dos reales + tres filas).
    # Se envejecen los reales para superar primero el cooldown de 60 s.
    with SessionLocal() as db:
        db.execute(
            MfaEmailCode.__table__.update()
            .where(MfaEmailCode.usuario_id == usuario.id)
            .values(created_at=datetime.utcnow() - timedelta(minutes=2))
        )
        for _ in range(3):
            db.add(MfaEmailCode(
                usuario_id=usuario.id,
                code_hash="0" * 64,
                purpose="mfa",
                expires_at=datetime.utcnow() + timedelta(minutes=10),
                created_at=datetime.utcnow() - timedelta(minutes=5),
            ))
        db.commit()
    bloqueado = cliente.post("/auth/mfa/enviar",
                             data={"csrf_token": token},
                             follow_redirects=False)
    assert bloqueado.status_code == 200
    assert "máximo de 5 códigos por hora" in bloqueado.text
    assert len(_archivos(carpeta_correo)) == 2


# --- 4. Política «any» ---------------------------------------------------------
def test_any_con_totp_usa_totp_y_permite_pedir_correo(cliente, fabrica,
                                                      carpeta_correo):
    usuario = fabrica(mfa_policy="any")
    _asegurar_totp(usuario)

    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert "metodo=email" not in respuesta.headers["location"]
    assert len(_archivos(carpeta_correo)) == 0

    formulario = cliente.get("/auth/mfa")
    assert formulario.status_code == 200
    assert "Verificación en dos pasos" in formulario.text
    assert "Recibir un código por correo" in formulario.text

    # El usuario pide explícitamente el correo: el servidor emite y envía.
    pedido = cliente.post("/auth/mfa/enviar",
                          data={"csrf_token": cliente.cookies.get(COOKIE_CSRF)},
                          follow_redirects=False)
    assert pedido.status_code == 200
    assert "Verificación por correo" in pedido.text
    assert len(_archivos(carpeta_correo)) == 1

    codigo = _codigo(_ultimo(carpeta_correo))
    ingreso = cliente.post(
        "/auth/mfa",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF),
              "metodo": "email", "codigo": codigo},
        follow_redirects=False,
    )
    assert ingreso.status_code == 303
    assert cliente.cookies.get(COOKIE_SESION)


def test_any_sin_totp_usa_correo(cliente, fabrica, carpeta_correo):
    usuario = fabrica(mfa_policy="any")
    respuesta = _login(cliente, usuario)
    assert respuesta.status_code == 303
    assert "metodo=email" in respuesta.headers["location"]
    assert len(_archivos(carpeta_correo)) == 1
    assert cliente.cookies.get(COOKIE_MFA)


def test_politica_totp_no_ofrece_correo(cliente, fabrica, carpeta_correo):
    usuario = fabrica(mfa_policy="totp")
    _asegurar_totp(usuario)

    assert _login(cliente, usuario).status_code == 303
    formulario = cliente.get("/auth/mfa?metodo=email")
    assert formulario.status_code == 200
    # La query no cambia el método: la política «totp» solo admite autenticador.
    assert "Verificación en dos pasos" in formulario.text
    assert "Recibir un código por correo" not in formulario.text
    respuesta = cliente.post("/auth/mfa/enviar",
                             data={"csrf_token": cliente.cookies.get(COOKIE_CSRF)},
                             follow_redirects=False)
    assert respuesta.status_code == 200
    assert "no tiene un correo disponible" in respuesta.text
    assert len(_archivos(carpeta_correo)) == 0


# --- 5. Outbox, cifrado y logs -------------------------------------------------
def test_outbox_registra_y_cifra_el_cuerpo(cliente, fabrica, carpeta_correo):
    usuario = fabrica(mfa_policy="email")
    assert _login(cliente, usuario).status_code == 303
    codigo = _codigo(_ultimo(carpeta_correo))

    fila = _outbox_ultimo()
    assert fila is not None
    assert fila.destinatario == usuario.email
    assert "Código de acceso" in fila.asunto
    assert fila.cuerpo.startswith("enc:")
    assert codigo not in fila.cuerpo
    assert fila.enviado_en is not None
    assert fila.intentos == 1
    assert fila.ultimo_error is None


def test_ningun_log_de_la_app_contiene_el_codigo(cliente, fabrica,
                                                 carpeta_correo, caplog):
    caplog.set_level(logging.DEBUG)
    usuario = fabrica(mfa_policy="email")
    assert _login(cliente, usuario).status_code == 303
    codigo = _codigo(_ultimo(carpeta_correo))

    formulario = cliente.get("/auth/mfa?metodo=email")
    assert codigo not in formulario.text
    respuesta = cliente.post(
        "/auth/mfa",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF),
              "metodo": "email", "codigo": codigo},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert codigo not in caplog.text


def test_cabeceras_con_salto_de_linea_se_descartan():
    from app.auth import mailer

    assert mailer.enviar("victima@ejemplo.test\r\nBcc: otro@ejemplo.test",
                         "Asunto", "cuerpo") is False
    assert mailer.enviar("victima@ejemplo.test", "Asunto\r\nBcc: x@y.test",
                         "cuerpo") is False


def test_reintentar_pendientes_reenvia_y_descifra(monkeypatch, auth_activa,
                                                  carpeta_correo):
    from app.auth import mailer
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import EmailOutbox

    # SMTP inalcanzable: el envío falla y la fila queda pendiente con el
    # cuerpo cifrado; después se cambia a ``file`` y el reintento la entrega.
    monkeypatch.setattr(settings, "email_backend", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", 1)
    monkeypatch.setattr(settings, "smtp_starttls", False)
    assert mailer.enviar("destino@ejemplo.test", "Aviso de prueba",
                         "texto con 123456", sensible=True) is False

    with SessionLocal() as db:
        fila = db.scalar(select(EmailOutbox).order_by(EmailOutbox.id.desc()))
        assert fila is not None
        assert fila.enviado_en is None
        assert fila.intentos == 1
        assert fila.cuerpo.startswith("enc:")
        assert "123456" not in fila.cuerpo

        monkeypatch.setattr(settings, "email_backend", "file")
        resultado = mailer.reintentar_pendientes(db, limit=5)
        db.refresh(fila)
        assert resultado["enviados"] >= 1
        assert fila.enviado_en is not None
        assert fila.intentos == 2
        assert fila.ultimo_error is None

    mensaje = _ultimo(carpeta_correo)
    assert "123456" in _texto(mensaje)
