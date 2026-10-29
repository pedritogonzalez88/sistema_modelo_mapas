"""Pruebas del feedback por códigos de estado (flash) — sin base de datos.

Regla de seguridad: el texto que se muestra SIEMPRE sale del diccionario del
backend; nunca se refleja el valor crudo de la URL.
"""
import pytest

from app.web import FLASH_ERROR, FLASH_OK, _flash


class RequestFalsa:
    """Simula request.query_params (objeto con .get)."""

    def __init__(self, params: dict | None = None) -> None:
        self.query_params = params or {}


@pytest.mark.parametrize("codigo", sorted(FLASH_OK))
def test_codigo_ok_devuelve_texto_y_tono(codigo):
    texto, tono = FLASH_OK[codigo]
    assert _flash(RequestFalsa({"ok": codigo})) == {"text": texto, "tone": tono}


@pytest.mark.parametrize("codigo", sorted(FLASH_ERROR))
def test_codigo_error_devuelve_tono_danger(codigo):
    assert _flash(RequestFalsa({"error": codigo})) == {
        "text": FLASH_ERROR[codigo],
        "tone": "danger",
    }


def test_codigo_desconocido_no_devuelve_nada():
    assert _flash(RequestFalsa({"ok": "inventado"})) is None
    assert _flash(RequestFalsa({"error": "inventado"})) is None


def test_sin_parametros_no_devuelve_nada():
    assert _flash(RequestFalsa()) is None


def test_no_se_refleja_texto_arbitrario_de_la_url():
    payload = '<script>alert("xss")</script>'
    assert _flash(RequestFalsa({"ok": payload})) is None
    assert _flash(RequestFalsa({"error": payload})) is None


@pytest.mark.parametrize("textos", [FLASH_OK, FLASH_ERROR])
def test_los_textos_no_contienen_html(textos):
    for valor in textos.values():
        texto = valor[0] if isinstance(valor, tuple) else valor
        assert texto.strip(), "todo código debe tener texto"
        assert "<" not in texto and ">" not in texto
        assert "&" not in texto


def test_todos_los_tonos_son_conocidos():
    tonos = {tono for _, tono in FLASH_OK.values()} | {"danger"}
    assert tonos <= {"ok", "warn", "danger", "info", "neutral"}
