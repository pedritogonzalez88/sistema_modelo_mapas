"""Pruebas unitarias de ``app.core.security`` (Fase A0).

No tocan la base de datos ni la aplicación web: verifican las primitivas
criptográficas de forma aislada. El reloj de TOTP se sustituye por uno fijo
para que las ventanas de 30 s no hagan las pruebas intermitentes.
"""
import re

import pyotp
import pytest
from argon2 import PasswordHasher

from app.core import security


class _RelojFijo:
    """Sustituto de ``time`` con un instante controlado."""

    def __init__(self, segundo: float):
        self.segundo = segundo

    def time(self) -> float:
        return self.segundo


# --- Contraseñas ---------------------------------------------------------------
def test_hash_password_genera_argon2id_distinto_cada_vez():
    primero = security.hash_password("Clave-larga-2026")
    segundo = security.hash_password("Clave-larga-2026")

    assert primero.startswith("$argon2id$")
    assert primero != segundo  # sal aleatoria por hash


def test_verify_password_acierta_y_falla():
    almacenado = security.hash_password("Clave-larga-2026")

    assert security.verify_password(almacenado, "Clave-larga-2026") == (True, False)
    assert security.verify_password(almacenado, "clave-larga-2026") == (False, False)
    assert security.verify_password(almacenado, "") == (False, False)
    assert security.verify_password("", "Clave-larga-2026") == (False, False)


def test_verify_password_detecta_rehash_necesario():
    debil = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    antiguo = debil.hash("Clave-larga-2026")

    ok, needs_rehash = security.verify_password(antiguo, "Clave-larga-2026")

    assert ok is True
    assert needs_rehash is True


def test_verify_password_no_lanza_con_hash_corrupto():
    assert security.verify_password("no-es-un-hash", "Clave-larga-2026") == (False, False)


def test_hash_password_rechaza_vacio():
    with pytest.raises(ValueError):
        security.hash_password("")


def test_dummy_hash_y_verify_dummy_siempre_fallan():
    assert security.DUMMY_HASH.startswith("$argon2id$")
    assert security.verify_dummy("cualquier-clave-larga") is False
    assert security.verify_dummy("") is False


# --- Política de contraseñas ---------------------------------------------------
@pytest.mark.parametrize("clave", ["corta", "Ab1!corta", "12345678"])
def test_validar_password_exige_longitud_minima(clave):
    errores = security.validar_password(clave)

    assert any("12 caracteres" in e for e in errores)


def test_validar_password_rechaza_demasiado_larga():
    errores = security.validar_password("A" * 129)

    assert any("128 caracteres" in e for e in errores)


@pytest.mark.parametrize("clave", ["password123", "contrasena", "qwerty123456", "123456789012"])
def test_validar_password_rechaza_claves_comunes(clave):
    errores = security.validar_password(clave)

    assert any("común" in e for e in errores)


def test_validar_password_compara_con_usuario_y_correo():
    errores = security.validar_password("jperez2026aep", username="jperez2026aep",
                                        email="jperez2026aep@aep.gov.py")
    assert any("nombre de usuario" in e for e in errores)

    errores_correo = security.validar_password("maria.gomez1", username="otro",
                                               email="maria.gomez1@aep.gov.py")
    assert any("correo institucional" in e for e in errores_correo)


def test_validar_password_acepta_clave_correcta():
    assert security.validar_password("Malla-EET-2026#ok", username="jperez",
                                     email="jperez@aep.gov.py") == []


def test_validar_password_rechaza_espacios_en_los_bordes():
    errores = security.validar_password(" Malla-EET-2026#ok ")

    assert any("espacios" in e for e in errores)


# --- Tokens y hashes -----------------------------------------------------------
def test_new_token_es_aleatorio_y_suficientemente_largo():
    tokens = {security.new_token() for _ in range(50)}

    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


def test_new_token_exige_entropia_minima():
    with pytest.raises(ValueError):
        security.new_token(8)


def test_sha256_hex_es_estable():
    import hashlib

    esperado = hashlib.sha256("hola".encode("utf-8")).hexdigest()

    assert security.sha256_hex("hola") == esperado
    assert security.sha256_hex("hola") == security.sha256_hex("hola")
    assert len(security.sha256_hex("hola")) == 64


def test_constant_time_eq_compara_textos():
    assert security.constant_time_eq("abc", "abc") is True
    assert security.constant_time_eq("abc", "abd") is False
    assert security.constant_time_eq("abc", "abcd") is False
    assert security.constant_time_eq("ñandú", "ñandú") is True


def test_hmac_code_liga_codigo_usuario_y_pepper():
    uno = security.hmac_code("123456", 7, "pepper-de-prueba")
    otro = security.hmac_code("123456", 8, "pepper-de-prueba")
    mismo = security.hmac_code("123456", 7, "pepper-de-prueba")

    assert uno == mismo
    assert uno != otro
    assert len(uno) == 64
    assert uno != security.hmac_code("123456", 7, "otro-pepper")


def test_hmac_code_exige_pepper():
    with pytest.raises(ValueError):
        security.hmac_code("123456", 1, "")


# --- Cofre de cifrado multi-clave ---------------------------------------------
def test_secret_box_cifra_con_la_clave_vigente_y_descifra_con_la_antigua():
    vigente = security.SecretBox([("v2", "clave-nueva-2026"), ("v1", "clave-vieja-2025")])

    blob, key_id = vigente.encrypt("secreto-totp")

    assert key_id == "v2"
    assert isinstance(blob, bytes)
    assert vigente.decrypt(blob, key_id) == "secreto-totp"
    # Un cofre donde v1 es la vigente sigue descifrando lo que cifró v2.
    otro = security.SecretBox([("v1", "clave-vieja-2025"), ("v2", "clave-nueva-2026")])
    assert otro.decrypt(blob, "v2") == "secreto-totp"
    assert otro.puede_descifrar("v2") is True


def test_secret_box_detecta_clave_desconocida_y_blob_alterado():
    caja = security.SecretBox([("v1", "clave-de-prueba")])
    blob, _ = caja.encrypt("dato")

    with pytest.raises(ValueError):
        caja.decrypt(blob, "v9")

    with pytest.raises(ValueError):
        caja.decrypt(blob[:-4] + b"AAAA", "v1")

    assert caja.puede_descifrar("v9") is False


def test_secret_box_acepta_clave_fernet_y_secreto_arbitrario():
    from cryptography.fernet import Fernet

    fernet_b64 = Fernet.generate_key().decode()
    caja = security.SecretBox([("v1", fernet_b64), ("v2", "cadena-larga-aleatoria")])

    blob_a, _ = caja.encrypt("uno")
    assert caja.decrypt(blob_a, "v1") == "uno"

    caja_v2 = security.SecretBox([("v2", "cadena-larga-aleatoria"),
                                  ("v1", fernet_b64)])
    blob_b, key_b = caja_v2.encrypt("dos")
    assert key_b == "v2"
    assert caja.decrypt(blob_b, "v2") == "dos"


def test_secret_box_exige_al_menos_una_clave():
    with pytest.raises(ValueError):
        security.SecretBox([])


def test_secret_box_desde_settings_falla_sin_claves(monkeypatch):
    monkeypatch.setattr(security.settings, "fernet_keys", "")
    monkeypatch.setattr(security.settings, "secret_key", "")

    with pytest.raises(RuntimeError):
        security.secret_box()


# --- TOTP ----------------------------------------------------------------------
def test_totp_uri_contiene_emisor_y_correo():
    secreto = security.generar_secreto()
    uri = security.totp_uri(secreto, "persona@aep.gov.py")

    assert uri.startswith("otpauth://totp/")
    assert "persona%40aep.gov.py" in uri
    assert "issuer=Validador" in uri
    assert secreto in uri


def test_generar_secreto_tiene_160_bits():
    secreto = security.generar_secreto()

    assert len(secreto) == 32
    assert re.fullmatch(r"[A-Z2-7]+", secreto)


def test_verificar_totp_acepta_codigo_actual(monkeypatch):
    monkeypatch.setattr(security, "time", _RelojFijo(1_700_000_010))
    secreto = security.generar_secreto()
    codigo = pyotp.TOTP(secreto).at(1_700_000_010)

    paso = security.verificar_totp(secreto, codigo, None, window=1)

    assert paso == 1_700_000_010 // 30


def test_verificar_totp_acepta_ventana_de_un_paso(monkeypatch):
    base = 1_700_000_010
    monkeypatch.setattr(security, "time", _RelojFijo(base))
    secreto = security.generar_secreto()
    totp = pyotp.TOTP(secreto)

    anterior = security.verificar_totp(secreto, totp.at(base - 30), None, window=1)
    siguiente = security.verificar_totp(secreto, totp.at(base + 30), None, window=1)

    assert anterior == (base // 30) - 1
    assert siguiente == (base // 30) + 1


def test_verificar_totp_rechaza_fuera_de_ventana(monkeypatch):
    base = 1_700_000_010
    monkeypatch.setattr(security, "time", _RelojFijo(base))
    secreto = security.generar_secreto()
    codigo_viejo = pyotp.TOTP(secreto).at(base - 90)

    assert security.verificar_totp(secreto, codigo_viejo, None, window=1) is None


def test_verificar_totp_rechaza_reutilizacion(monkeypatch):
    base = 1_700_000_010
    monkeypatch.setattr(security, "time", _RelojFijo(base))
    secreto = security.generar_secreto()
    codigo = pyotp.TOTP(secreto).at(base)
    paso = base // 30

    assert security.verificar_totp(secreto, codigo, paso, window=1) is None
    assert security.verificar_totp(secreto, codigo, paso - 1, window=1) == paso


def test_verificar_totp_rechaza_formatos_invalidos(monkeypatch):
    monkeypatch.setattr(security, "time", _RelojFijo(1_700_000_010))
    secreto = security.generar_secreto()

    assert security.verificar_totp(secreto, "", None) is None
    assert security.verificar_totp(secreto, "12345", None) is None
    assert security.verificar_totp(secreto, "12a456", None) is None
    assert security.verificar_totp("", "123456", None) is None


# --- Códigos de respaldo -------------------------------------------------------
def test_generar_codigos_respaldo_formato_y_unicidad():
    codigos = security.generar_codigos_respaldo(10)

    assert len(codigos) == 10
    assert len(set(codigos)) == 10
    for codigo in codigos:
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", codigo)
        assert not set(codigo) & set("O0I1")


def test_generar_codigos_respaldo_valida_cantidad():
    with pytest.raises(ValueError):
        security.generar_codigos_respaldo(0)


def test_normalizar_codigo_unifica_escritura():
    assert security.normalizar_codigo("abcd-efgh") == "ABCD-EFGH"
    assert security.normalizar_codigo("abcd efgh") == "ABCD-EFGH"
    assert security.normalizar_codigo(" ABCD-EFGH ") == "ABCD-EFGH"
    assert security.normalizar_codigo("ABCDEFGH") == "ABCD-EFGH"


def test_normalizar_codigo_devuelve_texto_limpio_si_no_cumple_formato():
    assert security.normalizar_codigo("abc-12") == "ABC-12"
    assert security.normalizar_codigo("") == ""


def test_health_y_ready_son_rutas_publicas():
    from app.auth.middleware import es_ruta_publica

    assert es_ruta_publica("/health") is True
    assert es_ruta_publica("/ready") is True
