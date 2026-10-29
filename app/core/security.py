"""Primitivas criptográficas de la aplicación (Fase A0).

Todo lo de este módulo es puro y testeable: no toca la base de datos ni el
estado de la petición. Las únicas dependencias de configuración son las claves
de ``settings`` (leídas en cada construcción de :class:`SecretBox`), por lo que
las pruebas pueden inyectar claves propias sin parchear variables globales.

Reglas de diseño:

- Contraseñas con Argon2id (memoria 64 MB, 3 pasadas, 4 hilos).
- Comparaciones sensibles en tiempo constante.
- Cifrado simétrico multi-clave para permitir rotación sin perder secretos.
- Ningún secreto se registra en logs ni se devuelve en mensajes de error.
"""
import base64
import binascii
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping, Sequence
from typing import Final

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

try:  # argon2-cffi >= 21.1 expone InvalidHash; >= 23.1 lo renombra como *Error
    from argon2.exceptions import InvalidHashError as _InvalidHashError
except ImportError:  # pragma: no cover - compatibilidad con versiones antiguas
    from argon2.exceptions import InvalidHash as _InvalidHashError

__all__ = [
    "DUMMY_HASH",
    "SecretBox",
    "constant_time_eq",
    "generar_codigos_respaldo",
    "generar_secreto",
    "hash_password",
    "hmac_code",
    "new_token",
    "normalizar_codigo",
    "secret_box",
    "sha256_hex",
    "totp_uri",
    "validar_password",
    "verificar_totp",
    "verify_dummy",
    "verify_password",
]

# Parámetros de Argon2id (OWASP: 19 MiB mínimo; aquí 64 MiB y 3 pasadas).
_HASHER: Final = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

# Hash de relleno para verificar siempre algo cuando el usuario no existe.
# Se calcula una sola vez al importar el módulo para no penalizar el login.
DUMMY_HASH: Final = _HASHER.hash("hash-de-relleno-para-tiempos-constantes")

# Alfabeto de códigos de respaldo: sin O/0 ni I/1 para evitar confusión al
# transcribir a mano.
ALFABETO_RESPALDO: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Lista mínima local de contraseñas frecuentes (español e inglés). No sustituye
# a un diccionario completo, pero bloquea los intentos obvios sin dependencias.
PASSWORDS_COMUNES: Final = frozenset({
    "123456789012", "1234567890123", "12345678901234", "1234567890",
    "password", "password1", "password123", "password1234", "passw0rd",
    "contrasena", "contraseña", "contraseña123", "contrasena123",
    "qwertyuiop", "qwerty123456", "qwertyuiop123", "1q2w3e4r5t6y",
    "admin1234567", "administrador", "administrator", "admin1234",
    "iloveyou123", "welcome12345", "welcome123456", "letmein123456",
    "abc123456789", "abcdefghijkl", "aaaaaaaaaaaa", "000000000000",
    "111111111111", "121212121212", "asdfghjkl123", "asdfghjklñ",
    "paraguay2024", "paraguay2025", "ingeibtr2", "eetparaguay",
    "validadoreet", "cambiarme123", "temporal1234", "bienvenido123",
})


# --- Contraseñas ---------------------------------------------------------------
def hash_password(password: str) -> str:
    """Devuelve el hash Argon2id de ``password`` en formato PHC.

    Argon2 incorpora sal aleatoria y parámetros en el propio hash, por lo que
    dos llamadas con la misma contraseña producen valores distintos.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("La contraseña no puede estar vacía.")
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, bool]:
    """Verifica una contraseña y avisa si el hash debe regenerarse.

    Devuelve ``(ok, needs_rehash)``. Un hash corrupto o de otro algoritmo se
    trata como credencial inválida (nunca lanza), para que el login responda
    igual ante cualquier fallo.
    """
    if not password_hash or not isinstance(password, str) or not password:
        return False, False
    try:
        _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, _InvalidHashError):
        return False, False
    except Exception:  # parámetros desconocidos: se considera inválido
        return False, False
    try:
        needs_rehash = _HASHER.check_needs_rehash(password_hash)
    except Exception:
        needs_rehash = False
    return True, needs_rehash


def verify_dummy(password: str) -> bool:
    """Consume el mismo tiempo que una verificación real y siempre falla.

    Se usa cuando el identificador no existe: el atacante no puede distinguir
    "cuenta inexistente" de "contraseña incorrecta" por el tiempo de respuesta.
    """
    try:
        _HASHER.verify(DUMMY_HASH, password or "")
    except Exception:
        return False
    return False


def validar_password(password: str, username: str | None = None,
                     email: str | None = None) -> list[str]:
    """Aplica la política de contraseñas y devuelve los incumplimientos.

    Una lista vacía significa contraseña aceptable. Los mensajes están en
    español y son aptos para mostrarse junto al formulario.
    """
    errores: list[str] = []
    valor = password or ""

    if len(valor) < 12:
        errores.append("La contraseña debe tener al menos 12 caracteres.")
    if len(valor) > 128:
        errores.append("La contraseña no puede superar los 128 caracteres.")
    if valor and valor.strip() != valor:
        errores.append("La contraseña no puede empezar ni terminar con espacios.")

    normalizada = valor.strip().casefold()
    if normalizada and normalizada in PASSWORDS_COMUNES:
        errores.append("La contraseña es demasiado común; elija otra.")

    if username and normalizada and normalizada == username.strip().casefold():
        errores.append("La contraseña no puede ser igual al nombre de usuario.")

    if email:
        correo = email.strip().casefold()
        local = correo.split("@", 1)[0]
        if normalizada and normalizada in {correo, local}:
            errores.append("La contraseña no puede ser igual al correo institucional.")

    return errores


# --- Tokens y hashes -----------------------------------------------------------
def new_token(nbytes: int = 32) -> str:
    """Genera un token URL-safe criptográficamente aleatorio."""
    if nbytes < 16:
        raise ValueError("El token debe tener al menos 16 bytes de entropía.")
    return secrets.token_urlsafe(nbytes)


def sha256_hex(texto: str) -> str:
    """Hash SHA-256 en hexadecimal; se usa para guardar tokens, nunca el token."""
    return hashlib.sha256((texto or "").encode("utf-8")).hexdigest()


def hmac_code(code: str, user_id: int | str, pepper: str) -> str:
    """HMAC-SHA256 del código de un solo uso, ligado al usuario y al pepper.

    El pepper vive solo en configuración: quien lea la tabla no puede
    reconstruir el código ni reutilizarlo para otro usuario.
    """
    if not pepper:
        raise ValueError("Se requiere el pepper para calcular el HMAC del código.")
    mensaje = f"{user_id}:{code}".encode("utf-8")
    return hmac.new(pepper.encode("utf-8"), mensaje, hashlib.sha256).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    """Compara dos textos en tiempo constante (evita filtraciones por tiempo)."""
    izquierda = a.encode("utf-8") if isinstance(a, str) else bytes(a)
    derecha = b.encode("utf-8") if isinstance(b, str) else bytes(b)
    return hmac.compare_digest(izquierda, derecha)


# --- Cifrado simétrico multi-clave --------------------------------------------
def _clave_fernet(material: str) -> Fernet:
    """Construye un ``Fernet`` a partir de una clave o de un secreto arbitrario.

    Si ``material`` ya es una clave Fernet (base64 URL-safe de 32 bytes) se usa
    tal cual. En caso contrario se deriva con SHA-256, de modo que
    ``EET_SECRET_KEY`` puede ser cualquier cadena larga y aleatoria.
    """
    texto = (material or "").strip()
    if texto.startswith("base64:"):
        texto = texto[len("base64:"):]
    if not texto:
        raise ValueError("Las claves de cifrado no pueden estar vacías.")
    candidata = texto.encode("ascii", "ignore")
    try:
        if len(base64.urlsafe_b64decode(candidata)) == 32:
            return Fernet(candidata)
    except (binascii.Error, ValueError):
        pass
    derivada = base64.urlsafe_b64encode(hashlib.sha256(texto.encode("utf-8")).digest())
    return Fernet(derivada)


class SecretBox:
    """Cofre de cifrado simétrico con rotación de claves (``v1..vN``).

    La primera clave de la secuencia es la vigente para cifrar; todas las
    demás siguen disponibles para descifrar datos antiguos. El blob guarda el
    identificador de la clave usada en una columna aparte (``key_id``), de modo
    que una rotación no invalida los secretos ya almacenados.
    """

    def __init__(self, claves: Mapping[str, str] | Sequence[tuple[str, str]]):
        pares = list(claves.items()) if isinstance(claves, Mapping) else list(claves)
        if not pares:
            raise ValueError(
                "SecretBox requiere al menos una clave de cifrado "
                "(EET_FERNET_KEYS o EET_SECRET_KEY)."
            )
        self._fernets: dict[str, Fernet] = {}
        for identificador, material in pares:
            if not identificador:
                raise ValueError("Cada clave de cifrado necesita un identificador (v1, v2...).")
            self._fernets[str(identificador)] = _clave_fernet(str(material))
        self.key_id: str = str(pares[0][0])

    @property
    def key_ids(self) -> list[str]:
        """Identificadores disponibles, en orden de prioridad."""
        return list(self._fernets)

    def encrypt(self, texto: str) -> tuple[bytes, str]:
        """Cifra ``texto`` con la clave vigente y devuelve ``(blob, key_id)``."""
        if texto is None:
            raise ValueError("No se puede cifrar un valor nulo.")
        blob = self._fernets[self.key_id].encrypt(str(texto).encode("utf-8"))
        return blob, self.key_id

    def decrypt(self, blob: bytes, key_id: str) -> str:
        """Descifra ``blob`` con la clave indicada.

        Lanza ``ValueError`` si la clave no existe o si el blob fue alterado;
        nunca expone el contenido parcial.
        """
        fernet = self._fernets.get(str(key_id))
        if fernet is None:
            raise ValueError(f"Clave de cifrado desconocida: {key_id!r}.")
        try:
            return fernet.decrypt(bytes(blob)).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("El dato cifrado no es válido o fue alterado.") from exc

    def puede_descifrar(self, key_id: str) -> bool:
        """Indica si la clave indicada está disponible en este cofre."""
        return str(key_id) in self._fernets


def secret_box() -> SecretBox:
    """Cofre construido desde ``settings`` (``EET_FERNET_KEYS`` o ``SECRET_KEY``)."""
    claves = settings.fernet_key_list
    if not claves:
        raise RuntimeError(
            "No hay claves de cifrado configuradas: defina EET_FERNET_KEYS "
            "o EET_SECRET_KEY antes de usar el cofre."
        )
    return SecretBox(claves)


# --- TOTP ----------------------------------------------------------------------
def generar_secreto() -> str:
    """Secreto TOTP base32 de 160 bits (compatible con apps autenticadoras)."""
    return pyotp.random_base32(length=32)


def totp_uri(secreto: str, email: str, issuer: str | None = None) -> str:
    """URI ``otpauth://`` para el código QR de enrolamiento."""
    if not secreto:
        raise ValueError("Se requiere el secreto TOTP.")
    return pyotp.TOTP(secreto).provisioning_uri(
        name=email or "usuario", issuer_name=issuer or settings.mfa_issuer
    )


def verificar_totp(secreto: str, code: str, last_step: int | None,
                   window: int | None = None) -> int | None:
    """Valida un código TOTP y devuelve el paso aceptado.

    - Acepta ``±window`` pasos de 30 s (por desfase de reloj).
    - Rechaza pasos ``<= last_step`` para impedir la reutilización de un código.
    - Devuelve ``None`` si no hay coincidencia o el formato no es válido.
    """
    if not secreto:
        return None
    candidato = (code or "").strip().replace(" ", "")
    if len(candidato) != 6 or not candidato.isdigit():
        return None
    ventana = settings.mfa_window if window is None else int(window)
    ventana = max(0, ventana)
    totp = pyotp.TOTP(secreto)
    ahora = int(time.time()) // 30
    for step in range(ahora - ventana, ahora + ventana + 1):
        if last_step is not None and step <= int(last_step):
            continue
        if hmac.compare_digest(totp.at(step * 30), candidato):
            return step
    return None


# --- Códigos de respaldo -------------------------------------------------------
def generar_codigos_respaldo(n: int = 10) -> list[str]:
    """Genera ``n`` códigos de un solo uso con formato ``XXXX-XXXX``.

    Usa el alfabeto sin caracteres ambiguos y garantiza que no se repitan
    dentro del mismo lote. La comparación con los hashes guardados debe
    hacerse sobre :func:`normalizar_codigo`.
    """
    if n <= 0:
        raise ValueError("La cantidad de códigos debe ser positiva.")
    vistos: set[str] = set()
    codigos: list[str] = []
    while len(codigos) < n:
        crudo = "".join(secrets.choice(ALFABETO_RESPALDO) for _ in range(8))
        codigo = f"{crudo[:4]}-{crudo[4:]}"
        if codigo in vistos:
            continue
        vistos.add(codigo)
        codigos.append(codigo)
    return codigos


def normalizar_codigo(codigo: str) -> str:
    """Normaliza un código de respaldo a ``XXXX-XXXX`` en mayúsculas.

    Acepta lo que el usuario tipea (minúsculas, espacios o guiones de más).
    Si el contenido alfanumérico no tiene 8 caracteres se devuelve el texto
    limpio en mayúsculas para que el llamador pueda rechazarlo por formato.
    """
    limpio = "".join(ch for ch in (codigo or "").upper() if ch.isalnum())
    if len(limpio) == 8:
        return f"{limpio[:4]}-{limpio[4:]}"
    return (codigo or "").strip().upper()
