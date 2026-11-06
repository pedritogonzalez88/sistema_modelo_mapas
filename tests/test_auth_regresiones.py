"""Regresiones de políticas de autenticación que no necesitan PostgreSQL.

Cubren dos fallos concretos:

- el login podía enviar correos de MFA sin respetar los topes de la cuenta/IP;
- cambiar el rol no actualizaba la política de segundo factor ni la fuente del
  rol (una cuenta promovida a admin quedaba sin TOTP y un rol manual sobre una
  cuenta LDAP se revertía en el siguiente login).

Se usa SQLite en memoria con las tablas implicadas.
"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.auth import service
from app.core.database import Base
from app.core.security import utcnow
from app.models import Auditoria, MfaEmailCode, Usuario


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Base.metadata.tables[n] for n in
                ("usuarios", "auditoria", "mfa_email_codes", "auth_tokens")],
    )
    monkeypatch.setattr(service.settings, "auth_pepper", "pepper-de-pruebas")
    with Session(engine) as sesion:
        yield sesion


def _usuario(db: Session, *, username: str, rol: str = "consulta",
             mfa_policy: str = "disabled", role_source: str = "manual",
             activo: bool = True) -> Usuario:
    fila = Usuario(nombre=username, username=username, email=f"{username}@x.test",
                   rol=rol, mfa_policy=mfa_policy, role_source=role_source,
                   is_active=activo)
    db.add(fila)
    db.commit()
    db.refresh(fila)
    return fila


def test_crear_desafio_no_invalida_el_challenge_anterior(db):
    """Un login nuevo no debe tumbar el challenge legítimo ya emitido (A-5)."""
    from app.models import AuthToken

    usuario = _usuario(db, username="dos-desafios")
    token_uno = service.crear_desafio(db, usuario)
    token_dos = service.crear_desafio(db, usuario)

    filas = db.scalars(select(AuthToken).where(
        AuthToken.usuario_id == usuario.id,
        AuthToken.purpose == "mfa")).all()
    assert len([fila for fila in filas if fila.used_at is None]) == 2
    assert service.cargar_desafio(db, token_uno) is not None
    assert service.cargar_desafio(db, token_dos) is not None


# --- A-5: topes de envío dentro de emitir_codigo_email ------------------------
def test_emitir_codigo_aplica_cooldown_y_no_invalida_el_codigo_vigente(
        db, monkeypatch):
    monkeypatch.setattr(service.mailer, "enviar", lambda *a, **k: True)
    usuario = _usuario(db, username="cooldown")

    assert service.emitir_codigo_email(db, usuario, ip="203.0.113.9") is True
    codigo = db.scalar(select(MfaEmailCode).where(
        MfaEmailCode.usuario_id == usuario.id))
    assert codigo is not None and codigo.consumed_at is None

    # Segundo intento inmediato: el cooldown de 60 s lo bloquea y NO consume
    # ni invalida el código anterior.
    assert service.emitir_codigo_email(db, usuario, ip="203.0.113.9") is False
    assert db.scalar(select(func.count()).select_from(MfaEmailCode)) == 1
    db.refresh(codigo)
    assert codigo.consumed_at is None


def test_emitir_codigo_aplica_el_tope_por_cuenta(db, monkeypatch):
    monkeypatch.setattr(service.mailer, "enviar", lambda *a, **k: True)
    usuario = _usuario(db, username="tope-cuenta")
    viejo = utcnow() - timedelta(minutes=10)
    for indice in range(service.MAX_ENVIOS_CORREO_HORA):
        db.add(MfaEmailCode(
            usuario_id=usuario.id, code_hash=f"hash-{indice}", purpose="mfa",
            expires_at=utcnow() + timedelta(minutes=10), created_at=viejo,
            attempts=0, ip="203.0.113.9",
        ))
    db.commit()

    assert service.emitir_codigo_email(db, usuario, ip="203.0.113.9") is False


def test_emitir_codigo_aplica_el_tope_por_ip(db, monkeypatch):
    monkeypatch.setattr(service.mailer, "enviar", lambda *a, **k: True)
    otra = _usuario(db, username="ruido")
    nueva = _usuario(db, username="nueva")
    viejo = utcnow() - timedelta(minutes=10)
    for indice in range(service.MAX_ENVIOS_CORREO_POR_IP_HORA):
        db.add(MfaEmailCode(
            usuario_id=otra.id, code_hash=f"ruido-{indice}", purpose="mfa",
            expires_at=utcnow() + timedelta(minutes=10), created_at=viejo,
            attempts=0, ip="203.0.113.9",
        ))
    db.commit()

    assert service.emitir_codigo_email(db, nueva, ip="203.0.113.9") is False
    assert db.scalar(select(func.count()).select_from(MfaEmailCode).where(
        MfaEmailCode.usuario_id == nueva.id)) == 0


# --- A-6: el rol impone la política y el rol manual manda ---------------------
def test_promover_a_admin_impone_totp_y_marca_rol_manual(db):
    objetivo = _usuario(db, username="promovida", rol="consulta",
                        mfa_policy="disabled", role_source="ldap")

    usuario, errores = service.cambiar_rol_usuario(
        db, None, objetivo.id, "admin", "Promoción a administración")

    assert errores == []
    assert usuario is not None
    assert usuario.rol == "admin"
    assert usuario.mfa_policy == "totp"
    # El administrador impuso el rol: el siguiente login LDAP no lo revierte.
    assert usuario.role_source == "manual"
    fila = db.scalar(select(Auditoria).where(
        Auditoria.accion == "role_changed"))
    assert fila is not None
    assert fila.detalle["mfa_policy"] == "totp"
    assert fila.detalle["role_source"] == "manual"


def test_degradar_admin_vuelve_a_la_politica_del_rol(db):
    actor = _usuario(db, username="actor", rol="admin", mfa_policy="totp")
    objetivo = _usuario(db, username="degradada", rol="admin",
                        mfa_policy="totp", role_source="ldap")

    usuario, errores = service.cambiar_rol_usuario(
        db, actor, objetivo.id, "consulta", "Rotación de funciones")

    assert errores == []
    assert usuario is not None
    assert usuario.rol == "consulta"
    assert usuario.mfa_policy == "any"
    assert usuario.role_source == "manual"
