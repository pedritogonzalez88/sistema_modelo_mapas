"""Configuración de la aplicación, leída del entorno con prefijo ``EET_``.

Los valores por defecto son deliberadamente seguros y conservadores:

- ``auth_enabled=False``: el sistema se comporta como antes de la Fase A0/A1.
- ``cookie_secure=True`` y ``admin_internal_only=True``: apto para exposición a
  internet; en desarrollo local se pueden relajar por entorno.

Las validaciones de esta clase se ejecutan al importar el módulo: si se activa
autenticación o LDAP sin los datos obligatorios, la aplicación no arranca.
"""
import ipaddress
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Roles válidos del sistema. Se duplican aquí para no acoplar la configuración
#: al módulo de permisos; deben coincidir con ``app.auth.permissions``.
_ROLES_AUTH = ("admin", "supervision", "cargador", "consulta", "asistente")

#: Entornos admitidos. ``prod`` exige el paquete completo de endurecimiento;
#: ``staging`` solo advierte; ``dev`` relaja las exigencias para trabajar en
#: local sobre HTTP.
_ENTORNOS = ("dev", "staging", "prod")

#: Longitudes mínimas de los secretos en producción.
_SECRETO_MIN_PROD = 32
_PEPPER_MIN_PROD = 16

_log = logging.getLogger(__name__)


def _redes(valor: str, variable: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parsea una lista de CIDR separados por coma; error explícito si es inválida."""
    redes: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for crudo in (valor or "").split(","):
        item = crudo.strip()
        if not item:
            continue
        try:
            redes.append(ipaddress.ip_network(item, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"{variable} contiene una red inválida: '{item}'."
            ) from exc
    return redes


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EET_", env_file=".env", extra="ignore")

    app_name: str = "Validador EET"
    database_url: str = "postgresql+psycopg://eet:eet@localhost:5432/eet"
    upload_dir: str = "./uploads"
    srid: int = 4326  # WGS84; ajustar al sistema de referencia de la malla oficial
    regla_version: str = "tabla7-v1"

    # --- Autenticación y sesiones ---
    auth_enabled: bool = False
    secret_key: str = ""  # clave Fernet (base64) o cadena larga aleatoria
    auth_pepper: str = ""  # pepper para HMAC de códigos de un solo uso
    session_cookie: str = "__Host-eet_session"
    session_absolute_min: int = 720  # 12 h de vida absoluta
    session_idle_min: int = 30  # 30 min de inactividad
    cookie_secure: bool = True
    login_max_intentos: int = 5
    login_bloqueo_min: int = 15
    login_ip_max_15min: int = 20
    mfa_issuer: str = "Validador EET (AEP)"
    mfa_window: int = 1

    # --- Correo institucional ---
    email_backend: str = "console"  # console | file | smtp
    # Carpeta de los .eml del backend ``file`` (desarrollo y pruebas).
    email_dir: str = "uploads/mail"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str = "Validador EET <no-reply@localhost>"
    public_base_url: str = "http://localhost:8000"

    # --- LDAP / Active Directory (deshabilitado hasta que TI lo habilite) ---
    ldap_enabled: bool = False
    ldap_url: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_base_dn: str | None = None
    ldap_user_filter: str = "(sAMAccountName={login})"
    ldap_role_map: str = "{}"  # JSON grupo→rol
    ldap_timeout: int = 5
    #: ``ldap://`` negocia StartTLS; ``ldaps://`` usa TLS directo (no se toca).
    ldap_starttls: bool = True
    #: Rol cuando ningún grupo mapea. Vacío = denegar el acceso (fail closed).
    ldap_default_role: str = ""
    #: Crea la cuenta local en el primer login LDAP válido (opt-in explícito).
    ldap_auto_provision: bool = False
    #: ``memberOf`` (atributo del usuario) o ``member`` (búsqueda inversa).
    ldap_group_attr: str = "memberOf"
    ldap_attr_nombre: str = "displayName"
    ldap_attr_email: str = "mail"
    ldap_attr_login: str = "sAMAccountName"

    # --- Entorno y despliegue ---
    #: ``dev`` | ``staging`` | ``prod``. En ``prod`` el validador de abajo exige
    #: HTTPS, cookies ``Secure``, SMTP real, docs ocultos y secretos largos.
    environment: str = "dev"
    #: CIDR del reverse proxy de confianza, separados por coma. Vacío = no se
    #: confía en ``X-Forwarded-*`` y se usa ``request.client.host``.
    trusted_proxy_ips: str = ""

    # --- Endurecimiento ---
    admin_internal_only: bool = True
    internal_cidrs: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"
    max_upload_mb: int = 20
    expose_docs: bool = False

    # --- Cifrado multi-clave ---
    # Formato: "v2:<clave>,v1:<clave>"; la primera clave es la vigente.
    fernet_keys: Any = ""

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def email_path(self) -> Path:
        """Carpeta del backend ``file`` del mailer (se crea si no existe)."""
        p = Path(self.email_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def ldap_role_map_dict(self) -> dict[str, str]:
        """Mapa ``grupo LDAP -> rol``; ``{}`` cuando no hay configuración.

        Se parsea como JSON en cada acceso (es barato y evita cachés
        mutables). Las claves se normalizan a **minúsculas** para comparar sin
        distinguir mayúsculas con los valores que devuelve el directorio. Un
        JSON inválido produce un error explícito en lugar de degradar
        silenciosamente los permisos.
        """
        texto = (self.ldap_role_map or "").strip()
        if not texto:
            return {}
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "EET_LDAP_ROLE_MAP debe ser un objeto JSON válido "
                f"(grupo LDAP -> rol): {exc.msg} en la posición {exc.pos}."
            ) from exc
        if not isinstance(datos, dict):
            raise ValueError(  # noqa: TRY004 - validación de configuración
                "EET_LDAP_ROLE_MAP debe ser un objeto JSON (grupo -> rol).")
        mapa = {}
        for grupo, rol in datos.items():
            if not isinstance(grupo, str) or not isinstance(rol, str):
                raise ValueError(  # noqa: TRY004 - validación de configuración
                    "EET_LDAP_ROLE_MAP exige claves y valores de texto "
                    "(grupo LDAP -> rol)."
                )
            clave = grupo.strip().casefold()
            if not clave:
                raise ValueError("EET_LDAP_ROLE_MAP contiene un grupo vacío.")
            mapa[clave] = rol.strip().casefold()
        return mapa

    @property
    def internal_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Lista de redes internas permitidas para administración y auditoría."""
        return _redes(self.internal_cidrs, "EET_INTERNAL_CIDRS")

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Redes del reverse proxy en las que se confía para ``X-Forwarded-*``.

        Vacío significa **no** confiar en ninguna cabecera de proxy: la IP real
        es siempre ``request.client.host``. Esa es la opción correcta cuando la
        aplicación se publica directamente o detrás de un proxy que ya reescribe
        la conexión (p. ej. el balanceador de la plataforma).
        """
        return _redes(self.trusted_proxy_ips, "EET_TRUSTED_PROXY_IPS")

    @property
    def trusted_proxy_hosts(self) -> list[str]:
        """Valores para ``trusted_hosts`` de ``ProxyHeadersMiddleware``."""
        return [crudo.strip() for crudo in (self.trusted_proxy_ips or "").split(",")
                if crudo.strip()]

    @property
    def fernet_key_list(self) -> list[tuple[str, str]]:
        """Claves de cifrado en orden de prioridad: ``[(id, material), ...]``.

        Acepta ``EET_FERNET_KEYS="v2:<clave>,v1:<clave>"`` para rotar sin
        perder datos. Si no está definida, se usa ``secret_key`` como clave
        ``v1``. Nunca se registran los valores en logs.
        """
        # Coercion defensiva: si el entorno pasa un dict u otro tipo, se
        # convierte a texto. Un valor invalido no tumba el arranque: se omite
        # con una advertencia y se usa secret_key como respaldo.
        crudo = self.fernet_keys
        if isinstance(crudo, (dict, list, tuple)):
            import logging as _log
            _log.getLogger(__name__).warning(
                "EET_FERNET_KEYS tiene un tipo inesperado (%s); se ignora y se "
                "usa EET_SECRET_KEY como respaldo.", type(crudo).__name__)
            crudo = ""
        crudo = (crudo or "").strip() if isinstance(crudo, str) else str(crudo or "").strip()
        if not crudo:
            return [("v1", self.secret_key)] if self.secret_key else []
        claves: list[tuple[str, str]] = []
        for parte in crudo.split(","):
            item = parte.strip()
            if not item:
                continue
            if ":" not in item:
                raise ValueError(
                    "EET_FERNET_KEYS espera pares 'id:clave' separados por comas, "
                    "por ejemplo 'v2:<base64>,v1:<base64>'."
                )
            identificador, material = item.split(":", 1)
            identificador = identificador.strip()
            material = material.strip()
            if not identificador or not material:
                raise ValueError("EET_FERNET_KEYS contiene una entrada incompleta.")
            claves.append((identificador, material))
        return claves

    @model_validator(mode="after")
    def _validar_seguridad(self) -> "Settings":
        """Falla al arrancar si falta configuración crítica, nunca en caliente."""
        entorno = (self.environment or "dev").strip().casefold()
        if entorno not in _ENTORNOS:
            raise ValueError(
                "EET_ENVIRONMENT debe ser uno de: " + ", ".join(_ENTORNOS) + "."
            )
        self.environment = entorno

        if self.auth_enabled:
            faltantes = [
                nombre
                for nombre, valor in (
                    ("EET_SECRET_KEY", self.secret_key),
                    ("EET_AUTH_PEPPER", self.auth_pepper),
                )
                if not (valor or "").strip()
            ]
            if faltantes:
                raise ValueError(
                    "Autenticación activada (EET_AUTH_ENABLED=true) sin configuración "
                    f"obligatoria: falta {', '.join(faltantes)}."
                )
        self._validar_entorno(entorno)

        if self.ldap_enabled:
            faltantes = [
                nombre
                for nombre, valor in (
                    ("EET_LDAP_URL", self.ldap_url),
                    ("EET_LDAP_BIND_DN", self.ldap_bind_dn),
                    ("EET_LDAP_BIND_PASSWORD", self.ldap_bind_password),
                    ("EET_LDAP_BASE_DN", self.ldap_base_dn),
                )
                if not (valor or "").strip()
            ]
            if faltantes:
                raise ValueError(
                    "LDAP activado (EET_LDAP_ENABLED=true) sin configuración "
                    f"obligatoria: falta {', '.join(faltantes)}."
                )
            url = (self.ldap_url or "").strip().casefold()
            if not url.startswith(("ldap://", "ldaps://")):
                raise ValueError(
                    "EET_LDAP_URL debe comenzar con 'ldap://' o 'ldaps://'."
                )
            mapa = self.ldap_role_map_dict
            invalidos = sorted({rol for rol in mapa.values()
                                if rol not in _ROLES_AUTH})
            if invalidos:
                raise ValueError(
                    "EET_LDAP_ROLE_MAP apunta a roles desconocidos: "
                    f"{', '.join(invalidos)}. Válidos: {', '.join(_ROLES_AUTH)}."
                )
            defecto = (self.ldap_default_role or "").strip().casefold()
            if defecto and defecto not in _ROLES_AUTH:
                raise ValueError(
                    "EET_LDAP_DEFAULT_ROLE debe ser un rol válido o quedar "
                    f"vacío (denegar). Válidos: {', '.join(_ROLES_AUTH)}."
                )
        # Valida el formato del mapa de roles y de las redes internas al inicio.
        _ = (self.ldap_role_map_dict, self.internal_networks,
             self.trusted_proxy_networks)
        try:
            _ = self.fernet_key_list
        except ValueError as exc:
            raise ValueError(
                "EET_FERNET_KEYS invalido: " + str(exc)
            ) from exc
        return self

    def _problemas_produccion(self) -> list[str]:
        """Incumplimientos de endurecimiento para ``prod`` (texto sin secretos).

        Nunca incluye el valor de un secreto: solo el nombre de la variable y la
        condición que no se cumple. Se reutiliza para ``staging`` (advertencia).
        """
        problemas: list[str] = []
        if not self.cookie_secure:
            problemas.append(
                "EET_COOKIE_SECURE debe ser true (cookies Secure sobre HTTPS).")
        if not (self.public_base_url or "").strip().casefold().startswith("https://"):
            problemas.append(
                "EET_PUBLIC_BASE_URL debe comenzar con https:// (enlaces de correo).")
        backend = (self.email_backend or "").strip().casefold()
        if backend != "smtp":
            problemas.append(
                "EET_EMAIL_BACKEND debe ser 'smtp' (el backend de consola no "
                "entrega códigos ni enlaces).")
        elif not (self.smtp_host or "").strip():
            problemas.append("EET_SMTP_HOST es obligatorio con EET_EMAIL_BACKEND=smtp.")
        if self.expose_docs:
            problemas.append(
                "EET_EXPOSE_DOCS debe ser false (no se publica /docs ni "
                "/openapi.json en producción).")
        if len((self.secret_key or "").strip()) < _SECRETO_MIN_PROD:
            problemas.append(
                f"EET_SECRET_KEY debe tener al menos {_SECRETO_MIN_PROD} caracteres.")
        if len((self.auth_pepper or "").strip()) < _PEPPER_MIN_PROD:
            problemas.append(
                f"EET_AUTH_PEPPER debe tener al menos {_PEPPER_MIN_PROD} caracteres.")
        if not (self.session_cookie or "").startswith("__Host-"):
            problemas.append(
                "EET_SESSION_COOKIE debe comenzar con '__Host-' (cookie atada al "
                "host y solo por HTTPS).")
        return problemas

    def _validar_entorno(self, entorno: str) -> None:
        """``prod`` falla cerrado; ``staging`` advierte; ``dev`` no exige nada.

        La comprobación solo se aplica con la autenticación activa: mientras la
        bandera esté apagada la aplicación no expone identidad y el despliegue
        gradual (Fase A0) debe seguir siendo posible.
        """
        if not self.auth_enabled or entorno == "dev":
            return
        problemas = self._problemas_produccion()
        if not problemas:
            return
        if entorno == "prod":
            raise ValueError(
                "Configuración insegura para EET_ENVIRONMENT=prod: "
                + " ".join(problemas)
            )
        for problema in problemas:
            _log.warning("Entorno staging incompleto: %s", problema)


settings = Settings()
