"""Proveedor LDAP/AD (Fase A5) con conexión simulada.

Las pruebas unitarias no tocan PostgreSQL; las de integración usan la base y
se ejecutan con:

    uv run pytest -m integration tests/test_auth_ldap.py -q

El directorio se simula con :class:`DirectorioFake`, inyectado en
``providers_common._FABRICA_CONEXION``: nunca se abre un socket real.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import delete, func, select

from app.auth import providers_common, service
from app.auth.providers import (
    LdapProvider,
    construir_filtro,
    elegir_rol,
    normalizar_mapa,
)
from app.core.config import settings

BIND_DN = "CN=svc-eet,OU=Servicios,DC=aep,DC=test"
BIND_PASSWORD = "Clave-Servicio-2026"
BASE_DN = "DC=aep,DC=test"

SUPERVISION_DN = f"CN=EET-Supervision,OU=Grupos,{BASE_DN}"
CARGA_DN = f"CN=EET-Carga,OU=Grupos,{BASE_DN}"
CONSULTA_DN = f"CN=EET-Consulta,OU=Grupos,{BASE_DN}"

MAPA = {
    SUPERVISION_DN: "supervision",
    CARGA_DN: "cargador",
    CONSULTA_DN: "consulta",
}

PASSWORD_DOMINIO = "Clave-Dominio-2026"


# --- Directorio simulado --------------------------------------------------------
class EntradaFake:
    """Entrada LDAP mínima compatible con ``_atributos_entrada``."""

    def __init__(self, dn: str, **atributos):
        self.entry_dn = dn
        self.entry_attributes_as_dict = dict(atributos)


class ConexionFake:
    """Conexión con la superficie que usa ``LdapProvider``."""

    def __init__(self, directorio: DirectorioFake, usuario, password, **kwargs):
        self.directorio = directorio
        self.usuario = usuario
        self.password = password
        self.kwargs = kwargs
        self.entries: list = []
        self.response: list = []
        self.busquedas: list[dict] = []
        self.starttls = False
        self.cerrada = False

    def start_tls(self) -> bool:
        self.starttls = True
        return True

    def bind(self) -> bool:
        self.directorio.intentos_bind.append((self.usuario, self.password))
        if self.directorio.error_bind is not None:
            raise self.directorio.error_bind
        if self.usuario == self.directorio.bind_dn:
            return not self.directorio.fallo_bind_servicio
        return self.password == self.directorio.passwords.get(self.usuario)

    def search(self, search_base, search_filter, search_scope=None,
               attributes=None, size_limit=0, **kwargs) -> bool:
        self.busquedas.append({
            "base": search_base,
            "filtro": search_filter,
            "atributos": list(attributes or []),
            "limite": size_limit,
        })
        if self.directorio.error_busqueda is not None:
            raise self.directorio.error_busqueda
        minuscula = (search_filter or "").casefold()
        if "objectclass=group" in minuscula:
            self.entries = list(self.directorio.grupos_entradas)
        elif "objectclass=*" in minuscula:
            self.entries = list(self.directorio.base_entradas)
        else:
            self.entries = self.directorio.usuarios_coincidentes(search_filter)
        if size_limit:
            self.entries = self.entries[:size_limit]
        self.response = self.entries
        return bool(self.entries)

    def unbind(self) -> None:
        self.cerrada = True


class DirectorioFake:
    """Servidor LDAP en memoria con cuentas, grupos y errores inyectables."""

    def __init__(self) -> None:
        self.bind_dn = BIND_DN
        self.passwords: dict[str, str] = {}
        self.usuarios: list[EntradaFake] = []
        self.grupos_entradas: list[EntradaFake] = [
            EntradaFake(SUPERVISION_DN, cn="EET-Supervision"),
            EntradaFake(CARGA_DN, cn="EET-Carga"),
        ]
        self.base_entradas: list[EntradaFake] = [
            EntradaFake(f"CN=Users,{BASE_DN}", cn="Users"),
            EntradaFake(f"OU=Grupos,{BASE_DN}", cn="Grupos"),
        ]
        self.error_bind: BaseException | None = None
        self.error_busqueda: BaseException | None = None
        self.fallo_bind_servicio = False
        self.conexiones: list[ConexionFake] = []
        self.intentos_bind: list[tuple] = []

    # Fábrica compatible con ``fabrica_por_defecto``.
    def factory(self, servidor, **kwargs) -> ConexionFake:
        usuario = kwargs.pop("user", None)
        password = kwargs.pop("password", None)
        conexion = ConexionFake(self, usuario, password, **kwargs)
        self.conexiones.append(conexion)
        return conexion

    def usuarios_coincidentes(self, filtro: str) -> list[EntradaFake]:
        encontrados = []
        for entrada in self.usuarios:
            atributos = {k.casefold(): v
                         for k, v in entrada.entry_attributes_as_dict.items()}
            login = str(atributos.get("samaccountname") or "")
            if login and escape_filter_chars(login) in filtro:
                encontrados.append(entrada)
        return encontrados

    def agregar_usuario(self, username: str, *, nombre: str | None = None,
                        email: str | None = None,
                        grupos: list[str] | None = None) -> EntradaFake:
        dn = f"CN={nombre or username},OU=Usuarios,{BASE_DN}"
        entrada = EntradaFake(
            dn,
            sAMAccountName=username,
            displayName=nombre or f"Usuario {username}",
            mail=email or f"{username}@aep.test",
            memberOf=[CONSULTA_DN] if grupos is None else grupos,
        )
        self.usuarios.append(entrada)
        self.passwords[dn] = PASSWORD_DOMINIO
        return entrada


@pytest.fixture
def directorio() -> DirectorioFake:
    return DirectorioFake()


def configurar_ldap(monkeypatch, directorio: DirectorioFake, **extra) -> None:
    """Activa LDAP con el directorio simulado (sin red)."""
    valores = {
        "ldap_enabled": True,
        "ldap_url": "ldap://dc.aep.test:389",
        "ldap_bind_dn": BIND_DN,
        "ldap_bind_password": BIND_PASSWORD,
        "ldap_base_dn": BASE_DN,
        "ldap_user_filter": "(sAMAccountName={login})",
        "ldap_role_map": json.dumps(MAPA),
        "ldap_timeout": 2,
        "ldap_starttls": True,
        "ldap_default_role": "",
        "ldap_auto_provision": False,
        "ldap_group_attr": "memberOf",
        "ldap_attr_nombre": "displayName",
        "ldap_attr_email": "mail",
        "ldap_attr_login": "sAMAccountName",
    }
    valores.update(extra)
    for nombre, valor in valores.items():
        monkeypatch.setattr(settings, nombre, valor)
    monkeypatch.setattr(providers_common, "_FABRICA_CONEXION", directorio.factory)
    monkeypatch.setattr(service, "RETARDO_MINIMO_LDAP", 0.0)


@pytest.fixture
def ldap_activo(monkeypatch, directorio):
    configurar_ldap(monkeypatch, directorio)
    return directorio


# --- Base de datos de prueba ----------------------------------------------------
@pytest.fixture
def usuarios_ldap():
    """Crea cuentas ``auth_provider=ldap`` y limpia todo al terminar."""
    from app.core.database import SessionLocal
    from app.models import Auditoria, LoginAttempt, Sesion, Usuario

    with SessionLocal() as db:
        corte_auditoria = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0

    def _crear(*, username: str | None = None, email: str | None = None,
               rol: str = "consulta", role_source: str = "ldap",
               activo: bool = True, nombre: str | None = None
               ) -> SimpleNamespace:
        username = username or f"prueba-ldap-{uuid.uuid4().hex[:8]}"
        email = email or f"{username}@aep.test"
        with SessionLocal() as db:
            usuario = Usuario(
                nombre=nombre or f"Usuario {username}",
                username=username,
                email=email,
                rol=rol,
                auth_provider="ldap",
                password_hash=None,
                is_active=activo,
                mfa_policy="disabled",
                role_source=role_source,
            )
            db.add(usuario)
            db.flush()
            identificador = usuario.id
            db.commit()
        return SimpleNamespace(id=identificador, username=username,
                               email=email, rol=rol, nombre=nombre)

    yield _crear

    with SessionLocal() as db:
        ids = list(db.scalars(
            select(Usuario.id).where(
                Usuario.username.like("prueba-ldap-%"))))
        actores = [f"usuario:{identificador}" for identificador in ids]
        if ids:
            db.execute(delete(Sesion).where(Sesion.usuario_id.in_(ids)))
            db.execute(delete(Usuario).where(Usuario.id.in_(ids)))
        db.execute(delete(Auditoria).where(
            Auditoria.id > corte_auditoria,
            Auditoria.actor.in_(actores + ["sistema"]) if actores
            else Auditoria.actor == "sistema"))
        db.execute(delete(LoginAttempt).where(
            LoginAttempt.id > corte_intentos))
        db.commit()


def _corte_auditoria() -> int:
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        return int(db.scalar(select(func.max(Auditoria.id))) or 0)


def _auditoria(desde: int, accion: str) -> list:
    from app.core.database import SessionLocal
    from app.models import Auditoria

    with SessionLocal() as db:
        return list(db.scalars(
            select(Auditoria).where(
                Auditoria.id > desde, Auditoria.accion == accion,
            ).order_by(Auditoria.id)))


def _login(username: str, password: str = PASSWORD_DOMINIO):
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return service.autenticar(db, username, password,
                                  ip="10.0.0.5", user_agent="pytest-ldap")


# --- Unitarias (sin red ni base) -------------------------------------------------
def test_normalizar_mapa_pasa_todo_a_minusculas():
    mapa = normalizar_mapa({"CN=EET-Supervision,OU=G,DC=X": "SUPERVISION"})
    assert mapa == {"cn=eet-supervision,ou=g,dc=x": "supervision"}


def test_prioridad_admin_sobre_el_resto():
    mapa = {
        "cn=eet-consulta,ou=g,dc=x": "consulta",
        "cn=eet-supervision,ou=g,dc=x": "supervision",
        "cn=eet-admin,ou=g,dc=x": "admin",
    }
    grupos = ["CN=EET-Consulta,OU=G,DC=X",
              "CN=EET-Supervision,OU=G,DC=X",
              "CN=EET-Admin,OU=G,DC=X"]
    assert elegir_rol(grupos, mapa, "") == "admin"


def test_mapeo_acepta_cn_suelto_y_deniega_por_defecto():
    assert elegir_rol(["EET-Carga"], {"eet-carga": "cargador"}, "") == "cargador"
    assert elegir_rol(["CN=Desconocido,OU=G,DC=X"], {}, "") is None
    assert elegir_rol([], {}, "consulta") == "consulta"


def test_filtro_escapa_intentos_de_inyeccion():
    filtro = construir_filtro("*)(uid=*")
    assert escape_filter_chars("*)(uid=*") in filtro
    assert "*)(uid=*" not in filtro
    assert filtro.startswith("(&(")


def test_proveedor_apagado_no_abre_conexion(monkeypatch):
    monkeypatch.setattr(settings, "ldap_enabled", False)
    llamado = []

    def _prohibido(*args, **kwargs):
        llamado.append(True)
        raise AssertionError("no debe abrir una conexión LDAP")

    monkeypatch.setattr(providers_common, "_FABRICA_CONEXION", _prohibido)
    assert LdapProvider().authenticate(None, "alguien", "clave") == (None, False)
    assert llamado == []


def test_diagnostico_apagado_no_abre_conexion(monkeypatch):
    monkeypatch.setattr(settings, "ldap_enabled", False)
    monkeypatch.setattr(providers_common, "_FABRICA_CONEXION",
                        lambda *a, **k: pytest.fail("no debe conectar"))
    diagnostico = LdapProvider().diagnostico()
    assert diagnostico.habilitado is False
    assert diagnostico.ok is False
    assert "deshabilitado" in diagnostico.pasos[0].nombre.casefold()


def test_diagnostico_con_fake_reporta_grupos_y_rol(ldap_activo):
    directorio = ldap_activo
    entrada = directorio.agregar_usuario(
        "jperez", nombre="Juan Pérez", grupos=[SUPERVISION_DN])
    diagnostico = LdapProvider().diagnostico("jperez")
    assert diagnostico.ok is True
    assert all(paso.ok for paso in diagnostico.pasos)
    assert diagnostico.usuario["dn"] == entrada.entry_dn
    assert diagnostico.usuario["rol"] == "supervision"
    assert diagnostico.grupos


def test_ldap_usa_starttls_y_ldaps_tls_directo(ldap_activo, monkeypatch):
    directorio = ldap_activo
    directorio.agregar_usuario("starttls")
    LdapProvider().diagnostico()
    assert directorio.conexiones[0].starttls is True

    monkeypatch.setattr(settings, "ldap_url", "ldaps://dc.aep.test:636")
    directorio.conexiones.clear()
    LdapProvider().diagnostico()
    assert directorio.conexiones[0].starttls is False


def test_group_attr_member_hace_busqueda_inversa(ldap_activo, monkeypatch):
    monkeypatch.setattr(settings, "ldap_group_attr", "member")
    directorio = ldap_activo
    entrada = directorio.agregar_usuario("miembro")
    # La entrada no trae memberOf: los grupos se buscan por ``member``.
    entrada.entry_attributes_as_dict.pop("memberOf", None)

    diagnostico = LdapProvider().diagnostico("miembro")

    assert diagnostico.ok is True
    assert diagnostico.usuario["rol"] == "supervision"
    filtros = [b["filtro"] for b in directorio.conexiones[0].busquedas]
    assert any("member=" in filtro for filtro in filtros)


def test_cli_ldap_check_apagado(monkeypatch, capsys):
    from app.cli import main

    monkeypatch.setattr(settings, "ldap_enabled", False)
    monkeypatch.setattr(providers_common, "_FABRICA_CONEXION",
                        lambda *a, **k: pytest.fail("no debe conectar"))
    assert main(["ldap-check"]) == 0
    salida = capsys.readouterr().out
    assert "deshabilitado" in salida.casefold()
    assert "ninguna conexión" in salida.casefold()


def test_cli_ldap_mapeo_no_expone_secretos(monkeypatch, capsys):
    from app.cli import main

    monkeypatch.setattr(settings, "ldap_role_map", json.dumps(MAPA))
    monkeypatch.setattr(settings, "ldap_default_role", "")
    monkeypatch.setattr(settings, "ldap_auto_provision", False)
    monkeypatch.setattr(settings, "ldap_bind_password", BIND_PASSWORD)
    assert main(["ldap-mapeo"]) == 0
    salida = capsys.readouterr().out
    assert "supervision" in salida
    assert "admin > supervision > cargador > consulta > asistente" in salida
    assert BIND_PASSWORD not in salida


# --- Integración (PostgreSQL) ----------------------------------------------------
@pytest.mark.integration
def test_login_valido_sincroniza_nombre_email_y_rol(ldap_activo, usuarios_ldap):
    usuario = usuarios_ldap(nombre="Nombre Anterior")
    entrada = ldap_activo.agregar_usuario(
        usuario.username, nombre="Juan Pérez", email="juan.perez@aep.test",
        grupos=[SUPERVISION_DN])
    corte = _corte_auditoria()

    resultado = _login(usuario.username)

    assert resultado.ok is True
    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        fila = db.get(Usuario, usuario.id)
        assert fila.rol == "supervision"
        assert fila.nombre == "Juan Pérez"
        assert fila.email == "juan.perez@aep.test"
        assert fila.ldap_dn == entrada.entry_dn
        assert fila.external_id == usuario.username
        assert fila.password_hash is None
        assert fila.last_login_at is not None

    assert _auditoria(corte, "login_ok")
    sincronizaciones = _auditoria(corte, "ldap_sync")
    assert sincronizaciones
    detalle = json.dumps([fila.detalle for fila in sincronizaciones])
    assert PASSWORD_DOMINIO not in detalle


@pytest.mark.integration
def test_password_incorrecta_en_el_segundo_bind_falla(ldap_activo,
                                                      usuarios_ldap):
    usuario = usuarios_ldap()
    ldap_activo.agregar_usuario(usuario.username, grupos=[SUPERVISION_DN])
    corte = _corte_auditoria()

    resultado = _login(usuario.username, password="Clave-Incorrecta-2026")

    assert resultado.ok is False
    assert resultado.motivo == "credenciales"
    assert _auditoria(corte, "login_fail")
    # Se validó contra el DN del usuario, no contra la cuenta de servicio.
    assert any(intento[0] != BIND_DN for intento in ldap_activo.intentos_bind)


@pytest.mark.integration
def test_usuario_no_encontrado_falla_generico(ldap_activo, usuarios_ldap):
    usuario = usuarios_ldap()
    corte = _corte_auditoria()

    resultado = _login(usuario.username)

    assert resultado.ok is False
    assert _auditoria(corte, "ldap_fail")
    assert _auditoria(corte, "login_fail")


@pytest.mark.integration
def test_entrada_duplicada_falla_y_audita_ambiguo(ldap_activo, usuarios_ldap):
    usuario = usuarios_ldap()
    ldap_activo.agregar_usuario(usuario.username, grupos=[SUPERVISION_DN])
    ldap_activo.agregar_usuario(usuario.username, grupos=[SUPERVISION_DN])
    corte = _corte_auditoria()

    resultado = _login(usuario.username)

    assert resultado.ok is False
    assert _auditoria(corte, "ldap_ambiguo")


@pytest.mark.integration
def test_sin_grupo_mapeado_no_entra(ldap_activo, usuarios_ldap):
    usuario = usuarios_ldap()
    ldap_activo.agregar_usuario(usuario.username,
                                grupos=["CN=Otro-Grupo,OU=G,DC=aep,DC=test"])
    corte = _corte_auditoria()

    resultado = _login(usuario.username)

    assert resultado.ok is False
    assert _auditoria(corte, "ldap_sin_rol")


@pytest.mark.integration
def test_rol_por_defecto_permite_entrar(ldap_activo, usuarios_ldap,
                                        monkeypatch):
    monkeypatch.setattr(settings, "ldap_default_role", "consulta")
    usuario = usuarios_ldap(rol="asistente")
    ldap_activo.agregar_usuario(usuario.username, grupos=[])
    resultado = _login(usuario.username)
    assert resultado.ok is True

    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        fila = db.get(Usuario, usuario.id)
        assert fila.rol == "consulta"


@pytest.mark.integration
def test_prioridad_de_grupos_consulta_y_supervision(ldap_activo, usuarios_ldap):
    usuario = usuarios_ldap(rol="consulta")
    ldap_activo.agregar_usuario(usuario.username,
                                grupos=[CONSULTA_DN, SUPERVISION_DN])
    assert _login(usuario.username).ok is True

    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        assert db.get(Usuario, usuario.id).rol == "supervision"


@pytest.mark.integration
def test_role_source_manual_no_se_sobrescribe(ldap_activo, usuarios_ldap):
    usuario = usuarios_ldap(rol="supervision", role_source="manual")
    ldap_activo.agregar_usuario(usuario.username, grupos=[CONSULTA_DN])
    assert _login(usuario.username).ok is True

    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        assert db.get(Usuario, usuario.id).rol == "supervision"


@pytest.mark.integration
@pytest.mark.parametrize("error", [
    LDAPException("el DC no responde"),
    TimeoutError("tiempo agotado"),
    OSError("red inalcanzable"),
])
def test_error_de_conexion_falla_controlado_y_audita(ldap_activo,
                                                     usuarios_ldap, error):
    usuario = usuarios_ldap()
    ldap_activo.agregar_usuario(usuario.username)
    ldap_activo.error_bind = error
    corte = _corte_auditoria()

    resultado = _login(usuario.username)

    assert resultado.ok is False
    assert resultado.motivo == "credenciales"
    filas = _auditoria(corte, "ldap_error")
    assert filas
    assert filas[-1].detalle.get("codigo")


@pytest.mark.integration
def test_auto_provision_crea_usuario_con_rol(ldap_activo, usuarios_ldap,
                                             monkeypatch):
    monkeypatch.setattr(settings, "ldap_auto_provision", True)
    username = f"prueba-ldap-auto-{uuid.uuid4().hex[:6]}"
    ldap_activo.agregar_usuario(username, nombre="Auto Prueba",
                                grupos=[SUPERVISION_DN])

    resultado = _login(username)

    assert resultado.ok is True
    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        fila = db.scalar(select(Usuario).where(Usuario.username == username))
        assert fila is not None
        assert fila.rol == "supervision"
        assert fila.auth_provider == "ldap"
        assert fila.role_source == "ldap"
        # El rol mapeado (supervisión) impone TOTP desde el alta.
        assert fila.mfa_policy == "totp"
        assert fila.password_hash is None
        assert fila.ldap_dn


@pytest.mark.integration
def test_auto_provision_sin_rol_no_crea(ldap_activo, usuarios_ldap,
                                        monkeypatch):
    monkeypatch.setattr(settings, "ldap_auto_provision", True)
    username = f"prueba-ldap-auto-{uuid.uuid4().hex[:6]}"
    ldap_activo.agregar_usuario(username,
                                grupos=["CN=Otro,OU=G,DC=aep,DC=test"])

    resultado = _login(username)

    assert resultado.ok is False
    from app.core.database import SessionLocal
    from app.models import Usuario

    with SessionLocal() as db:
        assert db.scalar(
            select(Usuario).where(Usuario.username == username)) is None


@pytest.mark.integration
def test_ldap_apagado_falla_sin_conexion_y_audita(monkeypatch, usuarios_ldap):
    usuario = usuarios_ldap()
    monkeypatch.setattr(settings, "ldap_enabled", False)
    monkeypatch.setattr(providers_common, "_FABRICA_CONEXION",
                        lambda *a, **k: pytest.fail("no debe conectar"))
    corte = _corte_auditoria()

    resultado = _login(usuario.username)

    assert resultado.ok is False
    assert resultado.motivo == "credenciales"
    assert _auditoria(corte, "ldap_disabled")


@pytest.mark.integration
def test_el_filtro_de_busqueda_escapa_el_login(ldap_activo, usuarios_ldap,
                                               monkeypatch):
    # Sin aprovisionamiento automático un login inexistente ni siquiera toca
    # LDAP; se activa para poder inspeccionar el filtro que se envía.
    monkeypatch.setattr(settings, "ldap_auto_provision", True)
    usuarios_ldap()
    corte = _corte_auditoria()

    _login("*)(uid=*")

    assert _auditoria(corte, "ldap_fail")
    filtros = [busqueda["filtro"] for busqueda in ldap_activo.conexiones[0].busquedas]
    assert filtros
    assert escape_filter_chars("*)(uid=*") in filtros[0]
    assert "*)(uid=*" not in filtros[0]

