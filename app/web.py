from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ---------------------------------------------------------------------------
# Feedback de acciones sin sesiones ni cookies: la URL lleva un código
# semántico (?ok=... / ?error=...) y aquí se traduce a texto + tono.
# Ventaja: sin XSS (nunca se refleja texto de la URL), testeable y sobrevive
# a las redirecciones 303.
# ---------------------------------------------------------------------------
FLASH_OK = {
    "entrega_creada":     ("Entrega recibida. Revise la vista previa antes de confirmar.", "info"),
    "entrega_confirmada": ("Entrega confirmada y cruces recalculados.", "ok"),
    "entrega_rechazada":  ("Entrega rechazada. El archivo se conserva como evidencia.", "warn"),
    "ficha_guardada":     ("Dictamen guardado.", "ok"),
    "ficha_transicion":   ("Estado de la ficha actualizado.", "ok"),
    "reemplazo_creado":   ("Reemplazo propuesto registrado.", "ok"),
    "reserva_propuesta":  ("Reserva asignada al punto de origen.", "ok"),
    "reemplazo_estado":   ("Estado del reemplazo actualizado.", "ok"),
    "malla_cargada":      ("Metadatos de la malla actualizados.", "ok"),
    "mapa_cargado":       ("Clases del mapa MADES cargadas.", "ok"),
    # Autenticación (Fase A1)
    "sesion_cerrada":     ("Sesión cerrada correctamente.", "ok"),
    "password_changed":   ("Contraseña actualizada. Las demás sesiones se cerraron.", "ok"),
}

FLASH_ERROR = {
    "archivo_invalido": "El archivo no tiene un formato válido.",
    "duplicado":        "El archivo ya fue cargado (misma huella SHA-256).",
    "clase_requerida":  "Para cerrar por consenso se exige una clase definitiva 1–9.",
    "transicion_invalida": "El cambio de estado solicitado no está permitido desde el estado actual.",
}


def _flash(request):
    """Traduce ?ok= / ?error= a {text, tone}. Código desconocido -> None.

    Nunca devuelve el valor crudo de la URL: solo textos del diccionario.
    """
    ok = request.query_params.get("ok")
    if ok in FLASH_OK:
        text, tone = FLASH_OK[ok]
        return {"text": text, "tone": tone}
    err = request.query_params.get("error")
    if err in FLASH_ERROR:
        return {"text": FLASH_ERROR[err], "tone": "danger"}
    return None


templates.env.globals["flash"] = _flash


def _csrf_token(request) -> str:
    """Token CSRF del formulario actual (global Jinja ``csrf_token(request)``).

    La importación es local para que ``app.web`` no arrastre el paquete de
    autenticación al importarse desde los routers y para que la resolución de
    dependencias quede en un único punto.
    """
    from app.auth.csrf import token_actual

    return token_actual(request)


templates.env.globals["csrf_token"] = _csrf_token
