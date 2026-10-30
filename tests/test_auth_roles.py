"""Autorización por rol y alcance de datos (Fase A2).

Cada prueba entra con una cuenta real de un rol y comprueba la matriz de
``app/auth/permissions.py`` contra el servidor: no se prueban solo los enlaces,
sino la respuesta HTTP de cada ruta sensible (200 / 403 / 422 / 400), tal como
exige el criterio de aceptación "un asistente no puede ver entregas de otro ni
acceder a Cruces/QA-QC/Exportar".

Requieren PostgreSQL y el ciclo de vida completo de la aplicación:

    uv run pytest -m integration tests/test_auth_roles.py -q
"""
import contextlib
import json
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration

COOKIE_SESION = "eet_session"  # cookie_secure=False en las pruebas
COOKIE_CSRF = "eet_csrf"
PATRON_CSRF = re.compile(r'name="csrf_token" value="([^"]*)"')
PASSWORD = "Clave-Segura-2026"

# Rutas de lectura que cada rol debe poder abrir sin error.
LECTURA_BASE = ("/", "/entregas", "/malla", "/mapa")
LECTURA_ANALISIS = ("/cruces", "/qaqc", "/reemplazos", "/exportar")


# --- Infraestructura -----------------------------------------------------------
@pytest.fixture(scope="module")
def cliente():
    """Cliente único del módulo; el inicio de sesión se rehace en cada prueba."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def roles_db():
    """Crea usuarios por rol y entregas de prueba, y deja la base como estaba.

    ``conftest.fabrica_usuarios`` no conoce ``letra_terna`` (alcance del
    asistente) ni crea entregas, así que la Fase A2 trae su propio generador:
    mismo espíritu, mismo teardown (usuarios, sesiones, auditoría e intentos).
    """
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import (Auditoria, Entrega, FichaQAQC, LoginAttempt, Sesion,
                            Usuario)
    from app.services import persist

    usuarios: list[int] = []
    entregas: list[int] = []
    fichas: list[int] = []

    with SessionLocal() as db:
        corte_auditoria = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0
        proyecto_id = persist.get_default_proyecto(db).id

    def usuario(rol: str, *, letra: str | None = None) -> SimpleNamespace:
        sufijo = uuid.uuid4().hex[:10]
        username = f"rol-{rol}-{sufijo}"
        email = f"{username}@ejemplo.test"
        with SessionLocal() as db:
            fila = Usuario(
                nombre=f"Prueba {rol} {sufijo}",
                username=username,
                email=email,
                rol=rol,
                letra_terna=letra,
                auth_provider="local",
                password_hash=hash_password(PASSWORD),
                is_active=True,
                must_change_password=False,
                mfa_policy="any",
            )
            db.add(fila)
            db.flush()
            identificador = fila.id
            db.commit()
        usuarios.append(identificador)
        return SimpleNamespace(id=identificador, username=username, email=email,
                               password=PASSWORD, rol=rol, letra_terna=letra)

    def entrega(letra: str) -> SimpleNamespace:
        """Entrega mínima: la prueba de alcance no abre el .xlsx."""
        numero_semana = uuid.uuid4().int % 100
        with SessionLocal() as db:
            fila = Entrega(
                proyecto_id=proyecto_id,
                asistente_letra=letra,
                semana=f"2026-W{numero_semana:02d}",
                tipo="avance",
                version=1,
                archivo_path="",
                sha256=uuid.uuid4().hex * 2,
                estado="pendiente",
                detalle_validacion={},
            )
            db.add(fila)
            db.flush()
            identificador = fila.id
            db.commit()
        entregas.append(identificador)
        return SimpleNamespace(id=identificador, letra=letra)

    def ficha() -> SimpleNamespace:
        """Ficha abierta para comprobar el modo lectura de QA/QC."""
        with SessionLocal() as db:
            fila = FichaQAQC(
                proyecto_id=proyecto_id,
                punto_id=900000 + uuid.uuid4().int % 99999,
                anio=2024,
                estado="Detectado",
                valores_iniciales={"A": 1, "B": 2, "C": 3},
                evidencias=[],
                sensores=[],
                firmas={},
            )
            db.add(fila)
            db.flush()
            identificador = fila.id
            db.commit()
        fichas.append(identificador)
        return SimpleNamespace(id=identificador)

    yield SimpleNamespace(usuario=usuario, entrega=entrega, ficha=ficha)

    with SessionLocal() as db:
        for identificador in fichas:
            db.execute(delete(FichaQAQC).where(FichaQAQC.id == identificador))
        for identificador in entregas:
            db.execute(delete(Entrega).where(Entrega.id == identificador))
        actores = [f"usuario:{identificador}" for identificador in usuarios]
        for identificador in usuarios:
            db.execute(delete(Sesion).where(Sesion.usuario_id == identificador))
            db.execute(delete(Usuario).where(Usuario.id == identificador))
        if actores:
            db.execute(delete(Auditoria).where(
                Auditoria.id > corte_auditoria,
                Auditoria.actor.in_(actores + ["sistema"])))
        db.execute(delete(LoginAttempt).where(LoginAttempt.id > corte_intentos))
        db.commit()


# --- Sesión --------------------------------------------------------------------
def _csrf_anonimo(cliente: TestClient) -> str:
    """Token CSRF de la cookie tras abrir el formulario de login."""
    respuesta = cliente.get("/auth/login")
    assert respuesta.status_code == 200
    token = cliente.cookies.get(COOKIE_CSRF)
    assert token
    return token


def _csrf_de_sesion(cliente: TestClient) -> str:
    """Token del formulario rendereado con la sesión activa (logout del shell)."""
    html = cliente.get("/").text
    encontrado = PATRON_CSRF.search(html)
    assert encontrado, "la página autenticada debe incluir el campo CSRF"
    return encontrado.group(1)


def _entrar(cliente: TestClient, usuario: SimpleNamespace) -> None:
    """Inicia sesión limpiando las cookies de la prueba anterior."""
    cliente.cookies.clear()
    respuesta = cliente.post(
        "/auth/login",
        data={"login": usuario.username, "password": usuario.password,
              "csrf_token": _csrf_anonimo(cliente)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303, respuesta.text
    assert cliente.cookies.get(COOKIE_SESION)


@contextlib.contextmanager
def _sesion(cliente: TestClient, usuario: SimpleNamespace):
    _entrar(cliente, usuario)
    yield cliente


# --- Asistente -----------------------------------------------------------------
def test_asistente_solo_ve_sus_entregas_y_no_entra_al_analisis(cliente, auth_activa,
                                                               roles_db):
    asistente = roles_db.usuario("asistente", letra="A")
    propia = roles_db.entrega("A")
    ajena = roles_db.entrega("B")

    with _sesion(cliente, asistente):
        portada = cliente.get("/")
        assert portada.status_code == 200
        # Sin resultados consolidados ni estado del flujo.
        assert "Concordancia de la terna" not in portada.text
        assert "Estado del flujo" not in portada.text
        assert "resultados consolidados" in portada.text

        listado = cliente.get("/entregas")
        assert listado.status_code == 200
        assert f"/entregas/{propia.id}/preview" in listado.text
        assert f"/entregas/{ajena.id}/preview" not in listado.text
        # Sin bloque de carga: su rol no gestiona entregas.
        assert 'action="/entregas/upload"' not in listado.text

        assert cliente.get(f"/entregas/{ajena.id}/preview").status_code == 403

        for ruta in LECTURA_ANALISIS:
            respuesta = cliente.get(ruta)
            assert respuesta.status_code == 403, ruta
            assert "text/html" in respuesta.headers["content-type"]


def test_asistente_sin_letra_no_ve_ninguna_entrega(cliente, auth_activa, roles_db):
    asistente = roles_db.usuario("asistente", letra=None)
    ajena = roles_db.entrega("A")

    with _sesion(cliente, asistente):
        listado = cliente.get("/entregas")
        assert listado.status_code == 200
        assert f"/entregas/{ajena.id}/preview" not in listado.text
        assert cliente.get(f"/entregas/{ajena.id}/preview").status_code == 403


def test_la_navegacion_del_asistente_no_muestra_secciones_vedadas(cliente,
                                                                  auth_activa,
                                                                  roles_db):
    asistente = roles_db.usuario("asistente", letra="C")

    with _sesion(cliente, asistente):
        html = cliente.get("/").text
        assert 'href="/entregas"' in html
        assert 'href="/cruces"' not in html
        assert 'href="/qaqc"' not in html
        assert 'href="/reemplazos"' not in html
        assert 'href="/exportar"' not in html
        # El menú de usuario y el logout siguen disponibles.
        assert "Cambiar contraseña" in html
        assert "Salir" in html


# --- Carga ---------------------------------------------------------------------
def test_cargador_lee_analisis_pero_no_dictamina_ni_exporta(cliente, auth_activa,
                                                            roles_db):
    cargador = roles_db.usuario("cargador")

    with _sesion(cliente, cargador):
        assert cliente.get("/cruces").status_code == 200
        assert cliente.get("/qaqc").status_code == 200

        token = _csrf_de_sesion(cliente)

        # 403 de autorización, no de CSRF ni de validación.
        evento = cliente.post("/qaqc/1/evento",
                              data={"csrf_token": token, "evento": "Iniciar revisión"},
                              follow_redirects=False)
        assert evento.status_code == 403

        # Tiene `entregas.gestionar`: la falta de archivo es 422, no 403.
        subida = cliente.post("/entregas/upload", data={"csrf_token": token},
                              follow_redirects=False)
        assert subida.status_code == 422

        assert cliente.get("/exportar").status_code == 403
        assert cliente.get("/exportar/consolidado.xlsx").status_code == 403

        # Puede cargar malla, mapa y entregas: los formularios siguen visibles.
        assert 'action="/malla/upload"' in cliente.get("/malla").text
        assert 'action="/mapa/upload"' in cliente.get("/mapa").text
        assert 'action="/entregas/upload"' in cliente.get("/entregas").text


def test_cargador_lee_la_ficha_sin_formularios_de_dictamen(cliente, auth_activa,
                                                           roles_db):
    cargador = roles_db.usuario("cargador")
    ficha = roles_db.ficha()

    with _sesion(cliente, cargador):
        pagina = cliente.get(f"/qaqc/{ficha.id}")
        assert pagina.status_code == 200
        assert "modo lectura" in pagina.text
        # Sin formularios POST de la ficha: el único que queda es el logout
        # del shell (dibujado dos veces: drawer y topbar).
        assert 'action="/qaqc/' not in pagina.text
        assert pagina.text.count('action="/auth/logout"') == 2


def test_supervision_ve_el_formulario_de_dictamen(cliente, auth_activa, roles_db):
    supervision = roles_db.usuario("supervision")
    ficha = roles_db.ficha()

    with _sesion(cliente, supervision):
        pagina = cliente.get(f"/qaqc/{ficha.id}")
        assert pagina.status_code == 200
        assert "modo lectura" not in pagina.text
        assert f'action="/qaqc/{ficha.id}"' in pagina.text
        assert f'action="/qaqc/{ficha.id}/evento"' in pagina.text


def test_consulta_no_ve_formularios_de_carga(cliente, auth_activa, roles_db):
    consulta = roles_db.usuario("consulta")

    with _sesion(cliente, consulta):
        assert 'action="/malla/upload"' not in cliente.get("/malla").text
        assert 'action="/mapa/upload"' not in cliente.get("/mapa").text
        assert 'action="/entregas/upload"' not in cliente.get("/entregas").text
        assert 'action="/reemplazos"' not in cliente.get("/reemplazos").text


# --- Supervisión ---------------------------------------------------------------
def test_supervision_lee_todo_y_dictamina(cliente, auth_activa, roles_db):
    supervision = roles_db.usuario("supervision")

    with _sesion(cliente, supervision):
        for ruta in LECTURA_BASE + LECTURA_ANALISIS:
            assert cliente.get(ruta).status_code == 200, ruta

        token = _csrf_de_sesion(cliente)
        # Un evento inválido no es un problema de permisos: la ficha responde
        # con su propio flujo (error de transición), nunca con 403.
        respuesta = cliente.post(
            "/qaqc/999999/evento",
            data={"csrf_token": token, "evento": "Evento inexistente"},
            follow_redirects=False)
        assert respuesta.status_code in (303, 400)
        assert respuesta.status_code != 403


# --- Consulta ------------------------------------------------------------------
def test_consulta_lee_todo_y_no_modifica(cliente, auth_activa, roles_db):
    consulta = roles_db.usuario("consulta")

    with _sesion(cliente, consulta):
        for ruta in LECTURA_BASE + LECTURA_ANALISIS:
            assert cliente.get(ruta).status_code == 200, ruta
        assert cliente.get("/exportar").status_code == 200

        token = _csrf_de_sesion(cliente)
        respuesta = cliente.post("/reemplazos",
                                 data={"csrf_token": token, "punto_origen": 1},
                                 follow_redirects=False)
        assert respuesta.status_code == 403


# --- Administración ------------------------------------------------------------
def test_admin_no_dictamina_ni_autoriza_reemplazos(cliente, auth_activa, roles_db):
    admin = roles_db.usuario("admin")

    with _sesion(cliente, admin):
        assert cliente.get("/").status_code == 200

        token = _csrf_de_sesion(cliente)

        evento = cliente.post("/qaqc/1/evento",
                              data={"csrf_token": token, "evento": "Iniciar revisión"},
                              follow_redirects=False)
        assert evento.status_code == 403

        autorizar = cliente.post("/reemplazos/1/estado",
                                 data={"csrf_token": token,
                                       "nuevo_estado": "autorizado"},
                                 follow_redirects=False)
        assert autorizar.status_code == 403


# --- Auditoría y contrato de la página 403 -------------------------------------
def test_el_403_es_html_salvo_que_se_pida_json(cliente, auth_activa, roles_db):
    asistente = roles_db.usuario("asistente", letra="B")

    with _sesion(cliente, asistente):
        html_403 = cliente.get("/cruces")
        assert html_403.status_code == 403
        assert "text/html" in html_403.headers["content-type"]
        assert "Acceso restringido" in html_403.text

        json_403 = cliente.get("/cruces", headers={"Accept": "application/json"})
        assert json_403.status_code == 403
        assert "application/json" in json_403.headers["content-type"]
        assert json_403.json()["detail"] == "No tiene permiso para esta acción."


def test_el_acceso_denegado_se_audita_sin_datos_sensibles(cliente, auth_activa,
                                                          roles_db):
    from app.core.database import SessionLocal
    from app.models import Auditoria

    consulta = roles_db.usuario("consulta")

    with _sesion(cliente, consulta):
        token = _csrf_de_sesion(cliente)
        respuesta = cliente.post("/reemplazos",
                                 data={"csrf_token": token, "punto_origen": 1},
                                 follow_redirects=False)
        assert respuesta.status_code == 403

    with SessionLocal() as db:
        fila = db.scalars(
            select(Auditoria)
            .where(Auditoria.actor == f"usuario:{consulta.id}",
                   Auditoria.accion == "acceso_denegado")
            .order_by(Auditoria.id.desc())).first()

    assert fila is not None, "el 403 de autorización debe quedar auditado"
    assert fila.detalle["ruta"] == "/reemplazos"
    assert fila.detalle["metodo"] == "POST"
    assert fila.detalle["rol"] == "consulta"
    assert fila.ip  # la IP del cliente de pruebas

    serializado = json.dumps(fila.detalle, ensure_ascii=False, default=str)
    for prohibido in ("csrf", "password", "contraseña", "token", "secret"):
        assert prohibido not in serializado.casefold()
    assert token not in serializado


# --- Compatibilidad con la bandera apagada -------------------------------------
def test_bandera_apagada_deja_pasar_todo_sin_sesion(cliente):
    from app.core.config import settings

    assert settings.auth_enabled is False
    cliente.cookies.clear()
    for ruta in ("/", "/cruces", "/qaqc", "/entregas", "/exportar"):
        assert cliente.get(ruta).status_code == 200, ruta
