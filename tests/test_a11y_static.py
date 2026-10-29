"""Auditoría estática de accesibilidad sobre el HTML renderizado.

No sustituye la revisión manual (teclado, zoom, contraste, lectores de
pantalla), pero congela los criterios WCAG 2.2 AA que el rediseño puede
romper sin que se note: idioma, viewport, jerarquía de encabezados, skip
link, etiquetas de formulario, caption de tablas y tipo de los botones.

Usa ``TestClient`` (necesita PostgreSQL), por eso se marca como integración:

    uv run pytest -m integration -q
"""
import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

# Rutas de la navegación principal + las dos vistas de detalle con formularios.
RUTAS = [
    "/",
    "/entregas",
    "/cruces",
    "/qaqc",
    "/reemplazos",
    "/malla",
    "/mapa",
    "/exportar",
    "/qaqc/1",
    "/cruces/81/2008",
    "/entregas/1/preview",
]

# Tipos de input que no llevan etiqueta visible ni id obligatorio.
SIN_ETIQUETA = {"hidden", "submit", "button", "reset", "image"}
# Tipos que se conforman con una etiqueta envolvente o `aria-label`.
CON_ETIQUETA_ENVOLVENTE = {"checkbox", "radio"}


class Documento(HTMLParser):
    """Recolecta solo lo que necesita la auditoría."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.viewport: str | None = None
        self.h1 = 0
        self.ids: set[str] = set()
        self.labels_for: set[str] = set()
        self.inputs: list[dict] = []
        self.botones: list[dict] = []
        self.tablas = 0
        self.captions = 0
        self.enlaces: list[str] = []
        self.main_contenido = False
        self._labels: list[dict] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        datos = {k: (v if v is not None else "") for k, v in attrs}
        if "id" in datos:
            self.ids.add(datos["id"])

        if tag == "html":
            self.lang = datos.get("lang")
        elif tag == "meta" and datos.get("name") == "viewport":
            self.viewport = datos.get("content", "")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "main" and datos.get("id") == "contenido":
            self.main_contenido = True
        elif tag == "a":
            self.enlaces.append(datos.get("href", ""))
        elif tag == "label":
            self._labels.append({"for": datos.get("for")})
            if datos.get("for"):
                self.labels_for.add(datos["for"])
        elif tag == "table":
            self.tablas += 1
        elif tag == "caption":
            self.captions += 1
        elif tag == "button":
            self.botones.append(datos)
        elif tag == "input":
            self.inputs.append({
                **datos,
                "envolvente": bool(self._labels),
            })

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._labels:
            self._labels.pop()


def _parsear(html: str) -> Documento:
    documento = Documento()
    documento.feed(html)
    documento.close()
    return documento


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def documentos(client):
    """Renderiza cada ruta una sola vez para toda la sesión de pruebas."""
    resultado = {}
    for ruta in RUTAS:
        respuesta = client.get(ruta)
        assert respuesta.status_code == 200, f"{ruta} -> {respuesta.status_code}"
        resultado[ruta] = _parsear(respuesta.text)
    return resultado


@pytest.mark.parametrize("ruta", RUTAS)
def test_documento_tiene_idioma_y_viewport(documentos, ruta):
    documento = documentos[ruta]
    assert documento.lang == "es", f"{ruta}: falta lang=es"
    assert documento.viewport, f"{ruta}: falta meta viewport"
    assert "width=device-width" in documento.viewport
    assert "user-scalable=no" not in documento.viewport


@pytest.mark.parametrize("ruta", RUTAS)
def test_un_solo_h1(documentos, ruta):
    assert documentos[ruta].h1 == 1, f"{ruta}: {documentos[ruta].h1} elementos h1"


@pytest.mark.parametrize("ruta", RUTAS)
def test_skip_link_y_destino(documentos, ruta):
    documento = documentos[ruta]
    assert "#contenido" in documento.enlaces, f"{ruta}: falta el skip link"
    assert documento.main_contenido, f"{ruta}: falta <main id='contenido'>"


@pytest.mark.parametrize("ruta", RUTAS)
def test_inputs_visibles_tienen_id_y_label(documentos, ruta):
    documento = documentos[ruta]
    problemas = []
    for campo in documento.inputs:
        tipo = (campo.get("type") or "text").lower()
        if tipo in SIN_ETIQUETA:
            continue
        if tipo in CON_ETIQUETA_ENVOLVENTE:
            if campo["envolvente"] or campo.get("id") in documento.labels_for \
                    or campo.get("aria-label"):
                continue
            problemas.append(f"{tipo} sin etiqueta (name={campo.get('name')})")
            continue
        identificador = campo.get("id")
        if not identificador:
            problemas.append(f"input {tipo} sin id (name={campo.get('name')})")
        elif identificador not in documento.labels_for:
            problemas.append(
                f"input {tipo} id={identificador} sin <label for>")
    assert not problemas, f"{ruta}: " + "; ".join(problemas)


@pytest.mark.parametrize("ruta", RUTAS)
def test_labels_apuntan_a_ids_existentes(documentos, ruta):
    documento = documentos[ruta]
    huerfanos = sorted(documento.labels_for - documento.ids)
    assert not huerfanos, f"{ruta}: label for sin control: {huerfanos}"


@pytest.mark.parametrize("ruta", RUTAS)
def test_tablas_tienen_caption(documentos, ruta):
    documento = documentos[ruta]
    assert documento.captions >= documento.tablas, (
        f"{ruta}: {documento.tablas} tablas y {documento.captions} captions")


@pytest.mark.parametrize("ruta", RUTAS)
def test_botones_declaran_type(documentos, ruta):
    documento = documentos[ruta]
    sin_type = [b for b in documento.botones if "type" not in b]
    assert not sin_type, f"{ruta}: botones sin type: {len(sin_type)}"


@pytest.mark.parametrize("ruta", RUTAS)
def test_sin_enlaces_placeholder(documentos, ruta):
    documento = documentos[ruta]
    invalidos = [h for h in documento.enlaces
                 if h.strip() == "#" or h.strip().lower().startswith("javascript:")]
    assert not invalidos, f"{ruta}: enlaces placeholder {invalidos}"


def test_el_skip_link_es_el_primer_enlace(documentos):
    """El atajo debe ser lo primero que recibe el foco por teclado."""
    html = documentos["/"]
    assert html.enlaces[0] == "#contenido"


def test_las_vistas_listadas_usan_page_header(client):
    """Cada vista declara su h1 mediante la macro page_header."""
    for ruta in RUTAS:
        if ruta in ("/cruces/81/2008", "/entregas/1/preview", "/qaqc/1"):
            continue
        html = client.get(ruta).text
        assert "page-header__title" in html, f"{ruta} no usa page_header"
        assert re.search(r"<h1[^>]*class=\"[^\"]*page-header__title", html), ruta
