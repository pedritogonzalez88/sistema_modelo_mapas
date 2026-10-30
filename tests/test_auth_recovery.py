"""Recuperación de contraseña por correo (Fase A4).

Requieren PostgreSQL y el ciclo de vida completo de la aplicación:

    uv run pytest -m integration tests/test_auth_recovery.py -q

Cobertura:

- la respuesta es idéntica para cuenta local, inexistente, inactiva y LDAP;
- el token restablece una sola vez, expira a los 30 minutos, revoca sesiones y
  envía la confirmación sin token;
- límite de 3 solicitudes por hora y contraseña corta sin consumir el token;
- CSRF en los POST nuevos.
"""
import re
import uuid
from datetime import datetime, timedelta
from email import message_from_bytes
from email import policy as email_policy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration

COOKIE_CSRF = "eet_csrf"
PASSWORD = "Clave-Segura-2026"
NUEVA = "Clave-Nueva-Segura-2026"

PATRON_TOKEN = re.compile(r"token=([A-Za-z0-9_\-]+)")
GENERICO = "Si la cuenta existe, enviamos instrucciones"


# --- Infraestructura -----------------------------------------------------------
@pytest.fixture
def carpeta_correo(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import EmailOutbox

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
def fabrica():
    """Usuarios locales, LDAP e inactivos con teardown completo."""
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import Auditoria, AuthToken, LoginAttempt, Sesion, Usuario

    creados: list[int] = []
    with SessionLocal() as db:
        corte_auditoria = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0

    def _crear(*, proveedor: str = "local", activo: bool = True,
               password: str = PASSWORD) -> SimpleNamespace:
        sufijo = uuid.uuid4().hex[:10]
        username = f"reset-{sufijo}"
        email = f"{username}@ejemplo.test"
        with SessionLocal() as db:
            usuario = Usuario(
                nombre=f"Usuario Reset {sufijo}",
                username=username,
                email=email,
                rol="consulta",
                auth_provider=proveedor,
                password_hash=hash_password(password) if proveedor == "local" else None,
                is_active=activo,
                must_change_password=False,
                mfa_policy="disabled",
            )
            db.add(usuario)
            db.flush()
            identificador = usuario.id
            db.commit()
        creados.append(identificador)
        return SimpleNamespace(id=identificador, username=username, email=email,
                               password=password, proveedor=proveedor)

    yield _crear

    with SessionLocal() as db:
        actores = [f"usuario:{identificador}" for identificador in creados]
        for identificador in creados:
            db.execute(delete(AuthToken).where(
                AuthToken.usuario_id == identificador))
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
def _csrf(cliente: TestClient) -> str:
    respuesta = cliente.get("/auth/recuperar")
    assert respuesta.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _solicitar(cliente: TestClient, identificador: str, token: str | None = None):
    return cliente.post(
        "/auth/recuperar",
        data={"csrf_token": token or _csrf(cliente), "identificador": identificador},
        follow_redirects=False,
    )


def _archivos(carpeta) -> list:
    return sorted(carpeta.glob("*.eml"),
                  key=lambda p: (p.stat().st_mtime_ns, p.name))


def _mensaje(carpeta, indice: int = -1):
    archivos = _archivos(carpeta)
    assert archivos, "debe existir al menos un correo"
    return message_from_bytes(archivos[indice].read_bytes(),
                              policy=email_policy.default)


def _texto(mensaje) -> str:
    parte = mensaje.get_body(preferencelist=("plain",))
    assert parte is not None
    return parte.get_content()


def _token_de(carpeta) -> str:
    encontrado = PATRON_TOKEN.search(_texto(_mensaje(carpeta)))
    assert encontrado, "el correo debe incluir el enlace con token"
    return encontrado.group(1)


def _tokens(usuario_id: int, proposito: str = "reset") -> list:
    from app.core.database import SessionLocal
    from app.models import AuthToken

    with SessionLocal() as db:
        return list(db.scalars(
            select(AuthToken).where(
                AuthToken.usuario_id == usuario_id,
                AuthToken.purpose == proposito,
            ).order_by(AuthToken.id)))


def _sesiones_vivas(usuario_id: int) -> list:
    from app.core.database import SessionLocal
    from app.models import Sesion

    with SessionLocal() as db:
        return list(db.scalars(
            select(Sesion).where(Sesion.usuario_id == usuario_id,
                                 Sesion.revoked_at.is_(None))))


def _sesion_manual(usuario_id: int) -> None:
    from app.core.database import SessionLocal
    from app.core.security import new_token, sha256_hex
    from app.models import Sesion

    with SessionLocal() as db:
        db.add(Sesion(
            token_hash=sha256_hex(new_token(32)),
            usuario_id=usuario_id,
            created_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            ip="10.0.0.55",
            user_agent="Navegador de prueba",
        ))
        db.commit()


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


# --- 1. No enumeración ---------------------------------------------------------
def test_respuesta_identica_para_local_inexistente_inactivo_y_ldap(
        cliente, fabrica, carpeta_correo):
    local = fabrica()
    ldap = fabrica(proveedor="ldap")
    inactivo = fabrica(activo=False)

    token = _csrf(cliente)
    respuestas = [
        _solicitar(cliente, local.email, token),
        _solicitar(cliente, "no-existe@ejemplo.test", token),
        _solicitar(cliente, ldap.email, token),
        _solicitar(cliente, inactivo.email, token),
    ]
    for respuesta in respuestas:
        assert respuesta.status_code == 200
        assert GENERICO in respuesta.text
    # Mismo texto exacto en los cuatro casos: nada delata la existencia.
    assert len({respuesta.text for respuesta in respuestas}) == 1

    # Solo la cuenta local activa recibió correo y generó token.
    assert len(_archivos(carpeta_correo)) == 1
    assert len(_tokens(local.id)) == 1
    assert _tokens(ldap.id) == []
    assert _tokens(inactivo.id) == []
    # La auditoría no guarda el token ni el enlace.
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        detalle = db.scalar(
            select(Auditoria.detalle).where(
                Auditoria.actor == f"usuario:{local.id}",
                Auditoria.accion == "password_reset_requested",
            ).order_by(Auditoria.id.desc())
        )
    assert detalle == {"enviado": True, "motivo": "enviado"}


# --- 2. Flujo completo ---------------------------------------------------------
def test_token_valido_restablece_revoca_sesiones_y_confirma(cliente, fabrica,
                                                            carpeta_correo):
    from app.core.database import SessionLocal
    from app.core.security import verify_password
    from app.models import Usuario

    usuario = fabrica()
    _sesion_manual(usuario.id)
    assert len(_sesiones_vivas(usuario.id)) == 1

    solicitud = _solicitar(cliente, usuario.email)
    assert solicitud.status_code == 200
    assert GENERICO in solicitud.text
    token = _token_de(carpeta_correo)
    assert "http://pruebas.local/auth/restablecer?token=" in _texto(
        _mensaje(carpeta_correo))

    formulario = cliente.get(f"/auth/restablecer?token={token}")
    assert formulario.status_code == 200
    assert formulario.headers["cache-control"] == "no-store"
    assert "Nueva contraseña" in formulario.text
    assert 'autocomplete="new-password"' in formulario.text
    assert f'value="{token}"' in formulario.text

    aplicado = cliente.post(
        "/auth/restablecer",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF), "token": token,
              "nueva": NUEVA, "confirmacion": NUEVA},
        follow_redirects=False,
    )
    assert aplicado.status_code == 303
    assert aplicado.headers["location"] == "/auth/login?ok=password_reset"

    with SessionLocal() as db:
        fila = db.get(Usuario, usuario.id)
        assert verify_password(fila.password_hash, NUEVA)[0]
        assert not verify_password(fila.password_hash, PASSWORD)[0]
        assert fila.password_changed_at is not None
        assert fila.must_change_password is False

    assert _sesiones_vivas(usuario.id) == []
    assert _tokens(usuario.id)[0].used_at is not None
    assert _auditoria(usuario.id, "password_reset_ok") == 1

    # Se envió la confirmación, sin token ni enlace de acción.
    assert len(_archivos(carpeta_correo)) == 2
    confirmacion = _texto(_mensaje(carpeta_correo))
    assert "se restableció" in confirmacion
    assert token not in confirmacion
    assert "/auth/restablecer" not in confirmacion

    # Un solo uso: el enlace ya no sirve.
    repetido = cliente.get(f"/auth/restablecer?token={token}")
    assert repetido.status_code == 200
    assert "El enlace no es válido o expiró." in repetido.text


def test_token_expira_a_los_30_minutos(cliente, fabrica, carpeta_correo):
    from app.core.database import SessionLocal
    from app.models import AuthToken

    usuario = fabrica()
    assert _solicitar(cliente, usuario.email).status_code == 200
    token = _token_de(carpeta_correo)
    assert token

    with SessionLocal() as db:
        fila = db.scalar(select(AuthToken).where(
            AuthToken.usuario_id == usuario.id))
        assert (fila.expires_at - fila.created_at) >= timedelta(minutes=29)
        fila.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()

    formulario = cliente.get(f"/auth/restablecer?token={token}")
    assert formulario.status_code == 200
    assert "El enlace no es válido o expiró." in formulario.text
    assert 'action="/auth/restablecer"' not in formulario.text


def test_password_corta_no_consume_el_token(cliente, fabrica, carpeta_correo):
    usuario = fabrica()
    assert _solicitar(cliente, usuario.email).status_code == 200
    token = _token_de(carpeta_correo)

    corta = cliente.post(
        "/auth/restablecer",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF), "token": token,
              "nueva": "corta", "confirmacion": "corta"},
        follow_redirects=False,
    )
    assert corta.status_code == 200
    assert "al menos 12 caracteres" in corta.text
    assert _tokens(usuario.id)[0].used_at is None

    # El enlace sigue sirviendo para corregir.
    assert cliente.get(f"/auth/restablecer?token={token}").status_code == 200
    correcta = cliente.post(
        "/auth/restablecer",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF), "token": token,
              "nueva": NUEVA, "confirmacion": NUEVA},
        follow_redirects=False,
    )
    assert correcta.status_code == 303


def test_confirmacion_distinta_no_consume_el_token(cliente, fabrica,
                                                   carpeta_correo):
    usuario = fabrica()
    assert _solicitar(cliente, usuario.email).status_code == 200
    token = _token_de(carpeta_correo)

    respuesta = cliente.post(
        "/auth/restablecer",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF), "token": token,
              "nueva": NUEVA, "confirmacion": NUEVA + "x"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 200
    assert "no coinciden" in respuesta.text
    assert _tokens(usuario.id)[0].used_at is None


# --- 3. Límites ----------------------------------------------------------------
def test_limite_de_tres_solicitudes_por_hora(cliente, fabrica, carpeta_correo):
    usuario = fabrica()
    token = _csrf(cliente)

    respuestas = [_solicitar(cliente, usuario.email, token) for _ in range(4)]
    for respuesta in respuestas:
        assert respuesta.status_code == 200
        assert GENERICO in respuesta.text
    assert len({respuesta.text for respuesta in respuestas}) == 1

    # Solo se emitieron tres enlaces y tres correos; el cuarto no envía.
    assert len(_tokens(usuario.id)) == 3
    assert len(_archivos(carpeta_correo)) == 3
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        motivos = [fila["motivo"] for fila in db.scalars(
            select(Auditoria.detalle).where(
                Auditoria.actor == f"usuario:{usuario.id}",
                Auditoria.accion == "password_reset_requested",
            ).order_by(Auditoria.id))]
    assert motivos == ["enviado", "enviado", "enviado", "limite"]


def test_token_invalido_no_restablece(cliente, fabrica):
    usuario = fabrica()
    respuesta = cliente.get("/auth/restablecer?token=token-inventado")
    assert respuesta.status_code == 200
    assert "El enlace no es válido o expiró." in respuesta.text

    post = cliente.post(
        "/auth/restablecer",
        data={"csrf_token": cliente.cookies.get(COOKIE_CSRF),
              "token": "token-inventado", "nueva": NUEVA,
              "confirmacion": NUEVA},
        follow_redirects=False,
    )
    assert post.status_code == 400
    assert "El enlace no es válido o expiró." in post.text
    assert _tokens(usuario.id) == []


# --- 4. CSRF -------------------------------------------------------------------
def test_los_post_nuevos_exigen_csrf(cliente, fabrica, carpeta_correo):
    usuario = fabrica()
    assert _solicitar(cliente, usuario.email).status_code == 200
    token = _token_de(carpeta_correo)

    sin_csrf_recuperar = cliente.post(
        "/auth/recuperar", data={"identificador": usuario.email},
        follow_redirects=False)
    assert sin_csrf_recuperar.status_code == 403

    sin_csrf_restablecer = cliente.post(
        "/auth/restablecer",
        data={"token": token, "nueva": NUEVA, "confirmacion": NUEVA},
        follow_redirects=False)
    assert sin_csrf_restablecer.status_code == 403
    assert _tokens(usuario.id)[0].used_at is None


# --- 5. Pantallas públicas -----------------------------------------------------
def test_pantallas_de_recuperacion_son_publicas(cliente):
    recuperar = cliente.get("/auth/recuperar", follow_redirects=False)
    assert recuperar.status_code == 200
    assert 'autocomplete="email"' in recuperar.text

    restablecer = cliente.get("/auth/restablecer", follow_redirects=False)
    assert restablecer.status_code == 200
    assert "Solicitar un enlace nuevo" in restablecer.text
