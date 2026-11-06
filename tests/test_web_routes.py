"""Pruebas de integración de las rutas web (requieren PostgreSQL).

Levantan la aplicación completa con ``TestClient``, de modo que el arranque
(``init_db`` + ``seed``), las sesiones de base de datos y el renderizado de las
plantillas se ejercitan de punta a punta. Se excluyen de la corrida por
defecto mediante el marker ``integration``:

    uv run pytest -m integration -q
"""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

RUTAS_HTML = [
    "/",
    "/entregas",
    "/cruces",
    "/qaqc",
    "/reemplazos",
    "/malla",
    "/mapa",
    "/exportar",
]


@pytest.fixture(scope="module")
def client(datos_web):
    """Cliente de pruebas con datos propios y ciclo de vida de la app."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("ruta", RUTAS_HTML)
def test_rutas_devuelven_html(client, ruta):
    respuesta = client.get(ruta)
    assert respuesta.status_code == 200, ruta
    assert "text/html" in respuesta.headers["content-type"], ruta
    assert "<!DOCTYPE html>" in respuesta.text


def test_salud_devuelve_json(client):
    respuesta = client.get("/salud")
    assert respuesta.status_code == 200
    assert "application/json" in respuesta.headers["content-type"]
    assert respuesta.json() == {"estado": "ok"}


def test_health_devuelve_json(client):
    """Alias público para monitores que usan la convención /health."""
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    assert "application/json" in respuesta.headers["content-type"]
    assert respuesta.json() == {"estado": "ok"}


def test_ready_comprueba_la_base(client):
    respuesta = client.get("/ready")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "listo", "base": "ok"}


def test_ready_devuelve_503_si_la_base_no_responde(client, monkeypatch):
    import app.main as main

    class MotorCaido:
        def connect(self):
            raise RuntimeError("sin base")

    monkeypatch.setattr(main, "engine", MotorCaido())

    respuesta = client.get("/ready")
    assert respuesta.status_code == 503
    assert respuesta.json() == {"estado": "no_listo", "base": "error"}
    assert "sin base" not in respuesta.text


def test_las_respuestas_llevan_request_id(client):
    respuesta = client.get("/salud", headers={"X-Request-ID": "prueba-123"})
    assert respuesta.headers["x-request-id"] == "prueba-123"

    generado = client.get("/salud").headers["x-request-id"]
    assert generado
    assert len(generado) == 32


def test_validacion_html_devuelve_html(client):
    respuesta = client.get("/cruces", params={"page": "no-es-numero"})
    assert respuesta.status_code == 422
    assert "text/html" in respuesta.headers["content-type"]
    assert "<!DOCTYPE html>" in respuesta.text
    assert "no son válidos" in respuesta.text


def test_validacion_json_devuelve_json(client):
    respuesta = client.get(
        "/cruces", params={"page": "no-es-numero"},
        headers={"Accept": "application/json"})
    assert respuesta.status_code == 422
    assert "application/json" in respuesta.headers["content-type"]
    assert isinstance(respuesta.json()["detail"], list)


def test_pagina_fuera_de_rango_se_ajusta(client):
    respuesta = client.get(
        "/cruces", params={"page": 9999},
        headers={"Accept": "application/json"})
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["page"] == datos["pages"]


def test_exportacion_xlsx_sanea_formulas(client):
    from io import BytesIO

    from openpyxl import load_workbook
    from sqlalchemy import delete

    from app.core.database import SessionLocal
    from app.models import PuntoMalla, ResultadoCruce
    from app.services import persist

    punto = 987654
    with SessionLocal() as db:
        proyecto = persist.get_default_proyecto(db)
        db.execute(delete(ResultadoCruce).where(
            ResultadoCruce.proyecto_id == proyecto.id,
            ResultadoCruce.punto_id == punto))
        db.execute(delete(PuntoMalla).where(
            PuntoMalla.proyecto_id == proyecto.id,
            PuntoMalla.punto_id == punto))
        db.add(PuntoMalla(
            proyecto_id=proyecto.id, punto_id=punto, estrato="=1+1",
            es_reserva=False, activo=True))
        db.add(ResultadoCruce(
            proyecto_id=proyecto.id, punto_id=punto, anio=2024,
            a=1, b=1, c=1, resultado="unanimidad", clase_consolidada=1,
            regla_version="test"))
        db.commit()
    try:
        respuesta = client.get("/exportar/consolidado.xlsx")
        assert respuesta.status_code == 200
        libro = load_workbook(BytesIO(respuesta.content), data_only=False)
        try:
            hoja = libro["Consolidado"]
            fila = next(f for f in hoja.iter_rows(min_row=2)
                        if f[0].value == punto)
            assert fila[2].value == "'=1+1"
        finally:
            libro.close()
    finally:
        with SessionLocal() as db:
            proyecto = persist.get_default_proyecto(db)
            db.execute(delete(ResultadoCruce).where(
                ResultadoCruce.proyecto_id == proyecto.id,
                ResultadoCruce.punto_id == punto))
            db.execute(delete(PuntoMalla).where(
                PuntoMalla.proyecto_id == proyecto.id,
                PuntoMalla.punto_id == punto))
            db.commit()


def test_cruce_inexistente_es_404_html(client):
    respuesta = client.get("/cruces/999999/2000")
    assert respuesta.status_code == 404
    assert "text/html" in respuesta.headers["content-type"]
    assert "application/json" not in respuesta.headers["content-type"]
    assert "Página no encontrada" in respuesta.text


def test_return_to_externo_se_ignora(client):
    respuesta = client.get("/cruces/81/2008",
                           params={"return_to": "https://evil.com"})
    assert respuesta.status_code == 200
    assert "evil.com" not in respuesta.text
    # El enlace de vuelta cae al destino interno por defecto.
    assert 'href="/cruces"' in respuesta.text


def test_cruces_pagina_2_muestra_contador(client):
    respuesta = client.get("/cruces", params={"page": 2})
    assert respuesta.status_code == 200
    assert "Mostrando" in respuesta.text
    assert "Página 2 de" in respuesta.text


def test_cruces_con_accept_json_conserva_el_contrato(client):
    respuesta = client.get("/cruces", headers={"Accept": "application/json"})
    assert respuesta.status_code == 200
    assert "application/json" in respuesta.headers["content-type"]
    datos = respuesta.json()
    assert datos["total"] >= 1
    assert datos["page"] == 1
    assert isinstance(datos["cruces"], list) and datos["cruces"]
    primero = datos["cruces"][0]
    assert {"punto", "anio", "resultado"} <= set(primero)


def test_detalle_de_cruce_conserva_json_en_el_error(client):
    """El handler global sigue devolviendo JSON a los clientes de máquina."""
    respuesta = client.get("/cruces/999999/2000",
                           headers={"Accept": "application/json"})
    assert respuesta.status_code == 404
    assert "application/json" in respuesta.headers["content-type"]
    assert "detail" in respuesta.json()


# --- Paginación de /qaqc --------------------------------------------------
# La lista de fichas se pagina en servidor (25/50/100 por página) para no
# enviar cientos de filas en un solo documento. Estas pruebas fijan el
# contador "Mostrando X–Y de N" y que la segunda página y el tamaño reducido
# respondan HTML sin errores.


def test_qaqc_pagina_2_muestra_el_siguiente_bloque(client):
    respuesta = client.get("/qaqc", params={"page": 2})
    assert respuesta.status_code == 200
    assert "text/html" in respuesta.headers["content-type"]
    assert "Mostrando" in respuesta.text
    assert "Página 2 de" in respuesta.text


def test_qaqc_filtro_por_estado_conserva_la_paginacion(client):
    respuesta = client.get("/qaqc", params={"estado": "Detectado", "page": 2})
    assert respuesta.status_code == 200
    assert "Mostrando" in respuesta.text
    assert "filtro: Detectado" in respuesta.text


def test_qaqc_per_page_25_reduce_el_bloque(client):
    respuesta = client.get("/qaqc", params={"per_page": 25})
    assert respuesta.status_code == 200
    assert "Mostrando 1–25 de" in respuesta.text
    # El selector sin JavaScript conserva el valor elegido en el HTML.
    assert 'value="25" selected' in respuesta.text


def test_qaqc_pagina_fuera_de_rango_se_acota(client):
    """Una página inexistente no rompe: se acota a la última disponible."""
    respuesta = client.get("/qaqc", params={"page": 999})
    assert respuesta.status_code == 200
    assert "Mostrando" in respuesta.text

