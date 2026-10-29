"""Pruebas de contrato de los componentes Jinja del sistema de diseño.

No requieren base de datos: solo cargan las plantillas con un Jinja2.Environment
propio (sin la app de FastAPI) y verifican el HTML generado por cada macro.
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.datastructures import URL

RAIZ = Path(__file__).resolve().parents[1]

env = Environment(
    loader=FileSystemLoader(str(RAIZ / "app" / "templates")),
    autoescape=select_autoescape(["html"]),
)


class RequestFalsa:
    """Objeto mínimo con la superficie de request que usa `pagination`."""

    def __init__(self, url: str) -> None:
        self.url = URL(url)


def _ui():
    """Módulo de macros de components/ui.html (macros como callables)."""
    return env.get_template("components/ui.html").module


def _iconos():
    return env.get_template("components/icons.html").module


# --- icon -------------------------------------------------------------------
def test_icon_usa_sprite_local():
    html = _iconos().icon("home")
    assert '/static/icons.svg#i-home' in html
    assert 'aria-hidden="true"' in html


def test_icon_con_titulo_es_accesible():
    html = _iconos().icon("map", title="Mapa MADES")
    assert "<title>Mapa MADES</title>" in html
    assert 'aria-hidden="false"' in html


# --- badge ------------------------------------------------------------------
def test_badge_tone_danger():
    html = _ui().badge(text="Discrepancia", tone="danger")
    assert "badge--danger" in html
    assert "Discrepancia" in html


def test_badge_tono_por_defecto_neutral():
    assert "badge--neutral" in _ui().badge(text="Pendiente")


# --- chip_clase -------------------------------------------------------------
def test_chip_clase_1():
    html = _ui().chip_clase(codigo=1)
    assert "clase-1" in html
    assert "Bosque natural" in html


def test_chip_clase_9():
    html = _ui().chip_clase(codigo=9)
    assert "clase-9" in html
    assert "Otras tierras" in html


def test_chip_clase_sc():
    html = _ui().chip_clase(codigo="SC")
    assert "SC" in html
    assert "Sin consenso" in html


def test_chip_clase_acepta_el_nombre_de_la_clase():
    html = _ui().chip_clase(codigo="Pastizales")
    assert "clase-5" in html
    assert "Pastizales" in html


# --- btn --------------------------------------------------------------------
def test_btn_con_href_renderiza_enlace():
    html = _ui().btn(label="Nueva entrega", href="/entregas", icon="upload")
    assert html.strip().startswith("<a ")
    assert 'href="/entregas"' in html
    assert "btn--primary" in html


def test_btn_sin_href_renderiza_boton():
    html = _ui().btn(label="Guardar dictamen", variant="secondary", size="sm")
    assert html.strip().startswith("<button ")
    assert 'type="button"' in html
    assert "btn--secondary" in html
    assert "btn--sm" in html


def test_btn_conserva_el_valor_del_boton_enviado():
    html = _ui().btn(label="Cerrar por consenso", type="submit", name="evento", value="consenso")
    assert 'name="evento"' in html
    assert 'value="consenso"' in html


# --- card / stat / empty ----------------------------------------------------
def test_card_con_cabecera_y_cuerpo():
    plantilla = env.from_string(
        "{% import 'components/ui.html' as ui %}"
        "{% call ui.card('Terna', 'Punto 12') %}contenido{% endcall %}"
    )
    html = plantilla.render()
    assert "card__title" in html and "Terna" in html
    assert "card__subtitle" in html and "Punto 12" in html
    assert "contenido" in html


def test_stat_muestra_valor_y_hint():
    html = _ui().stat(label="Fichas abiertas", value=12, hint="3 con más de 7 días", tone="warn")
    assert "stat__label" in html and "stat__value" in html
    assert ">12<" in html
    assert "stat--warn" in html


def test_empty_incluye_llamada_a_la_accion():
    html = _ui().empty(title="Sin entregas", text="Cargue la primera.",
                       action_label="Subir entrega", action_href="/entregas")
    assert "empty__title" in html
    assert 'href="/entregas"' in html
    assert "Subir entrega" in html


# --- banner -----------------------------------------------------------------
def test_banner_danger_usa_role_alert():
    html = _ui().banner(tone="danger", text="El archivo no es válido.")
    assert 'role="alert"' in html
    assert "banner--danger" in html


def test_banner_ok_usa_role_status():
    html = _ui().banner(tone="ok", text="Dictamen guardado.")
    assert 'role="status"' in html
    assert "banner--ok" in html


# --- field ------------------------------------------------------------------
def test_field_con_error_marca_aria_invalid():
    html = _ui().field(label="Semana", name="semana", error="Formato inválido")
    assert 'for="campo-semana"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="campo-semana-error"' in html
    assert 'role="alert"' in html


def test_field_sin_error_no_marca_aria_invalid():
    html = _ui().field(label="Semana", name="semana", hint="Formato 2026-W41", required=True)
    assert "aria-invalid" not in html
    assert 'aria-describedby="campo-semana-hint"' in html
    assert "required" in html
    assert "campo-semana-hint" in html


def test_field_textarea_respeta_el_contenido():
    html = _ui().field(label="Fundamento", name="debate", type="textarea", value="Texto previo")
    assert "<textarea" in html
    assert "Texto previo" in html


# --- page_header ------------------------------------------------------------
def test_page_header_tiene_un_solo_h1_y_acciones():
    plantilla = env.from_string(
        "{% import 'components/ui.html' as ui %}"
        "{% call ui.page_header('Resumen', 'Estado del proyecto', [{'label':'Inicio','href':'/'},{'label':'Resumen'}]) %}"
        '<a href="/entregas">Nueva entrega</a>{% endcall %}'
    )
    html = plantilla.render()
    assert html.count("<h1") == 1
    assert "Resumen" in html
    assert 'href="/entregas"' in html
    assert 'aria-current="page"' in html


# --- table_wrap -------------------------------------------------------------
def test_table_wrap_es_region_enfocable():
    plantilla = env.from_string(
        "{% import 'components/ui.html' as ui %}"
        "{% call ui.table_wrap('Ternas por punto') %}<table class='table'><tr><td>1</td></tr></table>{% endcall %}"
    )
    html = plantilla.render()
    assert 'role="region"' in html
    assert 'tabindex="0"' in html
    assert 'aria-label="Ternas por punto"' in html


# --- pagination -------------------------------------------------------------
def test_pagination_conserva_los_filtros_de_la_url():
    request = RequestFalsa("http://test/cruces?resultado=discrepancia&page=2")
    html = _ui().pagination(page=2, pages=3, request=request)
    assert "resultado=discrepancia" in html
    assert "page=3" in html   # siguiente
    assert "page=1" in html   # anterior
    assert 'aria-current="page"' in html


def test_pagination_se_oculta_con_una_sola_pagina():
    request = RequestFalsa("http://test/cruces")
    assert _ui().pagination(page=1, pages=1, request=request).strip() == ""


# --- stepper / timeline / donut / progress ----------------------------------
def test_stepper_marca_el_paso_actual():
    html = _ui().stepper(pasos=["Archivo", "Validación", "Confirmar"], actual=2)
    assert 'aria-current="step"' in html
    assert "is-current" in html
    assert "is-done" in html


def test_timeline_renderiza_fechas_y_tonos():
    html = _ui().timeline(items=[{"fecha": "2026-10-01", "texto": "Ficha creada", "tono": "info"}])
    assert "2026-10-01" in html
    assert "Ficha creada" in html
    assert "timeline__item--info" in html


def test_donut_es_accesible_y_tiene_titulo():
    html = _ui().donut(segments=[
        {"label": "Unanimidad", "value": 10, "color": "var(--color-emerald-600)"},
        {"label": "Discrepancia", "value": 2, "color": "var(--color-red-600)"},
    ])
    assert 'role="img"' in html
    assert "<title>" in html
    assert "Unanimidad" in html
    assert "stroke-dasharray" in html


def test_donut_sin_datos_no_divide_por_cero():
    html = _ui().donut(segments=[{"label": "Sin datos", "value": 0}])
    assert "<title>" in html
    assert "Sin datos" in html


def test_progress_expone_valores_aria():
    html = _ui().progress(value=30, max=100, label="Avance del flujo")
    assert 'role="progressbar"' in html
    assert 'aria-valuenow="30"' in html
    assert 'aria-valuemax="100"' in html
    assert 'aria-label="Avance del flujo"' in html
