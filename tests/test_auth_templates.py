"""Barrido CSRF de plantillas (unitario, sin base de datos).

Regla: **todo** ``<form method="post">`` incluye el campo oculto
``csrf_token`` como primer control. Se acepta el marcador ``csrf_token`` o la
macro ``ui.csrf_input()`` (que renderiza
``<input type="hidden" name="csrf_token" ...>``).

Los formularios GET de filtros no llevan token a propósito: el token viajaría
en la URL (historial, logs, Referer) sin aportar protección, porque un GET no
cambia estado.
"""
import re
from pathlib import Path

PLANTILLAS = Path(__file__).resolve().parents[1] / "app" / "templates"
PATRON_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S | re.I)
PATRON_METODO = re.compile(r'method\s*=\s*"([^"]+)"', re.I)
MARCADORES_CSRF = ("csrf_token", "csrf_input()")

# 25 formularios de la aplicación + las pantallas de acceso (Fase A1/A3) y los
# de recuperación y restablecimiento (Fase A4). Total actual: 30.
MINIMO_FORMULARIOS = 30


def _formularios():
    """Genera ``(archivo, metodo, atributos, cuerpo)`` por cada formulario."""
    for ruta in sorted(PLANTILLAS.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        for atributos, cuerpo in PATRON_FORM.findall(texto):
            metodo = PATRON_METODO.search(atributos)
            yield (ruta.relative_to(PLANTILLAS).as_posix(),
                   (metodo.group(1).lower() if metodo else "get"),
                   atributos, cuerpo)


def test_todos_los_formularios_post_llevan_csrf():
    revisados = 0
    faltantes = []
    for archivo, metodo, _atributos, cuerpo in _formularios():
        if metodo != "post":
            continue
        revisados += 1
        if not any(marcador in cuerpo for marcador in MARCADORES_CSRF):
            faltantes.append(archivo)
    assert revisados >= 12, f"solo se revisaron {revisados} formularios POST"
    assert not faltantes, f"formularios POST sin CSRF: {faltantes}"


def test_cantidad_minima_de_formularios_en_la_aplicacion():
    total = sum(1 for _ in _formularios())
    assert total >= MINIMO_FORMULARIOS, (
        f"se esperaban al menos {MINIMO_FORMULARIOS} formularios y hay {total}")


def test_el_logout_del_shell_es_post_con_csrf():
    shell = PLANTILLAS / "components" / "shell.html"
    texto = shell.read_text(encoding="utf-8")
    encontrados = [cuerpo for atributos, cuerpo in PATRON_FORM.findall(texto)
                   if 'action="/auth/logout"' in atributos
                   and 'method="post"' in atributos]
    assert len(encontrados) == 1, "el shell debe tener un único formulario de salida"
    assert any(marcador in encontrados[0] for marcador in MARCADORES_CSRF)


def test_las_pantallas_de_acceso_incluyen_csrf():
    for nombre in ("auth/login.html", "auth/cambiar_password.html",
                   "auth/mfa.html", "auth/seguridad.html",
                   "auth/recuperar.html", "auth/restablecer.html"):
        texto = (PLANTILLAS / nombre).read_text(encoding="utf-8")
        formularios = PATRON_FORM.findall(texto)
        assert formularios, f"{nombre} no declara formularios"
        for _atributos, cuerpo in formularios:
            assert any(marcador in cuerpo for marcador in MARCADORES_CSRF), nombre
