"""Fechas del mailer sin ``DeprecationWarning`` (A6.12).

``app/auth/mailer.py`` conservaba tres ``datetime.utcnow()`` (el último archivo
de la aplicación con la llamada deprecada en Python 3.12+). Estos casos cubren
los tres puntos: nombre del ``.eml``, cierre de la fila de la cola y reenvío.

No tocan la base: la sesión de SQLAlchemy se sustituye por una falsa y el envío
externo por un no-op. La garantía es que **ninguna** de las rutas emite el
``DeprecationWarning`` de ``utcnow``.
"""
from __future__ import annotations

import warnings
from datetime import datetime
from email.message import EmailMessage
from types import SimpleNamespace


class _SesionFalsa:
    """Sesión mínima: ``with``, ``get``, ``scalars`` y ``commit``."""

    def __init__(self, filas=None):
        self.filas = list(filas or [])
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _modelo, _pk):
        return self.filas[0] if self.filas else None

    def scalars(self, _consulta):
        return iter(self.filas)

    def commit(self):
        self.commits += 1


def _avisos_utcnow(capturadas) -> list[str]:
    return [str(item.message) for item in capturadas
            if "utcnow" in str(item.message).lower()]


def _capturar(funcion):
    with warnings.catch_warnings(record=True) as capturadas:
        warnings.simplefilter("always")
        resultado = funcion()
    return resultado, capturadas


def test_escribir_eml_no_emite_utcnow(tmp_path, monkeypatch):
    """El nombre del ``.eml`` usa el helper local, no ``datetime.utcnow``."""
    from app.auth import mailer
    from app.core.config import settings

    monkeypatch.setattr(settings, "email_dir", str(tmp_path))
    mensaje = EmailMessage()
    mensaje["From"] = "no-reply@localhost"
    mensaje["To"] = "destino@ejemplo.test"
    mensaje["Subject"] = "Prueba de fechas"
    mensaje.set_content("cuerpo")

    _, capturadas = _capturar(lambda: mailer._escribir_eml(mensaje))

    assert _avisos_utcnow(capturadas) == []
    assert len(list(tmp_path.glob("*.eml"))) == 1


def test_marcar_envio_no_emite_utcnow(monkeypatch):
    """El cierre correcto de la fila fija ``enviado_en`` sin la llamada vieja."""
    from app.auth import mailer

    fila = SimpleNamespace(intentos=1, enviado_en=None, ultimo_error="previo")
    sesion = _SesionFalsa([fila])
    monkeypatch.setattr(mailer, "SessionLocal", lambda: sesion)

    _, capturadas = _capturar(lambda: mailer._marcar(7))

    assert _avisos_utcnow(capturadas) == []
    assert isinstance(fila.enviado_en, datetime)
    assert fila.enviado_en.tzinfo is None
    assert fila.ultimo_error is None
    assert sesion.commits == 1


def test_reintentar_pendientes_no_emite_utcnow(monkeypatch):
    """El reenvío de la cola marca ``enviado_en`` sin ``datetime.utcnow``."""
    from app.auth import mailer

    fila = SimpleNamespace(
        cuerpo='{"texto": "hola", "html": ""}',
        destinatario="destino@ejemplo.test",
        asunto="Reenvío",
        intentos=0,
        enviado_en=None,
        ultimo_error=None,
    )
    sesion = _SesionFalsa([fila])
    monkeypatch.setattr(mailer, "_entregar", lambda *_a, **_k: None)

    resumen, capturadas = _capturar(
        lambda: mailer.reintentar_pendientes(sesion, limit=5))

    assert _avisos_utcnow(capturadas) == []
    assert resumen == {"revisados": 1, "enviados": 1, "fallidos": 0}
    assert isinstance(fila.enviado_en, datetime)
    assert sesion.commits == 1
