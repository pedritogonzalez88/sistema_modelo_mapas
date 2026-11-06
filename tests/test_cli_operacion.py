"""Comandos de operación de la CLI (A6.12).

Cubre los tres subcomandos nuevos:

- ``re-cifrar``: migra secretos TOTP y cuerpos de la cola de ``v1`` a ``v2``
  (round-trip completo y ``key_id`` actualizado), es idempotente y no imprime
  secretos.
- ``estado-auth``: responde con el resumen operativo y no falla sin datos.
- ``reenviar-correo``: informa revisados/enviados/fallidos y sale con 0 cuando
  no hay pendientes.

Los casos de base de datos están marcados ``integration`` (PostgreSQL del
contenedor); los de criptografía pura corren en la suite unitaria.
"""
from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings


# --- Unidad: re-cifrado criptográfico -------------------------------------------
def _cajas():
    """Par de cofres: uno solo ``v1`` y otro con ``v2`` delante de ``v1``."""
    from app.core.security import SecretBox

    clave_v1 = Fernet.generate_key().decode()
    clave_v2 = Fernet.generate_key().decode()
    return (SecretBox([("v1", clave_v1)]),
            SecretBox([("v2", clave_v2), ("v1", clave_v1)]))


def test_recifrar_blob_migra_v1_a_v2():
    from app.cli import _recifrar_blob

    viejo, nuevo = _cajas()
    secreto = "JBSWY3DPEHPK3PXPJBSWY3DP"
    blob, key_id = viejo.encrypt(secreto)

    estado, blob_nuevo, key_id_nuevo = _recifrar_blob(nuevo, blob, key_id)

    assert estado == "migrado"
    assert key_id_nuevo == "v2"
    assert nuevo.decrypt(blob_nuevo, key_id_nuevo) == secreto


def test_recifrar_blob_es_idempotente():
    from app.cli import _recifrar_blob

    _, nuevo = _cajas()
    blob, key_id = nuevo.encrypt("secreto-ya-vigente")

    assert _recifrar_blob(nuevo, blob, key_id) == ("actual", None, None)


def test_recifrar_blob_sin_clave_antigua_es_fallo():
    from app.cli import _recifrar_blob
    from app.core.security import SecretBox

    viejo, _ = _cajas()
    blob, key_id = viejo.encrypt("secreto-que-no-se-puede-leer")
    solo_v2 = SecretBox([("v2", Fernet.generate_key().decode())])

    assert _recifrar_blob(solo_v2, blob, key_id) == ("fallo", None, None)


def test_recifrar_cuerpo_de_la_cola():
    from app.cli import PREFIJO_CIFRADO, _recifrar_cuerpo

    viejo, nuevo = _cajas()
    token, key_id = viejo.encrypt('{"texto": "código 123456", "html": ""}')
    cuerpo = f"{PREFIJO_CIFRADO}{key_id}:{token.decode('ascii')}"

    estado, nuevo_cuerpo = _recifrar_cuerpo(nuevo, cuerpo)

    assert estado == "migrado"
    assert nuevo_cuerpo.startswith(f"{PREFIJO_CIFRADO}v2:")
    _, _, token_nuevo = nuevo_cuerpo.partition(f"{PREFIJO_CIFRADO}v2:")
    assert nuevo.decrypt(token_nuevo.encode("ascii"), "v2") == \
        '{"texto": "código 123456", "html": ""}'


def test_recifrar_cuerpo_ignora_los_no_cifrados():
    from app.cli import _recifrar_cuerpo

    _, nuevo = _cajas()
    assert _recifrar_cuerpo(nuevo, '{"texto": "sin datos sensibles"}') == \
        ("ajeno", None)
    assert _recifrar_cuerpo(nuevo, "enc:v9:no-es-un-token") == ("fallo", None)


def test_parser_expone_los_comandos_de_operacion():
    from app.cli import _parser, main

    args = _parser().parse_args(["reenviar-correo"])
    assert args.limit == 20
    args = _parser().parse_args(["reenviar-correo", "--limit", "3"])
    assert args.limit == 3
    for comando in ("re-cifrar", "estado-auth"):
        assert _parser().parse_args([comando]).comando == comando
    with pytest.raises(SystemExit):
        main(["reenviar-correo", "--limit", "0"])


# --- Unidad: contrato de salida del comando -------------------------------------
class _SesionGuionada:
    """Sesión falsa: entrega una tanda de filas por cada ``scalars``."""

    def __init__(self, tandas):
        self.tandas = list(tandas)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def scalars(self, _consulta):
        return iter(self.tandas.pop(0) if self.tandas else [])

    def commit(self):
        self.commits += 1


def _ejecutar_re_cifrar(monkeypatch, cofre, *tandas):
    from app import cli

    sesion = _SesionGuionada(tandas)
    monkeypatch.setattr(cli, "SessionLocal", lambda: sesion)
    monkeypatch.setattr(cli, "secret_box", lambda: cofre)
    return cli.main(["re-cifrar"]), sesion


def test_re_cifrar_sale_0_si_todo_migra(monkeypatch, capsys):
    from types import SimpleNamespace

    viejo, nuevo = _cajas()
    blob, key_id = viejo.encrypt("SECRETOTOTP")
    token, _ = viejo.encrypt('{"texto": "hola", "html": ""}')
    fila_totp = SimpleNamespace(secret_enc=blob, key_id=key_id)
    fila_correo = SimpleNamespace(cuerpo=f"enc:v1:{token.decode('ascii')}")

    codigo, sesion = _ejecutar_re_cifrar(monkeypatch, nuevo, [fila_totp],
                                         [fila_correo])

    assert codigo == 0
    assert sesion.commits == 1
    assert fila_totp.key_id == "v2"
    assert fila_correo.cuerpo.startswith("enc:v2:")
    salida = capsys.readouterr().out
    assert "Re-cifrados: 2" in salida and "fallos: 0" in salida
    # Nunca imprime el secreto ni el token.
    assert "SECRETOTOTP" not in salida
    assert token.decode("ascii") not in salida


def test_re_cifrar_sale_1_si_una_clave_no_esta(monkeypatch, capsys):
    from types import SimpleNamespace

    _, ajeno = _cajas()  # cofre con v1/v2 distintos a los de la fila
    token, _ = ajeno.encrypt("no-se-puede-leer")
    fila = SimpleNamespace(cuerpo=f"enc:v9:{token.decode('ascii')}")

    codigo, _ = _ejecutar_re_cifrar(monkeypatch, ajeno, [], [fila])

    assert codigo == 1
    assert "fallos: 1" in capsys.readouterr().out


def test_re_cifrar_sin_claves_sale_2(monkeypatch, capsys):
    from app import cli

    def _sin_claves():
        raise RuntimeError("No hay claves de cifrado configuradas.")

    monkeypatch.setattr(cli, "secret_box", _sin_claves)

    assert cli.main(["re-cifrar"]) == 2
    assert "No hay claves de cifrado utilizables" in capsys.readouterr().out


# --- Integración: comandos completos -------------------------------------------
def test_recifrar_blob_actual_corrupto_es_fallo():
    from app.cli import _recifrar_blob

    _, nuevo = _cajas()
    assert _recifrar_blob(nuevo, b"esto-no-es-un-token", nuevo.key_id)[0] == "fallo"


@pytest.mark.integration
def test_limpiar_sesiones_dias_cero_conserva_las_vivas():
    from datetime import timedelta

    from sqlalchemy import delete

    from app.auth.service import limpiar_expiradas
    from app.core.database import SessionLocal
    from app.core.security import utcnow
    from app.models import Sesion, Usuario

    sufijo = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        usuario = Usuario(
            nombre="Sesiones prueba", username=f"ses-{sufijo}",
            email=f"ses-{sufijo}@ejemplo.test", rol="consulta",
            auth_provider="local", is_active=True,
        )
        db.add(usuario)
        db.flush()
        usuario_id = usuario.id
        ahora = utcnow()
        db.add_all([
            Sesion(token_hash=uuid.uuid4().hex * 2, usuario_id=usuario_id,
                   expires_at=ahora + timedelta(days=1),
                   last_seen_at=ahora - timedelta(days=10)),
            Sesion(token_hash=uuid.uuid4().hex * 2, usuario_id=usuario_id,
                   expires_at=ahora - timedelta(days=1)),
        ])
        db.commit()

    try:
        with SessionLocal() as db:
            assert limpiar_expiradas(
                db, dias=0, usuario_id=usuario_id) == 1
        with SessionLocal() as db:
            from sqlalchemy import func, select
            vivas = db.scalar(select(func.count()).select_from(Sesion).where(
                Sesion.usuario_id == usuario_id)) or 0
            assert vivas == 1
    finally:
        with SessionLocal() as db:
            db.execute(delete(Sesion).where(Sesion.usuario_id == usuario_id))
            db.execute(delete(Usuario).where(Usuario.id == usuario_id))
            db.commit()


@pytest.mark.integration
def test_re_cifrar_migra_filas_reales(monkeypatch, capsys):
    """Un secreto TOTP y un cuerpo ``enc:v1`` pasan a ``v2`` en la base."""
    from sqlalchemy import delete

    from app.cli import main
    from app.core.database import SessionLocal
    from app.core.security import SecretBox, hash_password
    from app.models import EmailOutbox, MfaTotp, Usuario

    clave_v1 = Fernet.generate_key().decode()
    clave_v2 = Fernet.generate_key().decode()
    viejo = SecretBox([("v1", clave_v1)])
    nuevo = SecretBox([("v2", clave_v2), ("v1", clave_v1)])
    sufijo = uuid.uuid4().hex[:8]
    secreto = "MFRGGZDFMZTWQ2LK"

    token_cuerpo, _ = viejo.encrypt('{"texto": "código 654321", "html": ""}')
    cuerpo = f"enc:v1:{token_cuerpo.decode('ascii')}"

    with SessionLocal() as db:
        usuario = Usuario(
            nombre=f"Operación {sufijo}",
            username=f"op-{sufijo}",
            email=f"op-{sufijo}@ejemplo.test",
            rol="consulta",
            auth_provider="local",
            password_hash=hash_password("Clave-Segura-2026"),
            is_active=True,
        )
        db.add(usuario)
        db.flush()
        usuario_id = usuario.id
        blob, key_id = viejo.encrypt(secreto)
        db.add(MfaTotp(usuario_id=usuario_id, secret_enc=blob, key_id=key_id))
        fila = EmailOutbox(destinatario=f"op-{sufijo}@ejemplo.test",
                           asunto="Re-cifrado", cuerpo=cuerpo, intentos=1)
        db.add(fila)
        db.commit()
        outbox_id = fila.id

    monkeypatch.setattr(settings, "fernet_keys", f"v2:{clave_v2},v1:{clave_v1}")
    try:
        codigo = main(["re-cifrar"])
        salida = capsys.readouterr().out
        # El código refleja los fallos: en una base de desarrollo puede haber
        # cuerpos históricos cifrados con una clave ya retirada, que este
        # comando no puede migrar y reporta como fallo (nunca los altera).
        assert codigo == (0 if "fallos: 0" in salida else 1)
        assert "Clave vigente: v2" in salida
        assert "Re-cifrados: 2" in salida
        assert secreto not in salida
        assert cuerpo not in salida

        with SessionLocal() as db:
            totp = db.get(MfaTotp, usuario_id)
            assert totp.key_id == "v2"
            assert nuevo.decrypt(totp.secret_enc, totp.key_id) == secreto

            outbox = db.get(EmailOutbox, outbox_id)
            assert outbox.cuerpo.startswith("enc:v2:")
            _, _, token_nuevo = outbox.cuerpo.partition("enc:v2:")
            assert nuevo.decrypt(token_nuevo.encode("ascii"), "v2") == \
                '{"texto": "código 654321", "html": ""}'

        # Segunda pasada: las filas ya están en la clave vigente (idempotente).
        codigo = main(["re-cifrar"])
        salida = capsys.readouterr().out
        assert codigo == (0 if "fallos: 0" in salida else 1)
        assert "Re-cifrados: 0" in salida
    finally:
        with SessionLocal() as db:
            db.execute(delete(EmailOutbox).where(EmailOutbox.id == outbox_id))
            db.execute(delete(MfaTotp).where(MfaTotp.usuario_id == usuario_id))
            db.execute(delete(Usuario).where(Usuario.id == usuario_id))
            db.commit()


@pytest.mark.integration
def test_semilla_no_reactiva_ni_deja_sesiones_al_cambiar_password():
    from datetime import timedelta

    from sqlalchemy import delete, select

    from app.auth import audit
    from app.cli import _aplicar_semilla
    from app.core.database import SessionLocal
    from app.core.security import hash_password, utcnow
    from app.models import Auditoria, Sesion, Usuario

    sufijo = uuid.uuid4().hex[:8]
    username = f"seed-{sufijo}"
    email = f"{username}@ejemplo.test"
    datos = {"nombre": "Semilla Prueba", "username": username, "email": email,
             "rol": "consulta", "letra_terna": None}

    with SessionLocal() as db:
        usuario = Usuario(
            nombre=datos["nombre"], username=username, email=email,
            rol="consulta", auth_provider="local",
            password_hash=hash_password("Clave-Segura-2026"),
            is_active=False,
        )
        db.add(usuario)
        db.flush()
        usuario_id = usuario.id
        db.commit()

    try:
        with SessionLocal() as db:
            _aplicar_semilla(db, [(datos, db.get(Usuario, usuario_id))],
                             con_password=False)
        with SessionLocal() as db:
            assert db.get(Usuario, usuario_id).is_active is False

        with SessionLocal() as db:
            _aplicar_semilla(db, [(datos, db.get(Usuario, usuario_id))],
                             con_password=False, reactivar=True)
        with SessionLocal() as db:
            assert db.get(Usuario, usuario_id).is_active is True
            db.add(Sesion(
                token_hash=uuid.uuid4().hex * 2,
                usuario_id=usuario_id,
                expires_at=utcnow() + timedelta(days=1),
            ))
            db.commit()

        with SessionLocal() as db:
            _aplicar_semilla(db, [(datos, db.get(Usuario, usuario_id))],
                             con_password=True)
        with SessionLocal() as db:
            sesion = db.scalar(select(Sesion).where(
                Sesion.usuario_id == usuario_id))
            assert sesion is not None and sesion.revoked_at is not None
            assert db.get(Usuario, usuario_id).must_change_password is True
    finally:
        with SessionLocal() as db:
            db.execute(delete(Sesion).where(Sesion.usuario_id == usuario_id))
            db.execute(delete(Auditoria).where(
                Auditoria.entidad == "usuario",
                Auditoria.entidad_id == str(usuario_id),
                Auditoria.actor == audit.ACTOR_CLI))
            db.execute(delete(Usuario).where(Usuario.id == usuario_id))
            db.commit()


@pytest.mark.integration
def test_estado_auth_no_falla_sin_datos(capsys):
    from app.cli import main

    assert main(["estado-auth"]) == 0
    salida = capsys.readouterr().out
    assert "Estado de autenticación" in salida
    assert "Usuarios:" in salida
    assert "Sesiones activas:" in salida
    assert "Códigos de respaldo sin usar:" in salida
    assert "Correo:" in salida


@pytest.mark.integration
def test_reenviar_correo_sin_pendientes(monkeypatch, capsys):
    from app.auth import mailer
    from app.cli import main

    # Si la base tuviera pendientes, se despachan sin red ni salida real.
    monkeypatch.setattr(mailer, "_entregar", lambda *_a, **_k: None)

    assert main(["reenviar-correo", "--limit", "5"]) == 0
    salida = capsys.readouterr().out
    assert "Correos revisados:" in salida
    assert "fallidos:" in salida
