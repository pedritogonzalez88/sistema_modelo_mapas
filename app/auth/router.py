"""Rutas de autenticación (Fase A1): login, logout y cambio de contraseña.

No hay TOTP ni recuperación por correo todavía (Fases A3 y A4): las rutas
``/auth/recuperar`` y ``/auth/restablecer`` se declaran públicas en el gate
para no redirigir en bucle cuando se implementen, pero aquí no existen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import csrf, middleware
from app.auth.service import (
    autenticar,
    cambiar_password,
    crear_sesion,
    revocar_por_token,
)
from app.core.config import settings
from app.core.database import get_db
from app.models import Usuario
from app.web import templates

router = APIRouter(prefix="/auth", tags=["auth"])

_MENSAJE_GENERICO = "Usuario o contraseña incorrectos."


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _agente(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _login_response(request: Request, *, codigo: int = 200, error: str | None = None,
                    espera_min: int = 0, login: str = "",
                    siguiente: str = "") -> HTMLResponse:
    """Pinta el formulario de login conservando el usuario escrito."""
    return templates.TemplateResponse(
        request, "auth/login.html",
        {"next": middleware.ruta_segura(siguiente, ""), "login": login,
         "error": error, "espera_min": espera_min},
        status_code=codigo,
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = None,
               db: Session = Depends(get_db)):
    """Formulario de acceso. Si ya hay sesión, no se vuelve a pedir."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    if getattr(request.state, "usuario", None) is not None:
        return RedirectResponse(middleware.ruta_segura(next), status_code=303)
    return _login_response(request, siguiente=next or "")


@router.post("/login", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def login(request: Request, db: Session = Depends(get_db),
          login: str = Form(""), password: str = Form(""),
          siguiente: str = Form("", alias="next")):
    """Valida credenciales y crea la sesión; error genérico ante cualquier fallo."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)

    resultado = autenticar(db, login, password, _ip(request), _agente(request))
    if not resultado.ok:
        mensaje = _MENSAJE_GENERICO
        if resultado.motivo == "bloqueado":
            minutos = max(1, resultado.espera_min)
            unidad = "minuto" if minutos == 1 else "minutos"
            mensaje = (f"Demasiados intentos fallidos. "
                       f"Intente nuevamente en {minutos} {unidad}.")
        return _login_response(request, codigo=200, error=mensaje,
                               espera_min=resultado.espera_min, login=login,
                               siguiente=siguiente)

    # Rotación: la sesión anterior de esta cookie (si existía) se revoca.
    revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
    token, _ = crear_sesion(db, resultado.usuario, _ip(request), _agente(request))

    respuesta = RedirectResponse(
        middleware.ruta_segura(siguiente), status_code=303)
    middleware.aplicar_cookie_sesion(respuesta, token)
    return respuesta


@router.post("/logout", dependencies=[Depends(csrf.csrf_protect)])
def logout(request: Request, db: Session = Depends(get_db)):
    """Cierra la sesión: revoca la fila y borra la cookie."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
    respuesta = RedirectResponse("/auth/login?ok=sesion_cerrada", status_code=303)
    middleware.borrar_cookie_sesion(respuesta)
    return respuesta


def _redirigir_sin_sesion() -> RedirectResponse:
    destino = "/auth/login" if settings.auth_enabled else "/"
    return RedirectResponse(destino, status_code=303)


@router.get("/cambiar-password", response_class=HTMLResponse)
def cambiar_password_form(request: Request):
    """Formulario de cambio de contraseña (exige sesión por el gate)."""
    usuario = getattr(request.state, "usuario", None)
    if usuario is None:
        return _redirigir_sin_sesion()
    return templates.TemplateResponse(
        request, "auth/cambiar_password.html",
        {"errores": [], "must_change": bool(usuario.must_change_password)},
    )


@router.post("/cambiar-password", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def cambiar_password_post(request: Request, db: Session = Depends(get_db),
                          actual: str = Form(""), nueva: str = Form(""),
                          confirmacion: str = Form("")):
    """Aplica el cambio o vuelve a pintar el formulario con los errores."""
    usuario_sesion = getattr(request.state, "usuario", None)
    if usuario_sesion is None:
        return _redirigir_sin_sesion()

    # El usuario del gate vive en la sesión de base del middleware; se relee en
    # la sesión del router para guardar con la unidad de trabajo correcta.
    usuario = db.get(Usuario, usuario_sesion.id)
    if usuario is None:
        return _redirigir_sin_sesion()

    sesion = getattr(request.state, "sesion", None)
    errores = cambiar_password(
        db, usuario, actual, nueva, confirmacion,
        sesion_actual=getattr(sesion, "id", None),
        ip=_ip(request), user_agent=_agente(request),
    )
    if errores:
        return templates.TemplateResponse(
            request, "auth/cambiar_password.html",
            {"errores": errores, "must_change": bool(usuario.must_change_password)},
            status_code=200,
        )
    return RedirectResponse("/auth/cambiar-password?ok=password_changed",
                            status_code=303)
