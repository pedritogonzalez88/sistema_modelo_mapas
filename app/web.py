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
    # Autenticación (Fase A3). Los códigos de respaldo y su confirmación se
    # muestran en la misma respuesta (una sola vez), por eso no hay flash para
    # ``mfa_activado`` ni ``mfa_recovery_regenerated``: redirigir los perdería.
    "session_revoked":    ("Sesión revocada. Ese dispositivo ya no tiene acceso.", "ok"),
    # Autenticación (Fase A4)
    "password_reset":     ("Contraseña restablecida. Ingrese con su nueva clave.", "ok"),
    # Administración de usuarios (Fase A6b). La contraseña temporal y los
    # códigos de respaldo nunca viajan por la URL: se muestran en la propia
    # respuesta del POST, por eso ``password_temporal`` solo documenta el estado.
    "usuario_creado":     ("Usuario creado. Entregue la contraseña temporal por un canal seguro.", "ok"),
    "usuario_actualizado": ("Cambio aplicado y registrado en la auditoría.", "ok"),
    "password_temporal":  ("Contraseña temporal generada. Se muestra una sola vez.", "warn"),
    "mfa_reseteado":      ("Segundo factor reiniciado. El usuario deberá enrolarse de nuevo.", "ok"),
}

FLASH_ERROR = {
    "archivo_invalido": "El archivo no tiene un formato válido.",
    "duplicado":        "El archivo ya fue cargado (misma huella SHA-256).",
    "validacion_pendiente": "El archivo tiene errores de validación. Corríjalos y vuelva a cargarlo antes de confirmar.",
    "confirmacion_invalida": "La entrega no se pudo confirmar. Revise el archivo y vuelva a intentarlo.",
    "clase_requerida":  "Para cerrar por consenso se exige una clase definitiva 1–9.",
    "transicion_invalida": "El cambio de estado solicitado no está permitido desde el estado actual.",
    # Autenticación (Fase A3)
    "desafio_expirado": "El paso de verificación expiró. Ingrese de nuevo con su contraseña.",
    "mfa_bloqueado":    "Demasiados códigos incorrectos. Ingrese de nuevo con su contraseña.",
    "sesion_actual":    "Para cerrar esta sesión use la opción «Salir» del menú de la cuenta.",
    "sesion_no_encontrada": "La sesión indicada ya no existe o no pertenece a su cuenta.",
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


def _puede(request, permiso: str) -> bool:
    """Autorización para plantillas (global Jinja ``puede(request, permiso)``).

    La matriz vive en ``app.auth.permissions``; aquí solo se expone. La UI la
    usa para ocultar navegación y botones: **no** es el control de seguridad,
    que siempre ocurre en el servidor (``require_permiso``).
    """
    from app.auth.permissions import puede

    return puede(request, permiso)


def _rol_etiqueta(rol: str | None) -> str:
    """Etiqueta legible de un rol, sin duplicar el diccionario en el shell."""
    from app.auth.permissions import ROL_ETIQUETAS

    if not rol:
        return ""
    return ROL_ETIQUETAS.get(rol, rol)


templates.env.globals["puede"] = _puede
templates.env.globals["rol_etiqueta"] = _rol_etiqueta
