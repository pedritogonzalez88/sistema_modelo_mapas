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
def client():
    """Cliente de pruebas con el ciclo de vida de la app (startup/shutdown)."""
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
