"""Proveedores de autenticación (Fases A1 y A5).

``AuthProvider`` es la interfaz mínima que consume el servicio de sesiones.
Existen dos implementaciones:

- :class:`LocalProvider` (Fase A1): usuario/contraseña con Argon2id.
- :class:`LdapProvider` (Fase A5): bind contra LDAP/Active Directory.

El proveedor **nunca** decide la autorización ni crea la sesión: solo responde
"estas credenciales corresponden a este usuario" o ``None``.

Reglas del proveedor LDAP:

- Si ``EET_LDAP_ENABLED=false`` no se instancia conexión ni se toca la red.
- Se abre una conexión de servicio (cuenta de solo lectura) para buscar, y una
  **segunda** conexión con el DN del usuario y su contraseña para validarla.
  La contraseña del dominio nunca se guarda ni se registra.
- Un fallo de LDAP **no** cae a autenticación local (*fail closed*).
- La conexión es inyectable (``connection_factory``) para probar todo el flujo
  sin red; por defecto usa ``ldap3.Server`` / ``ldap3.Connection``.
"""
from __future__ import annotations

import ssl
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Protocol

import ldap3
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import audit
from app.core.config import Settings, settings
from app.core.security import hash_password, verify_dummy, verify_password
from app.models import Usuario
from app.seed_data import mfa_policy_para_rol

__all__ = [
    "AuthProvider",
    "DiagnosticoLdap",
    "LdapProvider",
    "LocalProvider",
    "PasoDiagnostico",
    "ResultadoLdap",
    "ROLES_PRIORIDAD",
    "buscar_usuario",
    "construir_filtro",
    "elegir_rol",
    "fabrica_por_defecto",
    "normalizar_mapa",
]

#: Prioridad cuando el usuario pertenece a varios grupos mapeados.
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


def _primero(valor: Any) -> Any:
    """Primer valor de un atributo LDAP (ldap3 devuelve listas)."""
    if isinstance(valor, (list, tuple)):
        return valor[0] if valor else None
    if isinstance(valor, bytes):
        return valor.decode("utf-8", "replace")
    return valor


def _atributos_entrada(entrada: Any) -> dict[str, Any]:
    """Atributos de una entrada LDAP con claves en minúsculas.

    Soporta ``ldap3.Entry`` (``entry_attributes_as_dict``) y diccionarios
    simples, que es lo que usan las pruebas.
    """
    datos = getattr(entrada, "entry_attributes_as_dict", None)
    if not isinstance(datos, dict) and isinstance(entrada, dict):
        datos = entrada
    if not isinstance(datos, dict):
        return {}
    return {str(clave).casefold(): valor for clave, valor in datos.items()}


def _dn_entrada(entrada: Any) -> str | None:
    """DN de una entrada (``entry_dn`` o ``distinguishedName``)."""
    dn = getattr(entrada, "entry_dn", None)
    if not dn:
        dn = _primero(_atributos_entrada(entrada).get("distinguishedname"))
    return str(dn).strip() if dn else None


def _codigo_error(exc: BaseException) -> str:
    """Código corto y seguro de un fallo LDAP (nunca el mensaje con DN)."""
    codigo = getattr(exc, "code", None)
    if not codigo:
        resultado = getattr(exc, "result", None)
        if isinstance(resultado, dict):
            codigo = resultado.get("result")
    return str(codigo or type(exc).__name__)[:60]


@dataclass(frozen=True)
class ResultadoLdap:
    """Resultado detallado de :meth:`LdapProvider.autenticar_detallado`.

    ``motivo`` es uno de :data:`MOTIVOS_LDAP`. ``detalle`` solo lleva datos no
    sensibles (códigos, cantidades, rol); nunca contraseñas ni el DN completo.
    """

    usuario: Usuario | None = None
    motivo: str = "ok"
    detalle: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.usuario is not None


@dataclass(frozen=True)
class PasoDiagnostico:
    """Línea del diagnóstico de ``ldap-check``."""

    nombre: str
    ok: bool
    detalle: str = ""


@dataclass(frozen=True)
class DiagnosticoLdap:
    """Resultado del diagnóstico legible para TI (sin credenciales)."""

    habilitado: bool
    ok: bool
    pasos: list[PasoDiagnostico] = field(default_factory=list)
    grupos: list[str] = field(default_factory=list)
    usuario: dict | None = None


class _BindFallido(Exception):
    """El bind (servicio o usuario) devolvió ``False``."""

    def __init__(self, codigo: str = "bind") -> None:
        super().__init__(codigo)
        self.code = codigo


class _ErrorLdap(Exception):
    """Resultado LDAP distinto de ``success`` (p. ej. 32, 49, 34)."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.code = codigo


class LdapProvider:
    """Autenticación contra LDAP/Active Directory.

    ``connection_factory(servidor, **kwargs)`` debe devolver un objeto con
    ``bind()``, ``search()``, ``entries``, ``start_tls()`` y ``unbind()``.
    Por defecto se usa :func:`fabrica_por_defecto` (``ldap3.Connection``).
    """

    nombre = "ldap"

    def __init__(self, connection_factory: Callable[..., Any] | None = None, *,
                 config: Settings | None = None) -> None:
        self._factory = connection_factory or fabrica_por_defecto
        self._config = config or settings

    # --- Configuración y conexión ---------------------------------------------
    @property
    def cfg(self) -> Settings:
        return self._config

    def _servidor(self) -> tuple[Any, bool]:
        url = (self.cfg.ldap_url or "").strip()
        use_ssl = url.casefold().startswith("ldaps://")
        servidor = ldap3.Server(
            url,
            use_ssl=use_ssl,
            connect_timeout=int(self.cfg.ldap_timeout or 5),
            tls=ldap3.Tls(validate=ssl.CERT_REQUIRED),
            get_info=ldap3.NONE,
        )
        return servidor, use_ssl

    def _conectar(self, usuario: str | None, password: str | None) -> Any:
        """Abre la conexión, negocia StartTLS si corresponde y hace bind."""
        servidor, use_ssl = self._servidor()
        conn = self._factory(
            servidor,
            user=usuario,
            password=password,
            auto_bind=False,
            receive_timeout=int(self.cfg.ldap_timeout or 5),
        )
        try:
            if not use_ssl and bool(self.cfg.ldap_starttls):
                conn.start_tls()
            if not conn.bind():
                resultado = getattr(conn, "result", None)
                codigo = "bind"
                if isinstance(resultado, dict):
                    codigo = str(resultado.get("description")
                                 or resultado.get("result") or "bind")
                raise _BindFallido(codigo)
        except BaseException:
            self._cerrar(conn)
            raise
        return conn

    @staticmethod
    def _cerrar(conn: Any) -> None:
        """Cierra la conexión sin enmascarar el error original."""
        if conn is None:
            return
        try:
            conn.unbind()
        except Exception:
            pass

    @contextmanager
    def _conexion_servicio(self) -> Iterator[Any]:
        conn = self._conectar(self.cfg.ldap_bind_dn, self.cfg.ldap_bind_password)
        try:
            yield conn
        finally:
            self._cerrar(conn)

    def _atributos_busqueda(self) -> list[str]:
        cfg = self.cfg
        atributos = [
            cfg.ldap_attr_login, cfg.ldap_attr_nombre, cfg.ldap_attr_email,
            cfg.ldap_group_attr, "distinguishedName",
        ]
        return list(dict.fromkeys(a for a in atributos if a))

    def _buscar(self, conn: Any, filtro: str, atributos: list[str],
                size_limit: int = 2) -> list[Any]:
        conn.search(
            search_base=self.cfg.ldap_base_dn,
            search_filter=filtro,
            search_scope=ldap3.SUBTREE,
            attributes=list(atributos),
            size_limit=size_limit,
        )
        # ``ldap3`` no lanza en el modo por defecto: el código real queda en
        # ``conn.result``. Se traduce a un error con código para el diagnóstico
        # (32 = noSuchObject, 49 = invalidCredentials, 34 = invalidDNSyntax...).
        resultado = getattr(conn, "result", None)
        if isinstance(resultado, dict):
            codigo = resultado.get("result")
            if codigo not in (0, None):
                raise _ErrorLdap(
                    str(resultado.get("description") or codigo))
        return list(getattr(conn, "entries", []) or [])

    def _grupos(self, conn: Any, entrada: Any, dn: str | None) -> list[str]:
        """Grupos del usuario como DN/CN; soporta ``memberOf`` y ``member``."""
        atributo = (self.cfg.ldap_group_attr or "memberOf").strip()
        crudos = _atributos_entrada(entrada).get(atributo.casefold()) or []
        if isinstance(crudos, (str, bytes)):
            crudos = [crudos]
        valores = [str(_primero(valor)).strip() for valor in crudos]
        valores = [valor for valor in valores if valor]

        if not valores and atributo.casefold() == "member" and dn:
            filtro = f"(&(objectClass=group)(member={escape_filter_chars(dn)}))"
            try:
                entradas = self._buscar(
                    conn, filtro, ["cn", "distinguishedName"], size_limit=200)
            except (LDAPException, _ErrorLdap):
                entradas = []
            valores = [
                _dn_entrada(item)
                or str(_primero(_atributos_entrada(item).get("cn")) or "")
                for item in entradas
            ]
            valores = [valor for valor in valores if valor]
        return valores

    # --- Auditoría --------------------------------------------------------------
    def _auditar(self, db: Session | None, usuario: Usuario | None, accion: str,
                 detalle: dict | None = None) -> None:
        """Registra el evento técnico (sin IP ni secretos).

        El servicio de login añade ``login_ok``/``login_fail`` con la IP y el
        agente; aquí quedan los códigos propios de LDAP (``ldap_error``,
        ``ldap_ambiguo``, ``ldap_sin_rol``, ``ldap_fail``, ``ldap_sync``).
        """
        if db is None:
            return
        try:
            audit.registrar(
                db, audit.actor_de(usuario), accion, entidad="usuario",
                entidad_id=getattr(usuario, "id", None), detalle=detalle or {},
            )
            db.flush()
        except Exception:  # la auditoría nunca debe tumbar el login
            pass

    # --- Autenticación ----------------------------------------------------------
    def authenticate(self, db: Session, login: str, password: str,
                     ) -> tuple[Usuario | None, bool]:
        """Contrato común: ``(usuario, False)`` o ``(None, False)``.

        El segundo valor existe por compatibilidad con ``LocalProvider``
        (rehash de Argon2id); en LDAP siempre es ``False`` porque no se guarda
        ningún hash.
        """
        resultado = self.autenticar_detallado(db, login, password)
        return (resultado.usuario, False) if resultado.usuario is not None \
            else (None, False)

    def autenticar_detallado(self, db: Session | None, login: str,
                             password: str) -> ResultadoLdap:
        """Flujo completo: búsqueda, segundo bind, grupos, rol y sincronización."""
        cfg = self.cfg
        if not cfg.ldap_enabled:
            # Nunca se abre una conexión con la bandera apagada.
            return ResultadoLdap(motivo="disabled")

        faltan = configuracion_faltante(cfg)
        if faltan:
            self._auditar(db, None, "ldap_error",
                          {"motivo": "configuracion", "codigo": "config"})
            return ResultadoLdap(motivo="error",
                                 detalle={"codigo": "config", "faltan": faltan})

        if not (password or "").strip():
            # Sin contraseña no se hace un bind anónimo "de cortesía".
            self._auditar(db, None, "ldap_fail", {"motivo": "sin_password"})
            return ResultadoLdap(motivo="credenciales")

        conn_usuario = None
        try:
            with self._conexion_servicio() as conn:
                entradas = self._buscar(
                    conn, construir_filtro(login, cfg),
                    self._atributos_busqueda(), size_limit=2)
                if not entradas:
                    self._auditar(db, None, "ldap_fail",
                                  {"motivo": "no_encontrado"})
                    return ResultadoLdap(motivo="no_encontrado")
                if len(entradas) > 1:
                    self._auditar(db, None, "ldap_ambiguo",
                                  {"entradas": len(entradas)})
                    return ResultadoLdap(motivo="ambiguo",
                                         detalle={"entradas": len(entradas)})

                entrada = entradas[0]
                dn = _dn_entrada(entrada)
                if not dn:
                    self._auditar(db, None, "ldap_fail", {"motivo": "sin_dn"})
                    return ResultadoLdap(motivo="no_encontrado")

                # Segundo bind: es la única validación real de la contraseña.
                try:
                    conn_usuario = self._conectar(dn, password or "")
                except (LDAPException, socket.timeout, OSError, _BindFallido):
                    self._auditar(db, None, "ldap_fail",
                                  {"motivo": "credenciales"})
                    return ResultadoLdap(motivo="credenciales")

                grupos = self._grupos(conn, entrada, dn)
                atributos = _atributos_entrada(entrada)
        except (LDAPException, _ErrorLdap, socket.timeout, OSError) as exc:
            self._auditar(db, None, "ldap_error",
                          {"motivo": "conexion", "codigo": _codigo_error(exc)})
            return ResultadoLdap(motivo="error",
                                 detalle={"codigo": _codigo_error(exc)})
        finally:
            self._cerrar(conn_usuario)

        rol = elegir_rol(grupos, cfg.ldap_role_map_dict, cfg.ldap_default_role)
        if rol is None:
            usuario = buscar_usuario(db, login) if db is not None else None
            self._auditar(db, usuario, "ldap_sin_rol", {"grupos": len(grupos)})
            return ResultadoLdap(motivo="sin_rol",
                                 detalle={"grupos": len(grupos)})

        try:
            return self._sincronizar(db, login, dn, atributos, grupos, rol)
        except (LDAPException, _ErrorLdap, socket.timeout, OSError) as exc:
            self._auditar(db, None, "ldap_error",
                          {"motivo": "sincronizacion",
                           "codigo": _codigo_error(exc)})
            return ResultadoLdap(motivo="error",
                                 detalle={"codigo": _codigo_error(exc)})

    # --- Sincronización ---------------------------------------------------------
    def _email_disponible(self, db: Session, email: str,
                          usuario_id: int) -> bool:
        """``True`` si otro usuario no usa ya ese correo (índice funcional)."""
        fila = db.scalar(
            select(Usuario.id).where(
                func.lower(Usuario.email) == email.casefold(),
                Usuario.id != usuario_id,
            ).limit(1)
        )
        return fila is None

    def _sincronizar(self, db: Session | None, login: str, dn: str,
                     atributos: dict[str, Any], grupos: list[str],
                     rol: str) -> ResultadoLdap:
        """Actualiza nombre, correo, DN, identificador externo y rol."""
        cfg = self.cfg
        identificador = _primero(
            atributos.get((cfg.ldap_attr_login or "samaccountname").casefold()))
        identificador = str(identificador or login or "").strip()
        nombre = str(_primero(
            atributos.get((cfg.ldap_attr_nombre or "displayname").casefold()))
            or "").strip()
        email = str(_primero(
            atributos.get((cfg.ldap_attr_email or "mail").casefold()))
            or "").strip().casefold()

        if db is None:
            return ResultadoLdap(motivo="ok", detalle={"rol": rol})

        usuario = buscar_usuario(db, login)
        if usuario is None and identificador \
                and identificador.casefold() != (login or "").casefold():
            usuario = buscar_usuario(db, identificador)

        if usuario is None:
            if not cfg.ldap_auto_provision:
                self._auditar(db, None, "ldap_fail", {"motivo": "sin_cuenta"})
                return ResultadoLdap(motivo="sin_cuenta")
            return self._provisionar(db, login, identificador, dn, nombre,
                                     email, rol)

        if not usuario.is_active:
            self._auditar(db, usuario, "ldap_fail", {"motivo": "inactivo"})
            return ResultadoLdap(motivo="inactivo")
        if usuario.auth_provider != "ldap":
            # Una cuenta local con el mismo nombre no se convierte en LDAP.
            self._auditar(db, usuario, "ldap_error",
                          {"motivo": "cuenta_local", "codigo": "proveedor"})
            return ResultadoLdap(motivo="error",
                                 detalle={"codigo": "proveedor"})

        cambios: list[str] = []
        if nombre and usuario.nombre != nombre:
            usuario.nombre = nombre[:200]
            cambios.append("nombre")
        if email and usuario.email != email \
                and self._email_disponible(db, email, usuario.id):
            usuario.email = email
            cambios.append("email")
        usuario.ldap_dn = dn
        if identificador:
            usuario.external_id = identificador[:255]
        if usuario.role_source == "ldap" and usuario.rol != rol:
            usuario.rol = rol
            # Igual que en el alta: el rol mapeado desde el directorio impone
            # su política de segundo factor (admin/supervisión -> TOTP).
            usuario.mfa_policy = mfa_policy_para_rol(rol)
            cambios.append("rol")
        db.flush()
        if cambios:
            self._auditar(db, usuario, "ldap_sync", {"campos": sorted(cambios)})
        db.commit()
        return ResultadoLdap(usuario=usuario, motivo="ok",
                             detalle={"rol": rol, "campos": sorted(cambios)})

    def _provisionar(self, db: Session, login: str, identificador: str,
                     dn: str, nombre: str, email: str,
                     rol: str) -> ResultadoLdap:
        """Crea la cuenta local en el primer login válido (opt-in).

        Nunca crea una cuenta sin rol mapeado: eso dejaría un usuario sin
        permisos y con acceso. Tampoco sobreescribe un ``username`` o correo
        existentes.
        """
        username = (identificador or login or "").strip()
        if not username or buscar_usuario(db, username) is not None:
            self._auditar(db, None, "ldap_fail", {"motivo": "sin_cuenta"})
            return ResultadoLdap(motivo="sin_cuenta")
        if email and buscar_usuario(db, email) is not None:
            self._auditar(db, None, "ldap_fail", {"motivo": "email_en_uso"})
            return ResultadoLdap(motivo="sin_cuenta")

        usuario = Usuario(
            nombre=(nombre or username)[:200],
            username=username[:100],
            email=email or None,
            rol=rol,
            auth_provider="ldap",
            password_hash=None,
            is_active=True,
            mfa_policy=mfa_policy_para_rol(rol),
            role_source="ldap",
            ldap_dn=dn,
            external_id=username[:255],
        )
        db.add(usuario)
        db.flush()
        self._auditar(db, usuario, "user_created",
                      {"origen": "ldap", "rol": rol})
        db.commit()
        return ResultadoLdap(usuario=usuario, motivo="ok",
                             detalle={"rol": rol, "provisionado": True})

    # --- Diagnóstico para TI ----------------------------------------------------
    def diagnostico(self, login: str | None = None) -> DiagnosticoLdap:
        """Comprueba configuración, conexión, bind, base y (opcional) usuario.

        No autentica a nadie: usa la cuenta de servicio y nunca pide ni
        imprime contraseñas. Es la implementación de ``ldap-check``.
        """
        cfg = self.cfg
        if not cfg.ldap_enabled:
            return DiagnosticoLdap(
                habilitado=False, ok=False,
                pasos=[PasoDiagnostico(
                    "Directorio deshabilitado (EET_LDAP_ENABLED=false)", True,
                    "no se abrió ninguna conexión")],
            )

        faltan = configuracion_faltante(cfg)
        if faltan:
            return DiagnosticoLdap(
                habilitado=True, ok=False,
                pasos=[PasoDiagnostico(
                    "Configuración incompleta", False, ", ".join(faltan))],
            )

        pasos: list[PasoDiagnostico] = []
        conn = None
        try:
            conn = self._conectar(cfg.ldap_bind_dn, cfg.ldap_bind_password)
            modo = "LDAPS" if (cfg.ldap_url or "").casefold().startswith("ldaps://") \
                else ("StartTLS" if cfg.ldap_starttls else "sin TLS")
            pasos.append(PasoDiagnostico(
                "Conexión y bind de servicio", True,
                f"{cfg.ldap_url} ({modo})"))
        except (LDAPException, _ErrorLdap, socket.timeout, OSError,
                _BindFallido) as exc:
            pasos.append(PasoDiagnostico(
                "Conexión y bind de servicio", False, _codigo_error(exc)))
            return DiagnosticoLdap(habilitado=True, ok=False, pasos=pasos)

        try:
            ejemplos = self._buscar(
                conn, "(&(objectClass=*))", ["cn", "distinguishedName"],
                size_limit=5)
            pasos.append(PasoDiagnostico(
                "Entradas visibles en la base", True,
                f"{len(ejemplos)} de ejemplo (máx. 5)"))

            grupos_entradas = self._buscar(
                conn, "(&(objectClass=group))", ["cn", "distinguishedName"],
                size_limit=500)
            nombres = []
            for entrada in grupos_entradas[:5]:
                nombre = _primero(_atributos_entrada(entrada).get("cn"))
                nombres.append(str(nombre or _dn_entrada(entrada) or "?"))
            pasos.append(PasoDiagnostico(
                "Grupos en la base", True,
                f"{len(grupos_entradas)} visible(s) (máx. 500)"))

            info_usuario = None
            if login:
                try:
                    entradas = self._buscar(
                        conn, construir_filtro(login, cfg),
                        self._atributos_busqueda(), size_limit=2)
                except (LDAPException, _ErrorLdap, socket.timeout, OSError) as exc:
                    pasos.append(PasoDiagnostico(
                        f"Búsqueda del usuario {login}", False,
                        _codigo_error(exc)))
                    entradas = None
                if entradas is not None:
                    if not entradas:
                        pasos.append(PasoDiagnostico(
                            f"Búsqueda del usuario {login}", False,
                            "no encontrado"))
                    elif len(entradas) > 1:
                        pasos.append(PasoDiagnostico(
                            f"Búsqueda del usuario {login}", False,
                            "más de una entrada (ambiguo)"))
                    else:
                        entrada = entradas[0]
                        dn = _dn_entrada(entrada)
                        grupos = self._grupos(conn, entrada, dn)
                        rol = elegir_rol(grupos, cfg.ldap_role_map_dict,
                                         cfg.ldap_default_role)
                        info_usuario = {"dn": dn, "grupos": grupos, "rol": rol}
                        pasos.append(PasoDiagnostico(
                            f"Búsqueda del usuario {login}", bool(dn),
                            f"{len(grupos)} grupo(s); rol resultante: "
                            f"{rol or 'sin rol (denegado)'}"))

            return DiagnosticoLdap(
                habilitado=True, ok=all(paso.ok for paso in pasos),
                pasos=pasos, grupos=nombres, usuario=info_usuario)
        except (LDAPException, _ErrorLdap, socket.timeout, OSError) as exc:
            pasos.append(PasoDiagnostico(
                "Búsqueda en la base", False, _codigo_error(exc)))
            return DiagnosticoLdap(habilitado=True, ok=False, pasos=pasos,
                                   grupos=[])
        finally:
            self._cerrar(conn)
