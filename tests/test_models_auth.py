"""Pruebas de estructura de los modelos de autenticación (Fase A0).

Son unitarias: inspeccionan la metadata de SQLAlchemy sin abrir conexión. Su
objetivo es fijar el contrato del esquema (columnas, índices, claves foráneas y
valores por defecto) antes de que la Fase A1 escriba la lógica de login.
"""
from sqlalchemy import Boolean, DateTime, Integer, String, Text

from app.models import (
    Auditoria,
    AuthToken,
    Base,
    EmailOutbox,
    LoginAttempt,
    MfaEmailCode,
    MfaTotp,
    RecoveryCode,
    Sesion,
    Usuario,
)

TABLAS_AUTH = (
    "sesiones",
    "mfa_totp",
    "mfa_email_codes",
    "recovery_codes",
    "auth_tokens",
    "login_attempts",
    "email_outbox",
)


def _columnas(modelo) -> dict:
    return {c.name: c for c in modelo.__table__.columns}


def _indices(modelo) -> dict:
    return {i.name: i for i in modelo.__table__.indexes}


# --- Usuario -------------------------------------------------------------------
def test_usuario_conserva_los_campos_previos():
    columnas = _columnas(Usuario)

    for nombre in ("id", "nombre", "rol", "letra_terna"):
        assert nombre in columnas
    # La letra de terna sigue siendo opcional y el rol obligatorio.
    assert columnas["letra_terna"].nullable is True
    assert columnas["rol"].nullable is False
    assert columnas["nombre"].nullable is False


def test_usuario_agrega_los_campos_de_identidad():
    columnas = _columnas(Usuario)

    esperadas = {
        "username", "email", "auth_provider", "password_hash",
        "password_changed_at", "must_change_password", "is_active",
        "mfa_policy", "mfa_enrolled_at", "role_source", "ldap_dn",
        "external_id", "last_login_at", "failed_attempts", "locked_until",
        "created_at", "updated_at",
    }
    assert esperadas <= set(columnas)


def test_usuario_no_exige_credenciales_para_las_filas_semilla():
    columnas = _columnas(Usuario)

    for nombre in ("username", "email", "password_hash", "ldap_dn",
                   "external_id", "last_login_at", "locked_until"):
        assert columnas[nombre].nullable is True, nombre
        assert columnas[nombre].server_default is None, nombre

    assert isinstance(columnas["username"].type, String)
    assert columnas["username"].type.length == 100
    assert isinstance(columnas["email"].type, String)
    assert columnas["email"].type.length == 255
    assert isinstance(columnas["password_hash"].type, Text)


def test_usuario_columnas_obligatorias_tienen_default_en_servidor():
    """Sin ``server_default`` la migración fallaría sobre filas existentes."""
    columnas = _columnas(Usuario)

    for nombre in ("auth_provider", "must_change_password", "is_active",
                   "mfa_policy", "role_source", "failed_attempts",
                   "created_at", "updated_at"):
        assert columnas[nombre].nullable is False, nombre
        assert columnas[nombre].server_default is not None, nombre

    assert isinstance(columnas["must_change_password"].type, Boolean)
    assert isinstance(columnas["is_active"].type, Boolean)
    assert isinstance(columnas["failed_attempts"].type, Integer)


def test_usuario_unico_por_indice_funcional_en_minusculas():
    indices = _indices(Usuario)

    assert "uq_usuarios_username_lower" in indices
    assert "uq_usuarios_email_lower" in indices
    assert indices["uq_usuarios_username_lower"].unique is True
    assert indices["uq_usuarios_email_lower"].unique is True

    # La expresión del índice debe ser lower(<columna>): unicidad sin CITEXT.
    for nombre, columna in (("uq_usuarios_username_lower", "username"),
                            ("uq_usuarios_email_lower", "email")):
        expresion = str(next(iter(indices[nombre].expressions))).lower()
        assert "lower" in expresion
        assert columna in expresion

    # No se crean restricciones UNIQUE planas que dupliquen los índices.
    unicos = {u.name for u in Usuario.__table__.constraints
              if u.__class__.__name__ == "UniqueConstraint"}
    assert not any(n and "username" in n for n in unicos)


# --- Tablas nuevas -------------------------------------------------------------
def test_tablas_nuevas_registradas():
    for tabla in TABLAS_AUTH:
        assert tabla in Base.metadata.tables, tabla


def test_sesion_guarda_solo_el_hash_del_token():
    columnas = _columnas(Sesion)
    indices = _indices(Sesion)

    assert set(columnas) == {
        "id", "token_hash", "usuario_id", "created_at", "last_seen_at",
        "expires_at", "revoked_at", "mfa_at", "ip", "user_agent",
    }
    assert columnas["token_hash"].type.length == 64
    assert columnas["ip"].type.length == 45
    assert columnas["user_agent"].type.length == 300
    assert indices["uq_sesiones_token_hash"].unique is True
    assert "ix_sesiones_usuario_revocada" in indices
    assert [c.name for c in indices["ix_sesiones_usuario_revocada"].columns] == [
        "usuario_id", "revoked_at"]


def test_mfa_totp_es_uno_por_usuario_y_cifra_el_secreto():
    columnas = _columnas(MfaTotp)

    assert columnas["usuario_id"].primary_key is True
    assert columnas["secret_enc"].nullable is False
    assert columnas["key_id"].type.length == 20
    assert "last_used_step" in columnas


def test_mfa_email_codes_tiene_indice_compuesto():
    columnas = _columnas(MfaEmailCode)
    indices = _indices(MfaEmailCode)

    assert {"code_hash", "purpose", "expires_at", "attempts", "consumed_at"} <= set(columnas)
    assert [c.name for c in indices["ix_mfa_email_codes_usuario_purpose"].columns] == [
        "usuario_id", "purpose", "consumed_at"]


def test_recovery_codes_y_auth_tokens():
    recuperacion = _columnas(RecoveryCode)
    assert {"usuario_id", "code_hash", "used_at", "created_at"} <= set(recuperacion)
    assert "ix_recovery_codes_usuario" in _indices(RecoveryCode)

    tokens = _columnas(AuthToken)
    assert {"usuario_id", "purpose", "token_hash", "expires_at", "used_at", "ip"} <= set(tokens)
    assert _indices(AuthToken)["ix_auth_tokens_token_hash"].unique is True


def test_login_attempts_indexado_por_cuenta_e_ip():
    indices = _indices(LoginAttempt)

    assert [c.name for c in indices["ix_login_attempts_identificador_fecha"].columns] == [
        "identificador", "created_at"]
    assert [c.name for c in indices["ix_login_attempts_ip_fecha"].columns] == [
        "ip", "created_at"]
    # El identificador debe tolerar correos largos.
    assert _columnas(LoginAttempt)["identificador"].type.length == 255


def test_email_outbox_tiene_lo_necesario_para_reintentos():
    columnas = _columnas(EmailOutbox)

    assert {"destinatario", "asunto", "cuerpo", "intentos", "enviado_en",
            "ultimo_error", "created_at"} <= set(columnas)
    assert columnas["intentos"].server_default is not None


def test_claves_foraneas_en_cascada_hacia_usuarios():
    for modelo in (Sesion, MfaTotp, MfaEmailCode, RecoveryCode, AuthToken):
        claves = [fk for fk in modelo.__table__.foreign_keys
                  if fk.column.table.name == "usuarios"]
        assert claves, modelo.__tablename__
        assert all(fk.ondelete == "CASCADE" for fk in claves), modelo.__tablename__


def test_login_attempts_y_outbox_sin_fk_a_usuarios():
    # Un intento fallido puede referirse a una cuenta inexistente.
    assert not list(LoginAttempt.__table__.foreign_keys)
    assert not list(EmailOutbox.__table__.foreign_keys)


# --- Auditoría -----------------------------------------------------------------
def test_auditoria_agrega_ip_y_agente():
    columnas = _columnas(Auditoria)

    for anterior in ("id", "actor", "accion", "entidad", "entidad_id", "detalle", "fecha"):
        assert anterior in columnas
    assert columnas["ip"].nullable is True
    assert columnas["ip"].type.length == 45
    assert columnas["user_agent"].nullable is True
    assert columnas["user_agent"].type.length == 300


def test_no_se_usan_tipos_citext():
    """La unicidad insensible a mayúsculas se resuelve con lower(...), no CITEXT."""
    for tabla in Base.metadata.tables.values():
        for columna in tabla.columns:
            assert "CITEXT" not in type(columna.type).__name__.upper()


def test_fechas_de_autenticacion_son_datetime_sin_zona():
    columnas = _columnas(Usuario)

    for nombre in ("password_changed_at", "mfa_enrolled_at", "last_login_at",
                   "locked_until", "created_at", "updated_at"):
        assert isinstance(columnas[nombre].type, DateTime), nombre
