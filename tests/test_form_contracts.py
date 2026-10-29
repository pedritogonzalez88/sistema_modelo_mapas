"""Contrato de los formularios: los `name` que espera el backend no cambian.

El rediseño de las plantillas puede alterar el marcado, pero un `name`
renombrado o eliminado rompe un POST sin que ninguna prueba de componente lo
detecte. Estas pruebas leen las plantillas con Jinja2 (sin base de datos) y
comparan el conjunto de campos contra el contrato que consumen los routers.

Se valida por inclusión: los campos ocultos de control (``*_present``,
``nuevo_estado``) y los filtros de listado son extras aceptados.
"""
from pathlib import Path
import re

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

RAIZ = Path(__file__).resolve().parents[1]
PLANTILLAS = RAIZ / "app" / "templates"

env = Environment(
    loader=FileSystemLoader(str(PLANTILLAS)),
    autoescape=select_autoescape(["html"]),
)


@pytest.fixture(scope="module")
def campos():
    """Devuelve una función que extrae los `name` de una plantilla."""

    def _campos(nombre_archivo: str) -> set[str]:
        codigo = env.loader.get_source(env, nombre_archivo)[0]
        # Los controles escritos a mano declaran `name="..."`; los que genera la
        # macro `ui.field` lo reciben como segundo argumento posicional. Se leen
        # ambos para que el contrato cubra todo el formulario sin renderizarlo.
        literales = set(re.findall(r'name="([A-Za-z0-9_\-\[\]\.]+)"', codigo))
        generados = set(re.findall(r'ui\.field\(\s*"[^"]*"\s*,\s*"([A-Za-z0-9_\-\[\]\.]+)"', codigo))
        return literales | generados

    return _campos


# --- Ficha QA/QC: el formulario más crítico (protocolo 4.12.1) --------------
CAMPOS_QAQC = {
    "debate",
    "dictamen",
    "clase_definitiva",
    "sensores",
    "causa_descarte",
    "fecha_sesion",
    "firma_a",
    "firma_b",
    "firma_c",
    "evidencias",
    "evento",
}
EXTRAS_QAQC = {"sensores_present", "evidencias_present"}


def test_qaqc_detalle_conserva_los_campos_del_post(campos):
    nombres = campos("qaqc_detalle.html")
    assert CAMPOS_QAQC <= nombres, f"faltan: {sorted(CAMPOS_QAQC - nombres)}"
    assert nombres - CAMPOS_QAQC <= EXTRAS_QAQC, (
        f"campos no previstos: {sorted(nombres - CAMPOS_QAQC - EXTRAS_QAQC)}"
    )


def test_qaqc_detalle_conserva_los_marcadores_de_vaciado(campos):
    """Sin estos ocultos el backend no distingue "no enviado" de "ninguno"."""
    nombres = campos("qaqc_detalle.html")
    assert "sensores_present" in nombres
    assert "evidencias_present" in nombres


# --- Entregas ---------------------------------------------------------------
CAMPOS_ENTREGAS = {"file", "asistente_letra", "semana", "tipo"}
EXTRAS_ENTREGAS = {"asistente", "estado", "motivo"}


def test_entregas_conserva_los_campos_de_carga(campos):
    nombres = campos("entregas.html")
    assert CAMPOS_ENTREGAS <= nombres, (
        f"faltan: {sorted(CAMPOS_ENTREGAS - nombres)}"
    )
    assert nombres - CAMPOS_ENTREGAS <= EXTRAS_ENTREGAS, (
        f"campos no previstos: {sorted(nombres - CAMPOS_ENTREGAS - EXTRAS_ENTREGAS)}"
    )


def test_vista_previa_conserva_el_motivo_del_rechazo(campos):
    """`motivo` vive en la vista previa, no en el listado de entregas."""
    assert "motivo" in campos("entrega_preview.html")


# --- Cargas de archivo -------------------------------------------------------
@pytest.mark.parametrize("plantilla", ["malla.html", "mapa.html"])
def test_cargas_de_archivo_usan_el_campo_file(campos, plantilla):
    nombres = campos(plantilla)
    assert "file" in nombres
    assert nombres <= {"file", "q", "per_page"}, (
        f"campos no previstos en {plantilla}: {sorted(nombres)}"
    )
