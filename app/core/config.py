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
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EET_", env_file=".env", extra="ignore")

    app_name: str = "Validador EET"
    database_url: str = "postgresql+psycopg://eet:eet@localhost:5433/eet"
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

    # --- Endurecimiento ---
    admin_internal_only: bool = True
    internal_cidrs: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"
    max_upload_mb: int = 20
    expose_docs: bool = False

    # --- Cifrado multi-clave ---
    # Formato: "v2:<clave>,v1:<clave>"; la primera clave es la vigente.
    fernet_keys: str = ""

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
        mutables). Un JSON inválido produce un error explícito en lugar de
        degradar silenciosamente los permisos.
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
            raise ValueError("EET_LDAP_ROLE_MAP debe ser un objeto JSON (grupo -> rol).")
        mapa = {}
        for grupo, rol in datos.items():
            if not isinstance(grupo, str) or not isinstance(rol, str):
                raise ValueError(
                    "EET_LDAP_ROLE_MAP exige claves y valores de texto "
                    "(grupo LDAP -> rol)."
                )
            mapa[grupo] = rol
        return mapa

    @property
    def internal_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Lista de redes internas permitidas para administración y auditoría."""
        redes = []
        for crudo in (self.internal_cidrs or "").split(","):
            valor = crudo.strip()
            if not valor:
                continue
            try:
                redes.append(ipaddress.ip_network(valor, strict=False))
            except ValueError as exc:
                raise ValueError(
                    f"EET_INTERNAL_CIDRS contiene una red inválida: '{valor}'."
                ) from exc
        return redes

    @property
    def fernet_key_list(self) -> list[tuple[str, str]]:
        """Claves de cifrado en orden de prioridad: ``[(id, material), ...]``.

        Acepta ``EET_FERNET_KEYS="v2:<clave>,v1:<clave>"`` para rotar sin
        perder datos. Si no está definida, se usa ``secret_key`` como clave
        ``v1``. Nunca se registran los valores en logs.
        """
        crudo = (self.fernet_keys or "").strip()
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
        # Valida el formato del mapa de roles y de las redes internas al inicio.
        self.ldap_role_map_dict
        self.internal_networks
        self.fernet_key_list
        return self


settings = Settings()
