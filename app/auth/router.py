"""Rutas de autenticación (Fases A1, A3 y A4).

A1: login, logout y cambio de contraseña.
A3: segundo factor TOTP, enrolamiento, códigos de respaldo y sesiones activas.
A4: código de 6 dígitos por correo, reenvío con límites y recuperación de
contraseña por enlace.

Reglas de seguridad que esta capa garantiza:

- Ningún handler devuelve el secreto TOTP, un código ni un token: el secreto
  solo aparece una vez, en el QR y en la clave manual del enrolamiento
  pendiente; los códigos de respaldo, una sola vez, en la respuesta que los
  emite. El código por correo **nunca** se pinta en pantalla: solo viaja al
  buzón del usuario.
- El método del segundo factor se valida contra el estado del servidor: pedir
  ``?metodo=email`` no basta si no hay un código emitido y vigente.
- Todo POST exige CSRF (``csrf_protect``) y las vistas con datos de sesión o
  de un solo uso se sirven con ``Cache-Control: no-store``.
- La recuperación responde siempre con el mismo texto y código HTTP, exista o
  no la cuenta, sea local o LDAP, esté activa o no.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

import segno
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import csrf, middleware
from app.auth.router_common import _agente, _ip, _sin_cache
from app.auth.routes_users import router as users_router
from app.auth.service import (
    RESPUESTA_RECUPERACION,
    autenticar,
    cambiar_password,
    cargar_desafio,
    codigo_email_vigente,
    codigos_restantes,
    confirmar_enrolamiento,
    crear_desafio,
    crear_sesion,
    emitir_codigo_email,
    estado_mfa,
    iniciar_enrolamiento,
    pendiente_enrolamiento,
    puede_enviar_codigo,
    puede_enviar_codigo_por_ip,
    regenerar_respaldo,
    restablecer_password,
    revocar_por_token,
    revocar_sesion_de,
    sesiones_activas,
    solicitar_reset,
    validar_token_reset,
    verificar_desafio,
    verificar_desafio_email,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.security import utcnow
from app.models import Sesion, Usuario
from app.web import templates

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(users_router)

_MENSAJE_GENERICO = "Usuario o contraseña incorrectos."
_MENSAJE_CODIGO = "Código incorrecto o expirado."
_MENSAJE_ENLACE = "El enlace no es válido o expiró."


def _cambio_password_url(siguiente: str | None) -> str:
    """Destino de cambio obligatorio, conservando el destino original."""
    destino = middleware.ruta_segura(siguiente, "/")
    if destino.startswith("/auth/cambiar-password"):
        destino = "/"
    return f"/auth/cambiar-password?next={quote(destino, safe='')}"


def _con_ok(destino: str) -> str:
    """Agrega el flash de contraseña cambiada sin pisar la query existente."""
    separador = "&" if "?" in destino else "?"
    return f"{destino}{separador}ok=password_changed"



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
        # Mismo mensaje para credenciales inválidas y cuenta bloqueada: evita
        # distinguir qué usuarios existen. El tiempo de espera se registra en
        # la auditoría, no se expone en la respuesta.
        return _login_response(request, codigo=200, error=_MENSAJE_GENERICO,
                               espera_min=resultado.espera_min, login=login,
                               siguiente=siguiente)

    estado = estado_mfa(db, resultado.usuario)

    # Segundo factor exigido (TOTP o correo): la sesión NO se crea todavía.
    if estado.requiere_desafio:
        revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
        token = crear_desafio(db, resultado.usuario, _ip(request))
        destino = middleware.ruta_segura(siguiente, "")
        parametros = {"metodo": estado.metodo or "totp"}
        if destino:
            parametros["next"] = destino
        if estado.metodo == "email":
            enviado = emitir_codigo_email(
                db, resultado.usuario, ip=_ip(request), user_agent=_agente(request))
            if not enviado:
                parametros["aviso"] = "correo_fallido"
        respuesta = RedirectResponse(
            "/auth/mfa?" + urlencode(parametros), status_code=303)
        middleware.aplicar_cookie_mfa(respuesta, token)
        middleware.borrar_cookie_sesion(respuesta)
        return respuesta

    # Rotación: la sesión anterior de esta cookie (si existía) se revoca.
    revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
    token, _ = crear_sesion(db, resultado.usuario, _ip(request), _agente(request))

    # Orden de prioridad: primero el cambio obligatorio, luego el enrolamiento
    # TOTP y finalmente el destino original. Así se evita el salto doble
    # ``/auth/seguridad`` -> ``/auth/cambiar-password``.
    if resultado.usuario.must_change_password:
        destino = _cambio_password_url(siguiente)
    elif estado.requiere_enrolamiento:
        destino = "/auth/seguridad"
    else:
        destino = middleware.ruta_segura(siguiente)
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


# --- Challenge de segundo factor (Fases A3 y A4) --------------------------------
def _mascarar_email(email: str | None) -> str:
    """Correo parcialmente oculto para confirmar el destino sin exponerlo."""
    correo = (email or "").strip()
    if "@" not in correo:
        return ""
    local, dominio = correo.split("@", 1)
    visible = local[:1] if local else ""
    return f"{visible}{'*' * max(1, len(local) - len(visible))}@{dominio}"


def _puede_correo(usuario: Usuario | None, estado=None) -> bool:
    """``True`` si la política y el correo habilitan el código por correo."""
    if usuario is None:
        return False
    policy = (estado.policy if estado is not None
              else (usuario.mfa_policy or "any").strip().lower())
    return bool((usuario.email or "").strip()) and policy in ("email", "any")


def _mfa_response(request: Request, db: Session, desafio,
                  *, codigo: int = 200, error: str | None = None,
                  siguiente: str = "", valor: str = "",
                  valor_respaldo: str = "", metodo: str = "totp",
                  aviso: str | None = None, espera: int = 0) -> HTMLResponse:
    """Pinta el formulario del segundo factor sin cachear la respuesta."""
    usuario = db.get(Usuario, desafio.usuario_id) if desafio is not None else None
    estado = estado_mfa(db, usuario)
    fila_codigo = codigo_email_vigente(db, usuario) if usuario is not None else None
    vigente = bool(fila_codigo is not None and fila_codigo.expires_at is not None
                   and fila_codigo.expires_at > utcnow())
    return _sin_cache(templates.TemplateResponse(
        request, "auth/mfa.html",
        {"next": middleware.ruta_segura(siguiente, ""), "error": error,
         "aviso": aviso, "espera": espera,
         "codigo": valor, "respaldo": valor_respaldo,
         "metodo": metodo if metodo in ("totp", "email") else "totp",
         "email_mascarado": _mascarar_email(getattr(usuario, "email", None)),
         "tiene_totp": estado.puede_totp,
         "puede_email": _puede_correo(usuario, estado),
         "codigo_vigente": vigente},
        status_code=codigo,
    ))


def _desafio_o_redirect(request: Request, db: Session):
    """Recupera el challenge de la cookie; si no vale, redirige al login."""
    desafio = cargar_desafio(db, request.cookies.get(middleware.nombre_cookie_mfa()))
    if desafio is not None:
        return desafio, None
    respuesta = RedirectResponse("/auth/login?error=desafio_expirado",
                                 status_code=303)
    middleware.borrar_cookie_mfa(respuesta)
    return None, respuesta


@router.get("/mfa", response_class=HTMLResponse)
def mfa_form(request: Request, next: str | None = None,
             metodo: str | None = None, aviso: str | None = None,
             db: Session = Depends(get_db)):
    """Paso de verificación. Sin challenge vigente se vuelve al login."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    desafio, respuesta = _desafio_o_redirect(request, db)
    if desafio is None:
        return respuesta
    usuario = db.get(Usuario, desafio.usuario_id)
    elegido = "email" if (metodo or "").strip().lower() == "email" else "totp"
    if elegido == "email" and not _puede_correo(usuario):
        elegido = "totp"
    # El aviso de "enviado" solo se muestra tras el POST que lo emite; por GET
    # únicamente se acepta el fallo de envío del login (texto fijo).
    aviso_get = aviso if aviso == "correo_fallido" else None
    return _mfa_response(request, db, desafio, siguiente=next or "",
                         metodo=elegido, aviso=aviso_get)


@router.post("/mfa", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def mfa_verificar(request: Request, db: Session = Depends(get_db),
                  codigo: str = Form(""), respaldo: str = Form(""),
                  metodo: str = Form(""),
                  siguiente: str = Form("", alias="next")):
    """Valida TOTP, un código de respaldo o el código enviado por correo."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)

    desafio, respuesta = _desafio_o_redirect(request, db)
    if desafio is None:
        return respuesta
    es_correo = (metodo or "").strip().lower() == "email"

    # Un solo campo visible (TOTP/correo) y otro opcional para el respaldo.
    valor = (respaldo or "").strip() or (codigo or "").strip()
    if not valor:
        return _mfa_response(
            request, db, desafio,
            error="Ingrese el código de 6 dígitos.", siguiente=siguiente,
            metodo="email" if es_correo else "totp")

    if es_correo:
        resultado = verificar_desafio_email(
            db, desafio, valor, ip=_ip(request), user_agent=_agente(request))
    else:
        resultado = verificar_desafio(
            db, desafio, valor, ip=_ip(request), user_agent=_agente(request))

    if resultado.motivo == "limite":
        respuesta = RedirectResponse("/auth/login?error=mfa_bloqueado",
                                     status_code=303)
        middleware.borrar_cookie_mfa(respuesta)
        middleware.borrar_cookie_sesion(respuesta)
        return respuesta

    if not resultado.ok:
        return _mfa_response(request, db, desafio, error=_MENSAJE_CODIGO,
                             siguiente=siguiente,
                             metodo="email" if es_correo else "totp")

    # Éxito: se rota la sesión previa (si había cookie) y se marca mfa_at.
    revocar_por_token(db, request.cookies.get(middleware.nombre_cookie_sesion()))
    token_sesion, _ = crear_sesion(
        db, resultado.usuario, _ip(request), _agente(request),
        mfa_at=utcnow(),
    )
    if resultado.usuario.must_change_password:
        destino = _cambio_password_url(siguiente)
    else:
        destino = middleware.ruta_segura(siguiente)
    respuesta = RedirectResponse(destino, status_code=303)
    middleware.aplicar_cookie_sesion(respuesta, token_sesion)
    middleware.borrar_cookie_mfa(respuesta)
    return respuesta


@router.post("/mfa/enviar", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def mfa_enviar(request: Request, db: Session = Depends(get_db),
               siguiente: str = Form("", alias="next")):
    """Emite y envía un código por correo respetando cooldown y límite horario.

    También es el cambio explícito a correo cuando la política es ``any`` y el
    usuario ya tiene TOTP: el método se decide aquí, no en la query.
    """
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)

    desafio, respuesta = _desafio_o_redirect(request, db)
    if desafio is None:
        return respuesta
    usuario = db.get(Usuario, desafio.usuario_id)
    if usuario is None or not usuario.is_active:
        respuesta = RedirectResponse("/auth/login?error=desafio_expirado",
                                     status_code=303)
        middleware.borrar_cookie_mfa(respuesta)
        return respuesta

    if not _puede_correo(usuario):
        return _mfa_response(
            request, db, desafio,
            error=("Su cuenta no tiene un correo disponible para el segundo "
                   "factor. Use el autenticador o un código de respaldo."),
            siguiente=siguiente, metodo="totp")

    permitido, motivo, espera = puede_enviar_codigo(db, usuario)
    if not permitido:
        mensaje = ("Espere unos segundos antes de pedir otro código."
                   if motivo == "cooldown" else
                   "Se alcanzó el máximo de 5 códigos por hora. Use su "
                   "autenticador, un código de respaldo o solicite un reset "
                   "asistido.")
        return _mfa_response(request, db, desafio, error=mensaje,
                             siguiente=siguiente, metodo="email", espera=espera)

    # Fase A6a: tope por IP. Mismo formulario y mensaje neutro, sin revelar el
    # motivo real ni si la cuenta tiene correo.
    if not puede_enviar_codigo_por_ip(db, _ip(request)):
        return _mfa_response(request, db, desafio,
                             error="Intente más tarde.",
                             siguiente=siguiente, metodo="email")

    enviado = emitir_codigo_email(
        db, usuario, ip=_ip(request), user_agent=_agente(request))
    return _mfa_response(
        request, db, desafio, siguiente=siguiente, metodo="email",
        aviso="correo_enviado" if enviado else "correo_fallido")


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
    estado = estado_mfa(db, usuario)
    return _sin_cache(templates.TemplateResponse(
        request, "auth/seguridad.html",
        {
            "proyecto": None,
            "migas": [{"label": "Inicio", "href": "/"}, {"label": "Seguridad"}],
            "estado": estado,
            "sesiones": sesiones_activas(db, usuario.id),
            "sesion_actual": sesion_actual,
            "codigos_restantes": codigos_restantes(db, usuario.id),
            "error": error,
            "pendiente": pendiente,
            "codigos_nuevos": codigos_nuevos,
            "correo_mascarado": _mascarar_email(usuario.email),
            "puede_email": _puede_correo(usuario, estado),
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


# --- Recuperación de contraseña (Fase A4) --------------------------------------
def _recuperar_response(request: Request, *, enviado: bool = False,
                        codigo: int = 200) -> HTMLResponse:
    """Formulario de solicitud; el mensaje es siempre el mismo."""
    return templates.TemplateResponse(
        request, "auth/recuperar.html",
        {"enviado": enviado, "mensaje": RESPUESTA_RECUPERACION},
        status_code=codigo,
    )


@router.get("/recuperar", response_class=HTMLResponse)
def recuperar_form(request: Request, db: Session = Depends(get_db)):
    """Solicitud de restablecimiento (pública)."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    return _recuperar_response(request)


@router.post("/recuperar", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def recuperar(request: Request, db: Session = Depends(get_db),
              identificador: str = Form("")):
    """Envía el enlace si corresponde y responde igual en todos los casos.

    No se distingue cuenta inexistente, inactiva, LDAP o con límite alcanzado:
    mismo código HTTP y mismo texto. El envío real lo decide el servicio.
    """
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    solicitar_reset(db, identificador, ip=_ip(request), user_agent=_agente(request))
    return _recuperar_response(request, enviado=True)


def _restablecer_response(request: Request, *, valido: bool,
                          token: str = "", errores: list[str] | None = None,
                          codigo: int = 200) -> HTMLResponse:
    """Formulario de nueva contraseña o mensaje de enlace inválido."""
    return _sin_cache(templates.TemplateResponse(
        request, "auth/restablecer.html",
        {"valido": valido, "token": token if valido else "",
         "errores": errores or [], "mensaje": _MENSAJE_ENLACE},
        status_code=codigo,
    ))


@router.get("/restablecer", response_class=HTMLResponse)
def restablecer_form(request: Request, token: str = "",
                     db: Session = Depends(get_db)):
    """Valida el enlace del correo antes de pedir la contraseña nueva."""
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    if validar_token_reset(db, token) is None:
        return _restablecer_response(request, valido=False)
    return _restablecer_response(request, valido=True, token=token)


@router.post("/restablecer", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def restablecer(request: Request, db: Session = Depends(get_db),
                token: str = Form(""), nueva: str = Form(""),
                confirmacion: str = Form("")):
    """Aplica la nueva contraseña, revoca sesiones y vuelve al login.

    Con errores de política o confirmación el token **no** se consume: se
    vuelve a pintar el formulario para corregir.
    """
    if not settings.auth_enabled:
        return RedirectResponse("/", status_code=303)
    fila = validar_token_reset(db, token)
    if fila is None:
        return _restablecer_response(request, valido=False, codigo=400)

    errores = restablecer_password(
        db, fila, nueva, confirmacion, ip=_ip(request), user_agent=_agente(request))
    if errores:
        return _restablecer_response(request, valido=True, token=token,
                                     errores=errores)

    respuesta = RedirectResponse("/auth/login?ok=password_reset", status_code=303)
    middleware.borrar_cookie_sesion(respuesta)
    middleware.borrar_cookie_mfa(respuesta)
    return respuesta


# --- Cambio de contraseña ------------------------------------------------------
@router.get("/cambiar-password", response_class=HTMLResponse)
def cambiar_password_form(request: Request, next: str | None = None):
    """Formulario de cambio de contraseña (exige sesión por el gate)."""
    usuario = getattr(request.state, "usuario", None)
    if usuario is None:
        return _redirigir_sin_sesion()
    return templates.TemplateResponse(
        request, "auth/cambiar_password.html",
        {"errores": [], "must_change": bool(usuario.must_change_password),
         "siguiente": middleware.ruta_segura(next, "")},
    )


@router.post("/cambiar-password", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect)])
def cambiar_password_post(request: Request, db: Session = Depends(get_db),
                          actual: str = Form(""), nueva: str = Form(""),
                          confirmacion: str = Form(""),
                          siguiente: str = Form("", alias="next")):
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
            {"errores": errores,
             "must_change": bool(usuario.must_change_password),
             "siguiente": middleware.ruta_segura(siguiente, "")},
            status_code=200,
        )

    # Después del cambio: si el rol aún exige enrolar TOTP, ir a seguridad;
    # si no, volver al destino original (o al inicio) con el aviso de éxito.
    if estado_mfa(db, usuario).requiere_enrolamiento:
        destino = "/auth/seguridad"
    else:
        destino = middleware.ruta_segura(siguiente, "/")
        if destino.startswith("/auth/cambiar-password"):
            destino = "/"
    return RedirectResponse(_con_ok(destino), status_code=303)


# --- Administración de usuarios y auditoría (Fase A6b) -------------------------
# Todas las rutas de esta sección aplican DOS guardas, en este orden:
#
# 1. ``requiere_red_interna``: 403 si ``EET_ADMIN_INTERNAL_ONLY=true`` y el
#    cliente no pertenece a ``EET_INTERNAL_CIDRS`` (ver ``dependencies.py``).
# 2. ``require_permiso(...)``: la matriz de ``app/auth/permissions.py``.
#
# Detrás de un reverse proxy hay que configurar ``EET_TRUSTED_PROXY_IPS``
# (Fase A6a) para que ``request.client.host`` sea la IP real del cliente y no
# la del proxy.
