"""Pruebas de concurrencia de mecanismos de un solo uso (PostgreSQL)."""
from __future__ import annotations

import threading
import time
import uuid

import pyotp
import pytest
from sqlalchemy import delete

from app.auth import mfa as mfa_service
from app.auth import service
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password, secret_box, utcnow
from app.models import MfaTotp, RecoveryCode, Usuario

pytestmark = pytest.mark.integration


def _usuario(db, sufijo: str) -> Usuario:
    usuario = Usuario(
        nombre=f"Concurrencia {sufijo}",
        username=f"conc-{sufijo}",
        email=f"conc-{sufijo}@ejemplo.test",
        rol="consulta",
        auth_provider="local",
        password_hash=hash_password("Clave-Segura-2026"),
        is_active=True,
        mfa_policy="totp",
    )
    db.add(usuario)
    db.flush()
    return usuario


def test_dos_consumos_concurrentes_del_mismo_respaldo_solo_uno_gana(monkeypatch):
    monkeypatch.setattr(settings, "auth_pepper", "pepper-de-concurrencia")
    sufijo = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        usuario = _usuario(db, sufijo)
        usuario_id = usuario.id
        codigos = mfa_service._emitir_codigos_respaldo(db, usuario)
        db.commit()

    codigo = codigos[0]
    barrera = threading.Barrier(2)
    resultados: list[bool] = []
    errores: list[BaseException] = []

    def consumir() -> None:
        try:
            with SessionLocal() as db:
                usuario = db.get(Usuario, usuario_id)
                barrera.wait(timeout=5)
                resultados.append(
                    service.consumir_codigo_respaldo(db, usuario, codigo))
                db.commit()
        except BaseException as exc:
            errores.append(exc)

    hilos = [threading.Thread(target=consumir) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=15)

    try:
        assert not errores, errores
        assert len(resultados) == 2
        assert sum(1 for resultado in resultados if resultado) == 1
    finally:
        with SessionLocal() as db:
            db.execute(delete(RecoveryCode).where(
                RecoveryCode.usuario_id == usuario_id))
            db.execute(delete(Usuario).where(Usuario.id == usuario_id))
            db.commit()


def test_dos_usos_concurrentes_del_mismo_totp_solo_uno_gana(monkeypatch):
    monkeypatch.setattr(settings, "auth_pepper", "pepper-de-concurrencia")
    monkeypatch.setattr(settings, "secret_key", "clave-de-pruebas-concurrencia")
    sufijo = uuid.uuid4().hex[:8]
    secreto = pyotp.random_base32()
    with SessionLocal() as db:
        usuario = _usuario(db, sufijo)
        usuario_id = usuario.id
        blob, key_id = secret_box().encrypt(secreto)
        db.add(MfaTotp(
            usuario_id=usuario_id, secret_enc=blob, key_id=key_id,
            confirmed_at=utcnow()))
        db.commit()

    codigo = pyotp.TOTP(secreto).at(time.time() + 30)
    barrera = threading.Barrier(2)
    resultados: list[str | None] = []
    errores: list[BaseException] = []

    def verificar() -> None:
        try:
            with SessionLocal() as db:
                usuario = db.get(Usuario, usuario_id)
                barrera.wait(timeout=5)
                resultados.append(
                    mfa_service._verificar_codigo(db, usuario, codigo))
                db.commit()
        except BaseException as exc:
            errores.append(exc)

    hilos = [threading.Thread(target=verificar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=15)

    try:
        assert not errores, errores
        assert len(resultados) == 2
        assert resultados.count("totp") == 1
    finally:
        with SessionLocal() as db:
            db.execute(delete(MfaTotp).where(
                MfaTotp.usuario_id == usuario_id))
            db.execute(delete(RecoveryCode).where(
                RecoveryCode.usuario_id == usuario_id))
            db.execute(delete(Usuario).where(Usuario.id == usuario_id))
            db.commit()
