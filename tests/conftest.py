"""Fixtures compartidas para la batería de pruebas de Evalua5."""
import uuid
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import delete


def _crear_libro(hojas):
    """Construye un libro .xlsx en memoria y devuelve su contenido en bytes.

    Cada hoja se especifica como un diccionario con las claves
    ``titulo``, ``encabezado`` y ``filas``.
    """
    libro = Workbook()
    libro.remove(libro.active)
    for hoja in hojas:
        ws = libro.create_sheet(title=hoja["titulo"])
        ws.append(hoja["encabezado"])
        for fila in hoja.get("filas", []):
            ws.append(fila)
    buffer = BytesIO()
    libro.save(buffer)
    libro.close()
    return buffer.getvalue()


@pytest.fixture
def crear_libro():
    """Factory que arma libros xlsx en memoria para el importador."""
    return _crear_libro


# --- Autenticación (Fase A1) ---------------------------------------------------
# Estas fixtures NO son autouse: las pruebas que no tocan autenticación siguen
# corriendo con la bandera apagada y el comportamiento anterior a la Fase A1.


@pytest.fixture
def auth_activa(monkeypatch):
    """Activa la bandera de autenticación con cookies no seguras.

    `cookie_secure=False` obliga a usar ``eet_session`` (los prefijos
    ``__Host-`` exigen Secure y el cliente de pruebas navega por http).
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "secret_key", "clave-de-pruebas-a1-solo-tests")
    monkeypatch.setattr(settings, "auth_pepper", "pepper-de-pruebas-a1")
    monkeypatch.setattr(settings, "login_max_intentos", 5)
    monkeypatch.setattr(settings, "login_bloqueo_min", 15)
    return settings


@pytest.fixture
def fabrica_usuarios():
    """Crea cuentas locales de prueba y las elimina al terminar.

    Devuelve un ``SimpleNamespace`` con ``id``, ``username``, ``email``,
    ``password`` y ``rol``. El teardown borra sesiones, auditoría e intentos
    asociados: la base queda como estaba.
    """
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import Auditoria, LoginAttempt, Sesion, Usuario
    from sqlalchemy import func, select

    creados: list[int] = []

    with SessionLocal() as db:
        # Corte para borrar solo lo que generen estas pruebas (el login de un
        # usuario inexistente se audita con actor "sistema", sin id de usuario).
        corte = db.scalar(select(func.max(Auditoria.id))) or 0
        corte_intentos = db.scalar(select(func.max(LoginAttempt.id))) or 0

    def _crear(*, username: str | None = None, email: str | None = None,
               password: str = "Clave-Segura-2026", rol: str = "consulta",
               activo: bool = True, must_change: bool = False,
               mfa_policy: str = "disabled",
               ) -> SimpleNamespace:
        sufijo = uuid.uuid4().hex[:10]
        username = username or f"prueba-{sufijo}"
        email = email or f"{username}@ejemplo.test"
        with SessionLocal() as db:
            usuario = Usuario(
                nombre=f"Usuario {sufijo}",
                username=username,
                email=email,
                rol=rol,
                auth_provider="local",
                password_hash=hash_password(password),
                is_active=activo,
                must_change_password=must_change,
                # La fixture es genérica de login: sin segundo factor para no
                # desviar al challenge por correo (Fase A4). Los tests que
                # necesitan TOTP pasan ``mfa_policy="totp"`` explícitamente.
                mfa_policy=mfa_policy,
            )
            db.add(usuario)
            db.flush()
            identificador = usuario.id
            db.commit()
        creados.append(identificador)
        return SimpleNamespace(id=identificador, username=username, email=email,
                               password=password, rol=rol)

    yield _crear

    with SessionLocal() as db:
        actores = [f"usuario:{identificador}" for identificador in creados]
        for identificador in creados:
            db.execute(delete(Sesion).where(Sesion.usuario_id == identificador))
            db.execute(delete(Usuario).where(Usuario.id == identificador))
        if actores:
            db.execute(delete(Auditoria).where(
                Auditoria.id > corte, Auditoria.actor.in_(actores)))
        # Intentos de acceso a identificadores inexistentes: actor "sistema".
        db.execute(delete(Auditoria).where(
            Auditoria.id > corte, Auditoria.actor == "sistema"))
        db.execute(delete(LoginAttempt).where(LoginAttempt.id > corte_intentos))
        db.commit()


# --- Datos autocontenidos para las pruebas de rutas y accesibilidad ------------
@pytest.fixture(scope="session")
def _esquema_listo():
    """Garantiza tablas incluso si el primer uso ocurre antes del lifespan."""
    from app.core.database import init_db

    init_db()


@pytest.fixture(scope="session")
def datos_web(_esquema_listo):
    """Crea y limpia las filas que asumen `test_web_routes` y `test_a11y_static`.

    En vez de depender de una base de desarrollo ya poblada, este fixture
    asegura el cruce 81/2008, la ficha 1, la entrega 1 con un .xlsx válido y
    suficientes filas (>50) para las pruebas de paginación. Si las filas ya
    existen, conserva sus valores y restaura los campos que modifica.
    """
    from pathlib import Path

    from openpyxl import Workbook
    from sqlalchemy import delete, select, text

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import Entrega, FichaQAQC, ResultadoCruce
    from app.services import persist

    ruta = Path(settings.upload_path) / "test-web-preview.xlsx"
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Evaluador_A"
    hoja.append(["ID_EV", "ID_PUNTO", "A2008"])
    hoja.append(["A", 81, 1])
    libro.save(ruta)
    libro.close()

    extras_cruces: list[int] = []
    extras_fichas: list[int] = []
    cruce_creado: int | None = None
    entrega_previa: dict | None = None
    ficha_previa: dict | None = None

    with SessionLocal() as db:
        proyecto = persist.get_default_proyecto(db)
        pid = proyecto.id

        entrega = db.get(Entrega, 1)
        if entrega is None:
            db.add(Entrega(
                id=1, proyecto_id=pid, asistente_letra="A", semana="TEST",
                tipo="avance", version=1, archivo_path=str(ruta),
                sha256="f" * 64, estado="pendiente", detalle_validacion={},
            ))
        else:
            entrega_previa = {
                "archivo_path": entrega.archivo_path,
                "estado": entrega.estado,
            }
            entrega.archivo_path = str(ruta)
            entrega.estado = "pendiente"

        ficha = db.get(FichaQAQC, 1)
        if ficha is None:
            db.add(FichaQAQC(
                id=1, proyecto_id=pid, punto_id=81, anio=2008,
                estado="Detectado", valores_iniciales={"A": 1, "B": 2, "C": 3},
                evidencias=[], sensores=[], firmas={},
            ))
        else:
            ficha_previa = {
                "punto_id": ficha.punto_id,
                "anio": ficha.anio,
                "estado": ficha.estado,
            }
            ficha.punto_id = 81
            ficha.anio = 2008
            ficha.estado = "Detectado"

        cruce = db.scalar(select(ResultadoCruce).where(
            ResultadoCruce.proyecto_id == pid,
            ResultadoCruce.punto_id == 81,
            ResultadoCruce.anio == 2008,
        ))
        if cruce is None:
            fila = ResultadoCruce(
                proyecto_id=pid, punto_id=81, anio=2008,
                a=1, b=2, c=3, resultado="discrepancia",
                clase_consolidada=None, regla_version="tabla7-v1",
            )
            db.add(fila)
            db.flush()
            cruce_creado = fila.id

        for numero in range(60):
            punto = 800000 + numero
            existe = db.scalar(select(ResultadoCruce.id).where(
                ResultadoCruce.proyecto_id == pid,
                ResultadoCruce.punto_id == punto,
                ResultadoCruce.anio == 1990,
            ))
            if existe is not None:
                continue
            fila = ResultadoCruce(
                proyecto_id=pid, punto_id=punto, anio=1990,
                a=1, b=1, c=1, resultado="unanimidad",
                clase_consolidada=1, regla_version="tabla7-v1",
            )
            db.add(fila)
            db.flush()
            extras_cruces.append(fila.id)

        for numero in range(55):
            punto = 700000 + numero
            existe = db.scalar(select(FichaQAQC.id).where(
                FichaQAQC.proyecto_id == pid,
                FichaQAQC.punto_id == punto,
                FichaQAQC.anio == 1990,
            ))
            if existe is not None:
                continue
            fila = FichaQAQC(
                proyecto_id=pid, punto_id=punto, anio=1990,
                estado="Detectado",
                valores_iniciales={"A": 1, "B": 2, "C": 3},
                evidencias=[], sensores=[], firmas={},
            )
            db.add(fila)
            db.flush()
            extras_fichas.append(fila.id)

        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text(
                "SELECT setval('entregas_id_seq', "
                "GREATEST((SELECT COALESCE(MAX(id), 1) FROM entregas), 1))"))
            db.execute(text(
                "SELECT setval('fichas_qaqc_id_seq', "
                "GREATEST((SELECT COALESCE(MAX(id), 1) FROM fichas_qaqc), 1))"))
        db.commit()

    yield SimpleNamespace(entrega_id=1, ficha_id=1, punto=81, anio=2008)

    with SessionLocal() as db:
        if entrega_previa is None:
            db.execute(delete(Entrega).where(Entrega.id == 1))
        else:
            fila = db.get(Entrega, 1)
            if fila is not None:
                for campo, valor in entrega_previa.items():
                    setattr(fila, campo, valor)
        if ficha_previa is None:
            db.execute(delete(FichaQAQC).where(FichaQAQC.id == 1))
        else:
            fila = db.get(FichaQAQC, 1)
            if fila is not None:
                for campo, valor in ficha_previa.items():
                    setattr(fila, campo, valor)
        if cruce_creado is not None:
            db.execute(delete(ResultadoCruce).where(
                ResultadoCruce.id == cruce_creado))
        if extras_cruces:
            db.execute(delete(ResultadoCruce).where(
                ResultadoCruce.id.in_(extras_cruces)))
        if extras_fichas:
            db.execute(delete(FichaQAQC).where(
                FichaQAQC.id.in_(extras_fichas)))
        db.commit()
    ruta.unlink(missing_ok=True)

