"""Proveedor local, contrato y utilidades de mapeo LDAP."""
from collections.abc import Callable
from typing import Any, Protocol

import ldap3
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.core.security import hash_password, verify_dummy, verify_password
from app.models import Usuario

ROLES_PRIORIDAD = ("admin", "supervision", "cargador", "consulta", "asistente")

#: Motivos que puede devolver :class:`ResultadoLdap` (``"ok"`` es el éxito).
MOTIVOS_LDAP = (
    "ok", "disabled", "credenciales", "no_encontrado", "ambiguo",
    "sin_rol", "sin_cuenta", "inactivo", "error",
)

#: Fábrica global sustituible en pruebas (evita abrir sockets reales).
_FABRICA_CONEXION: Callable[..., Any] | None = None


def fabrica_por_defecto(servidor: Any, **kwargs: Any) -> Any:
    """Crea un ``ldap3.Connection``; las pruebas pueden sustituirla.

    Se resuelve en tiempo de llamada (no en el import) para que ninguna prueba
    ni la propia aplicación abra sockets cuando el directorio está apagado.
    """
    if _FABRICA_CONEXION is not None:
        return _FABRICA_CONEXION(servidor, **kwargs)
    return ldap3.Connection(servidor, **kwargs)


def buscar_usuario(db: Session, login: str) -> Usuario | None:
    """Busca un usuario por ``username`` o ``email`` (sin distinguir mayúsculas).

    La comparación replica los índices funcionales ``lower(...)`` del esquema:
    si se buscara con ``==`` una cuenta con mayúsculas quedaría inaccesible.
    """
    clave = (login or "").strip().casefold()
    if not clave:
        return None
    return db.scalar(
        select(Usuario)
        .where(or_(func.lower(Usuario.username) == clave,
                   func.lower(Usuario.email) == clave))
        .order_by(Usuario.id)
        .limit(1)
    )


class AuthProvider(Protocol):
    """Contrato de un proveedor de identidad.

    ``authenticate`` devuelve ``(usuario, needs_rehash)`` o ``None``. El flag
    ``needs_rehash`` permite actualizar el hash cuando suben los parámetros de
    Argon2id; el llamador decide si confirma el cambio. El proveedor LDAP
    devuelve ``(usuario, False)`` y ``(None, False)`` en el fallo.
    """

    nombre: str

    def authenticate(self, db: Session, login: str, password: str,
                     ) -> tuple[Usuario, bool] | None: ...


class LocalProvider:
    """Autenticación local con Argon2id.

    Reglas de seguridad:

    - Si el identificador no existe se verifica contra ``DUMMY_HASH`` para que
      el tiempo de respuesta no revele si la cuenta existe.
    - Un usuario inactivo, sin hash o de otro proveedor también consume el
      tiempo del hash y devuelve ``None``: el mensaje al usuario es genérico.
    - Si el hash quedó con parámetros viejos se re-calcula en el mismo login
      (transparente para el usuario).
    """

    nombre = "local"

    def authenticate(self, db: Session, login: str, password: str,
                     ) -> tuple[Usuario, bool] | None:
        usuario = buscar_usuario(db, login)
        if usuario is None:
            verify_dummy(password or "")
            return None
        if (usuario.auth_provider != self.nombre or not usuario.is_active
                or not usuario.password_hash):
            # Mismo coste que una verificación real, mismo resultado visible.
            verify_dummy(password or "")
            return None
        ok, needs_rehash = verify_password(usuario.password_hash, password or "")
        if not ok:
            return None
        if needs_rehash:
            usuario.password_hash = hash_password(password)
        return usuario, needs_rehash


# --- Utilidades de mapeo LDAP ---------------------------------------------------
def normalizar_mapa(mapa: dict[str, str] | None) -> dict[str, str]:
    """Claves y valores del mapa grupo→rol en minúsculas.

    El directorio devuelve los grupos con la capitalización que quiera AD; el
    mapa se configura a mano y puede venir con ``CN=...`` en mayúsculas. La
    comparación es siempre *case-insensitive*.
    """
    limpio: dict[str, str] = {}
    for grupo, rol in (mapa or {}).items():
        clave = str(grupo).strip().casefold()
        valor = str(rol).strip().casefold()
        if clave and valor:
            limpio[clave] = valor
    return limpio


def cn_de(valor: str | None) -> str:
    """Primer RDN (``CN=...``) de un DN, o el valor tal cual si es un CN suelto."""
    texto = str(valor or "").strip()
    primero = texto.split(",", 1)[0].strip()
    if "=" in primero:
        return primero.split("=", 1)[1].strip().strip('"').strip()
    return primero


def elegir_rol(grupos: list[str] | tuple[str, ...], mapa: dict[str, str] | None,
               por_defecto: str = "") -> str | None:
    """Rol resultante de los grupos del usuario, o ``None`` si se deniega.

    Acepta grupos como DN completo (``CN=EET-Supervision,OU=...``) o como CN
    suelto (``EET-Supervision``). Si hay varios roles mapeados gana el de mayor
    prioridad (:data:`ROLES_PRIORIDAD`). Un ``por_defecto`` vacío significa
    *fail closed*: el usuario no entra.
    """
    mapa_n = normalizar_mapa(mapa)
    encontrados: set[str] = set()
    for grupo in grupos or ():
        for candidato in (str(grupo).strip().casefold(),
                          cn_de(grupo).casefold()):
            rol = mapa_n.get(candidato)
            if rol in ROLES_PRIORIDAD:
                encontrados.add(rol)
    if encontrados:
        return min(encontrados, key=ROLES_PRIORIDAD.index)
    defecto = str(por_defecto or "").strip().casefold()
    return defecto if defecto in ROLES_PRIORIDAD else None


def construir_filtro(login: str, config: Settings | None = None) -> str:
    """Filtro LDAP de búsqueda con el login **escapado**.

    ``EET_LDAP_USER_FILTER`` puede venir con o sin paréntesis y con el
    marcador ``{login}``. Siempre se devuelve envuelto en ``(&...)``.
    ``escape_filter_chars`` neutraliza ``*``, ``(``, ``)``, ``\\`` y NUL: sin
    eso un login como ``*)(uid=*`` alteraría la consulta.
    """
    cfg = config or settings
    patron = (cfg.ldap_user_filter or "(sAMAccountName={login})").strip()
    filtro = patron.format(login=escape_filter_chars(login or ""))
    if not filtro.startswith("("):
        filtro = f"({filtro})"
    return f"(&{filtro})"


def configuracion_faltante(config: Settings | None = None) -> list[str]:
    """Variables obligatorias que faltan para operar contra el directorio."""
    cfg = config or settings
    return [
        nombre
        for nombre, valor in (
            ("EET_LDAP_URL", cfg.ldap_url),
            ("EET_LDAP_BIND_DN", cfg.ldap_bind_dn),
            ("EET_LDAP_BIND_PASSWORD", cfg.ldap_bind_password),
            ("EET_LDAP_BASE_DN", cfg.ldap_base_dn),
        )
        if not (valor or "").strip()
    ]
