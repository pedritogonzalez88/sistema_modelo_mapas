"""Rutas de autenticación (Fases A1 y A3).

A1: login, logout y cambio de contraseña.
A3: segundo factor TOTP, enrolamiento, códigos de respaldo y sesiones activas.

No hay código por correo ni recuperación por correo todavía (Fase A4): las
rutas ``/auth/recuperar`` y ``/auth/restablecer`` se declaran públicas en el
gate para no redirigir en bucle cuando se implementen, pero aquí no existen.

Reglas de seguridad que esta capa garantiza:

- Ningún handler devuelve el secreto TOTP, un código ni un token: el secreto
  solo aparece una vez, en el QR y en la clave manual del enrolamiento
  pendiente; los códigos de respaldo, una sola vez, en la respuesta que los
  emite.
- Todo POST exige CSRF (``csrf_protect``) y las vistas con datos de sesión o
  de un solo uso se sirven con ``Cache-Control: no-store``.
- El challenge de segundo factor vive en ``auth_tokens``; la cookie solo
  transporta el token en claro y nunca se registra.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

import segno
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import csrf, middleware
from app.auth.service import (
    autenticar,
    cambiar_password,
    cargar_desafio,
    codigos_restantes,
    confirmar_enrolamiento,
    crear_desafio,
    crear_sesion,
    estado_mfa,
    iniciar_enrolamiento,
    pendiente_enrolamiento,
    regenerar_respaldo,
    revocar_por_token,
    revocar_sesion_de,
    sesiones_activas,
    verificar_desafio,
)
from app.core.config import settings
from app.core.database import get_db
from app.models import Sesion, Usuario
from app.web import templates

router = APIRouter(prefix="/auth", tags=["auth"])

_MENSAJE_GENERICO = "Usuario o contraseña incorrectos."
_MENSAJE_CODIGO = "Código incorrecto o expirado."


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _agente(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _sin_cache(respuesta: HTMLResponse) -> HTMLResponse:
    """``no-store`` para pantallas con datos de un solo uso o de sesión."""
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


def _redirigir_sin_sesion() -> RedirectResponse:
    """Sin identidad: al login si la bandera está activa, al inicio si no."""
    destino = "/auth/login" if settings.auth_enabled else "/"
    return RedirectResponse(destino, status_code=303)


# --- Login y sesión -------------------------------------------------------------
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

    estado = estado_mfa(db, resultado.usuario)

    # Segundo factor exigido y ya enrolado: la sesión NO se crea todavía.
    if estado.requiere_desafio:
        revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
        token = crear_desafio(db, resultado.usuario, _ip(request))
        destino = middleware.ruta_segura(siguiente, "")
        url = "/auth/mfa" + (f"?next={quote(destino, safe='')}" if destino else "")
        respuesta = RedirectResponse(url, status_code=303)
        middleware.aplicar_cookie_mfa(respuesta, token)
        middleware.borrar_cookie_sesion(respuesta)
        return respuesta

    # Rotación: la sesión anterior de esta cookie (si existía) se revoca.
    revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
    token, _ = crear_sesion(db, resultado.usuario, _ip(request), _agente(request))

    # Si su política exige TOTP y no está enrolado, el único destino posible
    # es la pantalla de seguridad (el gate no dejaría pasar a ningún otro lado).
    destino = ("/auth/seguridad" if estado.requiere_enrolamiento
               else middleware.ruta_segura(siguiente))
    respuesta = RedirectResponse(destino, status_code=303)
    middleware.aplicar_cookie_sesion(respuesta, token)
    return respuesta


@router.post("/logout", dependencies=[Depends(csrf.csrf_protect)])
def logout(request: Request, db: Session = Depends(get_db)):
    """Cierra la sesión: revoca la fila, borra la cookie y los desafíos."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
    respuesta = RedirectResponse("/auth/login?ok=sesion_cerrada", status_code=303)
    middleware.borrar_cookie_sesion(respuesta)
    middleware.borrar_cookie_mfa(respuesta)
    return respuesta


# --- Challenge de segundo factor (Fase A3) -------------------------------------
def _mfa_response(request: Request, *, codigo: int = 200, error: str | None = None,
                  siguiente: str = "", valor: str = "",
                  valor_respaldo: str = "") -> HTMLResponse:
    """Pinta el formulario del segundo factor sin cachear la respuesta."""
    return _sin_cache(templates.TemplateResponse(
        request, "auth/mfa.html",
        {"next": middleware.ruta_segura(siguiente, ""), "error": error,
         "codigo": valor, "respaldo": valor_respaldo},
        status_code=codigo,
    ))


@router.get("/mfa", response_class=HTMLResponse)
def mfa_form(request: Request, next: str | None = None,
             db: Session = Depends(get_db)):
    """Paso de verificación. Sin challenge vigente se vuelve al login."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    desafio = cargar_desafio(db, request.cookies.get(middleware.nombre_cookie_mfa()))
    if desafio is None:
        respuesta = RedirectResponse("/auth/login?error=desafio_expirado",
                                     status_code=303)
        middleware.borrar_cookie_mfa(respuesta)
        return respuesta
    return _mfa_response(request, siguiente=next or "")


@router.post("/mfa", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def mfa_verificar(request: Request, db: Session = Depends(get_db),
                  codigo: str = Form(""), respaldo: str = Form(""),
                  siguiente: str = Form("", alias="next")):
    """Valida TOTP o un código de respaldo y, si acierta, abre la sesión."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)

    token_challenge = request.cookies.get(middleware.nombre_cookie_mfa())
    desafio = cargar_desafio(db, token_challenge)
    if desafio is None:
        respuesta = RedirectResponse("/auth/login?error=desafio_expirado",
                                     status_code=303)
        middleware.borrar_cookie_mfa(respuesta)
        return respuesta

    # Un solo campo visible (TOTP) y otro opcional para el código de respaldo.
    valor = (respaldo or "").strip() or (codigo or "").strip()
    if not valor:
        return _mfa_response(request, error="Ingrese el código de 6 dígitos.",
                             siguiente=siguiente)

    resultado = verificar_desafio(db, desafio, valor, ip=_ip(request),
                                  user_agent=_agente(request))

    if resultado.motivo == "limite":
        respuesta = RedirectResponse("/auth/login?error=mfa_bloqueado",
                                     status_code=303)
        middleware.borrar_cookie_mfa(respuesta)
        middleware.borrar_cookie_sesion(respuesta)
        return respuesta

    if not resultado.ok:
        return _mfa_response(request, error=_MENSAJE_CODIGO, siguiente=siguiente)

    # Éxito: se rota la sesión previa (si había cookie) y se marca mfa_at.
    revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
    token_sesion, _ = crear_sesion(
        db, resultado.usuario, _ip(request), _agente(request),
        mfa_at=datetime.utcnow(),
    )
    respuesta = RedirectResponse(
        middleware.ruta_segura(siguiente), status_code=303)
    middleware.aplicar_cookie_sesion(respuesta, token_sesion)
    middleware.borrar_cookie_mfa(respuesta)
    return respuesta


# --- Pantalla de seguridad y enrolamiento (Fase A3) ----------------------------
def _agrupar(secreto: str, tam: int = 4) -> str:
    """Clave manual en bloques de 4 caracteres (más fácil de transcribir)."""
    limpio = (secreto or "").strip()
    return " ".join(limpio[i:i + tam] for i in range(0, len(limpio), tam))


def _pendiente_visual(db: Session, usuario: Usuario) -> dict | None:
    """QR en SVG y clave manual del enrolamiento pendiente, si existe."""
    datos = pendiente_enrolamiento(db, usuario)
    if datos is None:
        return None
    secreto, uri = datos
    return {"qr": segno.make(uri).svg_inline(scale=4),
            "manual": _agrupar(secreto)}


def _seguridad_response(request: Request, db: Session, usuario: Usuario, *,
                        codigo: int = 200, error: str | None = None,
                        pendiente: dict | None = None,
                        codigos_nuevos: list[str] | None = None) -> HTMLResponse:
    """Renderiza ``auth/seguridad.html`` con el estado completo de la cuenta."""
    sesion_actual = getattr(getattr(request.state, "sesion", None), "id", None)
    return _sin_cache(templates.TemplateResponse(
        request, "auth/seguridad.html",
        {
            "proyecto": None,
            "migas": [{"label": "Inicio", "href": "/"}, {"label": "Seguridad"}],
            "estado": estado_mfa(db, usuario),
            "sesiones": sesiones_activas(db, usuario.id),
            "sesion_actual": sesion_actual,
            "codigos_restantes": codigos_restantes(db, usuario.id),
            "error": error,
            "pendiente": pendiente,
            "codigos_nuevos": codigos_nuevos,
        },
        status_code=codigo,
    ))


@router.get("/seguridad", response_class=HTMLResponse)
def seguridad(request: Request, db: Session = Depends(get_db)):
    """Estado del segundo factor, códigos de respaldo y sesiones activas."""
    usuario_sesion = getattr(request.state, "usuario", None)
    if usuario_sesion is None:
        return _redirigir_sin_sesion()
    usuario = db.get(Usuario, usuario_sesion.id)
    if usuario is None:
        return _redirigir_sin_sesion()
    return _seguridad_response(request, db, usuario)


@router.post("/seguridad/totp/iniciar", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def totp_iniciar(request: Request, db: Session = Depends(get_db)):
    """Genera el secreto pendiente y muestra el QR con la clave manual."""
    usuario_sesion = getattr(request.state, "usuario", None)
    if usuario_sesion is None:
        return _redirigir_sin_sesion()
    usuario = db.get(Usuario, usuario_sesion.id)
    if usuario is None:
        return _redirigir_sin_sesion()

    if iniciar_enrolamiento(db, usuario) is None:
        return _seguridad_response(
            request, db, usuario,
            error=("Su autenticador ya está activo. Para reemplazarlo pida un "
                   "reset asistido a la administración."),
        )
    return _seguridad_response(request, db, usuario,
                               pendiente=_pendiente_visual(db, usuario))


@router.post("/seguridad/totp/confirmar", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def totp_confirmar(request: Request, db: Session = Depends(get_db),
                   codigo: str = Form("")):
    """Activa el TOTP y muestra los códigos de respaldo una única vez."""
    usuario_sesion = getattr(request.state, "usuario", None)
    if usuario_sesion is None:
        return _redirigir_sin_sesion()
    usuario = db.get(Usuario, usuario_sesion.id)
    if usuario is None:
        return _redirigir_sin_sesion()

    # La sesión se relee en esta unidad de trabajo para poder marcarla con
    # ``mfa_at`` (la del gate pertenece a otra sesión de base de datos).
    sesion_gate = getattr(request.state, "sesion", None)
    sesion = db.get(Sesion, sesion_gate.id) if sesion_gate is not None else None

    codigos, error = confirmar_enrolamiento(
        db, usuario, codigo, sesion=sesion, ip=_ip(request),
        user_agent=_agente(request),
    )
    if codigos is None:
        return _seguridad_response(
            request, db, usuario, error=error,
            pendiente=_pendiente_visual(db, usuario),
        )
    return _seguridad_response(request, db, usuario, codigos_nuevos=codigos)


@router.post("/seguridad/respaldo/regenerar", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def respaldo_regenerar(request: Request, db: Session = Depends(get_db),
                       codigo: str = Form("")):
    """Invalida los códigos sin usar y emite 10 nuevos (una sola vez)."""
    usuario_sesion = getattr(request.state, "usuario", None)
    if usuario_sesion is None:
        return _redirigir_sin_sesion()
    usuario = db.get(Usuario, usuario_sesion.id)
    if usuario is None:
        return _redirigir_sin_sesion()

    codigos, error = regenerar_respaldo(
        db, usuario, codigo, ip=_ip(request), user_agent=_agente(request),
    )
    if codigos is None:
        return _seguridad_response(request, db, usuario, error=error)
    return _seguridad_response(request, db, usuario, codigos_nuevos=codigos)


@router.post("/sesiones/{sesion_id}/revocar",
             dependencies=[Depends(csrf.csrf_protect)])
def sesion_revocar(request: Request, sesion_id: str,
                   db: Session = Depends(get_db)):
    """Revoca otra sesión propia; la sesión en curso se cierra con «Salir»."""
    usuario_sesion = getattr(request.state, "usuario", None)
    if usuario_sesion is None:
        return _redirigir_sin_sesion()

    actual = getattr(getattr(request.state, "sesion", None), "id", None)
    resultado = revocar_sesion_de(
        db, usuario_sesion.id, sesion_id, sesion_actual=actual,
        ip=_ip(request), user_agent=_agente(request),
    )
    codigos = {
        "ok": "?ok=session_revoked",
        "sesion_actual": "?error=sesion_actual",
        "no_encontrada": "?error=sesion_no_encontrada",
    }
    return RedirectResponse("/auth/seguridad" + codigos.get(resultado, ""),
                            status_code=303)


# --- Cambio de contraseña ------------------------------------------------------
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
