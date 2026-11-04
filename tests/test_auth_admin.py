"""Administración de usuarios, auditoría y red interna (Fase A6b).

Cada prueba entra con una cuenta real y comprueba el contrato HTTP completo:
permisos por rol, validaciones del alta, reglas de protección (último
administrador, autodesactivación), revocación de sesiones, resets asistidos,
vista/exportación de auditoría y la restricción por CIDR.

Requieren PostgreSQL y el ciclo de vida completo de la aplicación:

    uv run pytest -m integration tests/test_auth_admin.py -q
"""
import contextlib
import json
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration

COOKIE_SESION = "eet_session"  # cookie_secure=False en las pruebas
COOKIE_CSRF = "eet_csrf"
PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]*)"')
PATRON_TEMPORAL = re.compile(r'id="password-temporal">([^<]+)<')
PASSWORD = "Clave-Segura-2026"


# --- Infraestructura -----------------------------------------------------------
@pytest.fixture(scope="module")
def cliente():
    """Cliente único del módulo; la sesión se rehace en cada prueba."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def panel(auth_activa, monkeypatch):
    """Crea cuentas para la prueba y deja la base como estaba.

    ``admin_internal_only=False`` es obligatorio: el host de ``TestClient`` es
    ``testclient`` (no una IP) y con la restricción activa todas las rutas de
    administración responderían 403. El caso de red interna se prueba aparte
    sobreescribiendo la configuración.
    """
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import (Auditoria, AuthToken, LoginAttempt, MfaEmailCode,
                            MfaTotp, RecoveryCode, Sesion, Usuario)

    monkeypatch.setattr(auth_activa, "admin_internal_only", False)

    creados: list[int] = []
    with SessionLocal() as db:
        corte_auditoria = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0
        corte_usuarios = db.scalar(select(func.max(Usuario.id))) or 0

    def usuario(rol: str = "consulta", *, letra: str | None = None,
                username: str | None = None) -> SimpleNamespace:
        sufijo = uuid.uuid4().hex[:10]
        username = username or f"adm-{rol}-{sufijo}"
        email = f"{username}@ejemplo.org"
        nombre = f"Prueba {sufijo}"
        with SessionLocal() as db:
            fila = Usuario(
                nombre=nombre,
                username=username,
                email=email,
                rol=rol,
                letra_terna=letra,
                auth_provider="local",
                password_hash=hash_password(PASSWORD),
                is_active=True,
                must_change_password=False,
                # Sin segundo factor: estas pruebas miden administración.
                mfa_policy="disabled",
            )
            db.add(fila)
            db.flush()
            identificador = fila.id
            db.commit()
        creados.append(identificador)
        return SimpleNamespace(id=identificador, username=username, email=email,
                               password=PASSWORD, rol=rol, nombre=nombre,
                               letra_terna=letra)

    yield SimpleNamespace(usuario=usuario)

    with SessionLocal() as db:
        # Las pruebas también crean cuentas desde la UI: se borra todo lo que
        # apareció después del corte, no solo lo que armó la fábrica.
        sobrantes = list(db.scalars(
            select(Usuario.id).where(Usuario.id > corte_usuarios)).all())
        for identificador in set(creados) | set(sobrantes):
            for modelo in (Sesion, AuthToken, MfaTotp, RecoveryCode,
                           MfaEmailCode):
                db.execute(delete(modelo).where(
                    modelo.usuario_id == identificador))
            db.execute(delete(Usuario).where(Usuario.id == identificador))
        db.execute(delete(Auditoria).where(Auditoria.id > corte_auditoria))
        db.execute(delete(LoginAttempt).where(LoginAttempt.id > corte_intentos))
        db.commit()


# --- Sesión --------------------------------------------------------------------
def _csrf_anonimo(cliente: TestClient) -> str:
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _csrf_de_sesion(cliente: TestClient) -> str:
    """Token CSRF del shell autenticado (cualquier rol puede abrir «/»)."""
    html = cliente.get("/").text
    encontrado = PATRON_CSRF.search(html)
    assert encontrado, "la página autenticada debe incluir el campo CSRF"
    return encontrado.group(1)


def _entrar(cliente: TestClient, usuario: SimpleNamespace) -> None:
    cliente.cookies.clear()
    respuesta = cliente.post(
        "/auth/login",
        data={"login": usuario.username, "password": usuario.password,
              "csrf_token": _csrf_anonimo(cliente)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303, respuesta.text
    assert cliente.cookies.get(COOKIE_SESION)


@contextlib.contextmanager
def _sesion(cliente: TestClient, usuario: SimpleNamespace):
    _entrar(cliente, usuario)
    yield cliente


def _post(cliente: TestClient, ruta: str, token: str, **datos):
    """POST de formulario con CSRF y sin seguir la redirección."""
    datos["csrf_token"] = token
    return cliente.post(ruta, data=datos, follow_redirects=False)


def _db_usuario(usuario_id: int):
    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        return db.get(Usuario, usuario_id)


def _auditoria(accion: str, entidad_id: int | None = None):
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        consulta = select(Auditoria).where(Auditoria.accion == accion)
        if entidad_id is not None:
            consulta = consulta.where(Auditoria.entidad_id == str(entidad_id))
        return db.scalars(consulta.order_by(Auditoria.id.desc())).all()


# --- 1. Permisos ---------------------------------------------------------------
def test_solo_admin_entra_a_gestion_de_usuarios(cliente, panel):
    admin = panel.usuario("admin")
    with _sesion(cliente, admin):
        respuesta = cliente.get("/auth/usuarios")
        assert respuesta.status_code == 200
        assert "Crear usuario" in respuesta.text
        assert "Cuentas registradas" in respuesta.text

    for rol in ("supervision", "cargador", "consulta"):
        with _sesion(cliente, panel.usuario(rol)):
            respuesta = cliente.get("/auth/usuarios")
            assert respuesta.status_code == 403, rol
            assert "text/html" in respuesta.headers["content-type"]


def test_listado_de_usuarios_usa_dialogo_de_acciones(cliente, panel):
    admin = panel.usuario("admin")
    with _sesion(cliente, admin):
        respuesta = cliente.get("/auth/usuarios")
    assert respuesta.status_code == 200
    assert "<dialog" in respuesta.text
    assert 'data-modal-open="acciones-' in respuesta.text
    assert 'aria-haspopup="dialog"' in respuesta.text
    assert "data-modal-close" in respuesta.text


# --- 2. Alta y validaciones ----------------------------------------------------
def test_alta_admin_rechaza_mfa_no_totp(cliente, panel):
    admin = panel.usuario("admin")
    sufijo = uuid.uuid4().hex[:8]

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, "/auth/usuarios", token,
            nombre="Admin sin TOTP", username=f"adm-{sufijo}",
            email=f"adm-{sufijo}@ejemplo.org", rol="admin", letra="",
            mfa_policy="disabled", password=PASSWORD, confirmacion=PASSWORD,
        )

    assert respuesta.status_code == 200
    assert "TOTP" in respuesta.text


def test_alta_de_usuario_y_validaciones(cliente, panel):
    from app.core.security import verify_password
    from app.models import Usuario

    admin = panel.usuario("admin")
    sufijo = uuid.uuid4().hex[:8]
    username = f"alta-{sufijo}"
    email = f"{username}@ejemplo.org"

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)

        # Alta válida (mfa_policy vacía => «any» para consulta).
        creado = _post(
            cliente, "/auth/usuarios", token,
            nombre="Alta Válida", username=username, email=email,
            rol="consulta", letra="", mfa_policy="",
            password=PASSWORD, confirmacion=PASSWORD,
        )
        assert creado.status_code == 200, creado.text
        assert "Usuario creado" in creado.text
        assert username in creado.text

        from app.core.database import SessionLocal

        with SessionLocal() as db:
            fila = db.scalar(
                select(Usuario).where(Usuario.username == username))
        assert fila is not None
        assert fila.rol == "consulta"
        assert fila.auth_provider == "local"
        assert fila.must_change_password is True
        assert fila.mfa_policy == "any"
        assert verify_password(fila.password_hash, PASSWORD)[0]

        # Duplicado por usuario (comparación sin distinguir mayúsculas).
        duplicado_usuario = _post(
            cliente, "/auth/usuarios", token,
            nombre="Otra Persona", username=username.upper(),
            email=f"otro-{sufijo}@ejemplo.org", rol="consulta",
            password=PASSWORD, confirmacion=PASSWORD,
        )
        assert duplicado_usuario.status_code == 200
        assert "Ya existe una cuenta con el usuario" in duplicado_usuario.text
        # El formulario conserva los valores escritos.
        assert 'value="Otra Persona"' in duplicado_usuario.text

        # Duplicado por correo.
        duplicado_correo = _post(
            cliente, "/auth/usuarios", token,
            nombre="Otra Persona", username=f"otro-{sufijo}",
            email=email.upper(), rol="consulta",
            password=PASSWORD, confirmacion=PASSWORD,
        )
        assert "Ya existe una cuenta con el correo" in duplicado_correo.text

        # Asistente sin letra.
        sin_letra = _post(
            cliente, "/auth/usuarios", token,
            nombre="Sin Letra", username=f"sinletra-{sufijo}",
            email=f"sinletra-{sufijo}@ejemplo.org", rol="asistente",
            letra="", password=PASSWORD, confirmacion=PASSWORD,
        )
        assert "letra de terna" in sin_letra.text

        # Contraseña corta.
        corta = _post(
            cliente, "/auth/usuarios", token,
            nombre="Clave Corta", username=f"corta-{sufijo}",
            email=f"corta-{sufijo}@ejemplo.org", rol="consulta",
            password="Corta-2026", confirmacion="Corta-2026",
        )
        assert "al menos 12 caracteres" in corta.text

    assert any(evento.detalle.get("rol") == "consulta"
               for evento in _auditoria("user_created", fila.id))


def test_alta_con_password_generada_se_muestra_una_vez(cliente, panel):
    from app.core.security import verify_password
    from app.models import Usuario

    admin = panel.usuario("admin")
    sufijo = uuid.uuid4().hex[:8]
    username = f"gen-{sufijo}"

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, "/auth/usuarios", token,
            nombre="Clave Generada", username=username,
            email=f"{username}@ejemplo.org", rol="cargador",
            generar_password="1",
        )

    assert respuesta.status_code == 200
    encontrado = PATRON_TEMPORAL.search(respuesta.text)
    assert encontrado, "la contraseña generada debe mostrarse en la respuesta"
    temporal = encontrado.group(1).strip()
    assert len(temporal) >= 12
    assert respuesta.text.count(temporal) == 1, "se muestra una sola vez"

    from app.core.database import SessionLocal

    with SessionLocal() as db:
        fila = db.scalar(select(Usuario).where(Usuario.username == username))
    assert fila.must_change_password is True
    assert verify_password(fila.password_hash, temporal)[0]


# --- 3. Cambio de rol ----------------------------------------------------------
def test_cambio_de_rol_con_motivo(cliente, panel):
    admin = panel.usuario("admin")
    objetivo = panel.usuario("consulta")

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)

        sin_motivo = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/rol", token,
            rol="cargador", motivo="",
        )
        assert sin_motivo.status_code == 200
        assert "Indique el motivo" in sin_motivo.text
        assert _db_usuario(objetivo.id).rol == "consulta"

        propio = _post(
            cliente, f"/auth/usuarios/{admin.id}/rol", token,
            rol="consulta", motivo="Prueba",
        )
        assert propio.status_code == 200
        assert "No puede cambiar su propio rol" in propio.text
        assert _db_usuario(admin.id).rol == "admin"

        cambio = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/rol", token,
            rol="cargador", letra="", motivo="Rotación de funciones",
        )
        assert cambio.status_code == 303
        assert cambio.headers["location"] == "/auth/usuarios?ok=usuario_actualizado"

    assert _db_usuario(objetivo.id).rol == "cargador"
    filas = _auditoria("role_changed", objetivo.id)
    assert filas
    assert filas[0].detalle["de"] == "consulta"
    assert filas[0].detalle["a"] == "cargador"
    assert filas[0].detalle["motivo"] == "Rotación de funciones"


def test_rol_asistente_exige_letra_en_el_cambio(cliente, panel):
    admin = panel.usuario("admin")
    objetivo = panel.usuario("consulta")

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/rol", token,
            rol="asistente", letra="", motivo="Pasa a terna",
        )
        assert respuesta.status_code == 200
        assert "letra de terna" in respuesta.text

        con_letra = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/rol", token,
            rol="asistente", letra="B", motivo="Pasa a terna",
        )
        assert con_letra.status_code == 303

    assert _db_usuario(objetivo.id).letra_terna == "B"


# --- 4. Último administrador ---------------------------------------------------
def test_ultimo_admin_activo_no_se_degrada_ni_desactiva(cliente, panel):
    from app.auth import service
    from app.core.database import SessionLocal
    from app.models import Usuario

    admin = panel.usuario("admin")
    otro = panel.usuario("admin")

    # Por HTTP: con otro administrador activo, la degradación se permite.
    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{otro.id}/rol", token,
            rol="consulta", motivo="Reorganización del equipo",
        )
        assert respuesta.status_code == 303
    assert _db_usuario(otro.id).rol == "consulta"

    # A nivel de servicio (sin actor): el último administrador activo se protege.
    with SessionLocal() as db:
        aislados = list(db.scalars(select(Usuario).where(
            Usuario.rol == "admin",
            Usuario.is_active.is_(True),
            Usuario.id != admin.id,
        )))
        ids = [fila.id for fila in aislados]
        for fila in aislados:
            fila.is_active = False
        db.commit()
        try:
            _, errores_rol = service.cambiar_rol_usuario(
                db, None, admin.id, "consulta", "Prueba de último admin")
            assert any("último administrador" in error
                       for error in errores_rol), errores_rol

            _, errores_estado = service.cambiar_estado_usuario(
                db, None, admin.id, False, "Prueba de último admin")
            assert any("último administrador" in error
                       for error in errores_estado), errores_estado
        finally:
            for fila in db.scalars(select(Usuario).where(Usuario.id.in_(ids))):
                fila.is_active = True
            db.commit()

    assert _db_usuario(admin.id).rol == "admin"
    assert _db_usuario(admin.id).is_active is True


# --- 5. Autodesactivación ------------------------------------------------------
def test_no_puede_desactivar_su_propia_cuenta(cliente, panel):
    admin = panel.usuario("admin")

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{admin.id}/estado", token,
            activo="0", motivo="Prueba de autodesactivación",
        )
        assert respuesta.status_code == 200
        assert "No puede desactivar su propia cuenta" in respuesta.text

    assert _db_usuario(admin.id).is_active is True


def test_reset_de_password_propio_esta_prohibido(cliente, panel):
    admin = panel.usuario("admin")

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{admin.id}/reset-password", token,
            motivo="Prueba",
        )
        assert respuesta.status_code == 200
        assert "No puede restablecer su propia contraseña" in respuesta.text
        # La contraseña original sigue valiendo.
        cliente.cookies.clear()
        vuelta = cliente.post(
            "/auth/login",
            data={"login": admin.username, "password": admin.password,
                  "csrf_token": _csrf_anonimo(cliente)},
            follow_redirects=False,
        )
        assert vuelta.status_code == 303


# --- 6. Baja lógica revoca sesiones -------------------------------------------
def test_desactivar_revoca_sesiones_y_audita(cliente, panel):
    from datetime import timedelta

    from app.auth.service import crear_sesion
    from app.core.database import SessionLocal
    from app.core.security import sha256_hex, utcnow
    from app.models import AuthToken, Sesion, Usuario

    admin = panel.usuario("admin")
    objetivo = panel.usuario("consulta")

    with SessionLocal() as db:
        usuario_db = db.get(Usuario, objetivo.id)
        crear_sesion(db, usuario_db)
        crear_sesion(db, usuario_db)
        # Challenge de 2FA pendiente: también debe quedar anulado.
        db.add(AuthToken(
            usuario_id=objetivo.id, purpose="mfa",
            token_hash=sha256_hex("challenge-de-prueba"),
            expires_at=utcnow() + timedelta(minutes=5),
        ))
        db.commit()

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/estado", token,
            activo="0", motivo="Desvinculación del proyecto",
        )
        assert respuesta.status_code == 303

    with SessionLocal() as db:
        sesiones = db.scalars(
            select(Sesion).where(Sesion.usuario_id == objetivo.id)).all()
        desafios = db.scalars(
            select(AuthToken).where(AuthToken.usuario_id == objetivo.id)).all()
    assert sesiones
    assert all(sesion.revoked_at is not None for sesion in sesiones)
    assert desafios and all(fila.used_at is not None for fila in desafios)
    assert _db_usuario(objetivo.id).is_active is False

    filas = _auditoria("user_disabled", objetivo.id)
    assert filas
    assert filas[0].detalle["motivo"] == "Desvinculación del proyecto"
    assert filas[0].detalle["sesiones_revocadas"] >= 2

    # Reactivar deja la cuenta operativa y audita el evento inverso.
    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        reactivada = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/estado", token,
            activo="1", motivo="Reincorporación al proyecto",
        )
        assert reactivada.status_code == 303
    assert _db_usuario(objetivo.id).is_active is True
    filas = _auditoria("user_enabled", objetivo.id)
    assert filas
    assert filas[0].detalle["motivo"] == "Reincorporación al proyecto"


# --- 7. Reset de contraseña ----------------------------------------------------
def test_reset_password_muestra_temporal_una_vez(cliente, panel):
    from app.auth.service import crear_sesion
    from app.core.database import SessionLocal
    from app.core.security import verify_password
    from app.models import Sesion, Usuario

    admin = panel.usuario("admin")
    objetivo = panel.usuario("consulta")

    with SessionLocal() as db:
        crear_sesion(db, db.get(Usuario, objetivo.id))

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/reset-password", token,
            motivo="Olvidó su contraseña",
        )

    assert respuesta.status_code == 200
    encontrado = PATRON_TEMPORAL.search(respuesta.text)
    assert encontrado, "el reset debe mostrar la contraseña temporal"
    temporal = encontrado.group(1).strip()
    assert len(temporal) >= 12
    assert respuesta.text.count(temporal) == 1

    with SessionLocal() as db:
        fila = db.get(Usuario, objetivo.id)
        assert fila.must_change_password is True
        assert verify_password(fila.password_hash, temporal)[0]
        sesiones = db.scalars(
            select(Sesion).where(Sesion.usuario_id == objetivo.id)).all()
    assert sesiones and all(s.revoked_at is not None for s in sesiones)

    filas = _auditoria("password_reset_admin", objetivo.id)
    assert filas
    assert filas[0].detalle["motivo"] == "Olvidó su contraseña"
    serializado = json.dumps(filas[0].detalle, ensure_ascii=False)
    assert temporal not in serializado
    assert "password" not in serializado.casefold()


# --- 8. Reset de segundo factor -----------------------------------------------
def test_reset_mfa_borra_totp_y_codigos(cliente, panel):
    from app.core.database import SessionLocal
    from app.core.security import generar_secreto, secret_box, utcnow
    from app.models import MfaTotp, RecoveryCode, Usuario

    admin = panel.usuario("admin")
    objetivo = panel.usuario("consulta")

    with SessionLocal() as db:
        usuario_db = db.get(Usuario, objetivo.id)
        usuario_db.mfa_policy = "totp"
        usuario_db.mfa_enrolled_at = utcnow()
        blob, key_id = secret_box().encrypt(generar_secreto())
        db.add(MfaTotp(usuario_id=objetivo.id, secret_enc=blob, key_id=key_id,
                       confirmed_at=utcnow()))
        db.add(RecoveryCode(usuario_id=objetivo.id,
                            code_hash="a" * 64))
        db.commit()

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{objetivo.id}/reset-mfa", token,
            motivo="Cambió de teléfono",
        )
        assert respuesta.status_code == 303
        assert respuesta.headers["location"] == "/auth/usuarios?ok=mfa_reseteado"

    with SessionLocal() as db:
        assert db.get(MfaTotp, objetivo.id) is None
        codigos = db.scalars(select(RecoveryCode).where(
            RecoveryCode.usuario_id == objetivo.id)).all()
        assert codigos == []
        assert db.get(Usuario, objetivo.id).mfa_enrolled_at is None

    filas = _auditoria("mfa_reset_admin", objetivo.id)
    assert filas
    assert filas[0].detalle["motivo"] == "Cambió de teléfono"
    assert filas[0].detalle["totp_eliminado"] is True


def test_reset_mfa_propio_esta_prohibido(cliente, panel):
    admin = panel.usuario("admin")

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        respuesta = _post(
            cliente, f"/auth/usuarios/{admin.id}/reset-mfa", token,
            motivo="Prueba",
        )
        assert respuesta.status_code == 200
        assert "No puede reiniciar su propio segundo factor" in respuesta.text


# --- 9. Auditoría y CSV --------------------------------------------------------
def test_auditoria_para_admin_y_supervision_y_csv(cliente, panel):
    admin = panel.usuario("admin")
    supervision = panel.usuario("supervision")
    cargador = panel.usuario("cargador")

    with _sesion(cliente, admin):
        pagina = cliente.get("/auth/auditoria")
        assert pagina.status_code == 200
        assert "Eventos registrados" in pagina.text
        assert "Descargar CSV" in pagina.text

        csv = cliente.get("/auth/auditoria.csv")
        assert csv.status_code == 200
        assert csv.headers["content-type"].startswith("text/csv")
        assert "charset=utf-8" in csv.headers["content-type"]
        assert csv.headers["content-disposition"] == \
            "attachment; filename=auditoria_eet.csv"
        assert csv.content.startswith(b"\xef\xbb\xbf"), "debe llevar BOM UTF-8"
        cabecera = csv.content.decode("utf-8-sig").splitlines()[0]
        assert cabecera == ("fecha,actor,accion,entidad,entidad_id,ip,"
                            "user_agent,detalle")

    with _sesion(cliente, supervision):
        assert cliente.get("/auth/auditoria").status_code == 200
        assert cliente.get("/auth/auditoria.csv").status_code == 200

    with _sesion(cliente, cargador):
        assert cliente.get("/auth/auditoria").status_code == 403
        assert cliente.get("/auth/auditoria.csv").status_code == 403


def test_auditoria_no_permite_borrar_ni_modificar(cliente, panel):
    admin = panel.usuario("admin")
    with _sesion(cliente, admin):
        # Solo hay GET: un POST o DELETE sobre la vista no tiene ruta.
        assert cliente.post("/auth/auditoria",
                            follow_redirects=False).status_code in (403, 405)
        assert cliente.delete("/auth/auditoria").status_code in (403, 405)
        pagina = cliente.get("/auth/auditoria").text
        # La vista no publica ningún formulario POST hacia la auditoría.
        assert 'method="post" action="/auth/auditoria' not in pagina


def test_filtros_de_auditoria_por_accion(cliente, panel):
    admin = panel.usuario("admin")
    objetivo = panel.usuario("consulta")

    with _sesion(cliente, admin):
        token = _csrf_de_sesion(cliente)
        _post(cliente, f"/auth/usuarios/{objetivo.id}/rol", token,
              rol="cargador", motivo="Filtro de auditoría")
        respuesta = cliente.get(
            f"/auth/auditoria?accion=role_changed&q={objetivo.id}")
        assert respuesta.status_code == 200
        assert "role_changed" in respuesta.text
        assert str(objetivo.id) in respuesta.text


# --- 10. Red interna -----------------------------------------------------------
def test_restriccion_por_red_interna(cliente, panel, monkeypatch):
    from app.core.config import settings

    admin = panel.usuario("admin")

    with _sesion(cliente, admin):
        # El fixture deja la restricción apagada.
        assert cliente.get("/auth/usuarios").status_code == 200

        # Activa con un CIDR que no incluye al host de TestClient.
        monkeypatch.setattr(settings, "admin_internal_only", True)
        monkeypatch.setattr(settings, "internal_cidrs", "10.0.0.0/8")
        negado = cliente.get("/auth/usuarios")
        assert negado.status_code == 403
        assert "solo está disponible desde la red interna" in negado.text
        assert cliente.get("/auth/auditoria").status_code == 403

        token = _csrf_de_sesion(cliente)
        negado_post = _post(
            cliente, f"/auth/usuarios/{admin.id}/rol", token,
            rol="consulta", motivo="Prueba de red")
        assert negado_post.status_code == 403

        # Apagada vuelve a permitir.
        monkeypatch.setattr(settings, "admin_internal_only", False)
        assert cliente.get("/auth/usuarios").status_code == 200

        # CIDR total con host no-IP: se rechaza igual (el host debe ser una IP).
        monkeypatch.setattr(settings, "admin_internal_only", True)
        monkeypatch.setattr(settings, "internal_cidrs", "0.0.0.0/0,::/0")
        assert cliente.get("/auth/usuarios").status_code == 403


# --- 11. CSRF ------------------------------------------------------------------
def test_post_sin_csrf_responde_403(cliente, panel):
    admin = panel.usuario("admin")

    with _sesion(cliente, admin):
        alta = cliente.post(
            "/auth/usuarios",
            data={"nombre": "Sin CSRF", "username": "sin-csrf",
                  "email": "sin-csrf@ejemplo.org", "rol": "consulta"},
            follow_redirects=False,
        )
        assert alta.status_code == 403

        rol = cliente.post(
            f"/auth/usuarios/{admin.id}/rol",
            data={"rol": "consulta", "motivo": "Prueba"},
            follow_redirects=False,
        )
        assert rol.status_code == 403


# --- 12. Compatibilidad con la bandera apagada --------------------------------
def test_bandera_apagada_no_rompe_las_rutas_nuevas(cliente):
    from app.core.config import settings

    assert settings.auth_enabled is False
    cliente.cookies.clear()
    usuarios = cliente.get("/auth/usuarios")
    assert usuarios.status_code == 200
    assert "Crear usuario" in usuarios.text
    assert cliente.get("/auth/auditoria").status_code == 200
