"""Rutas de administración de usuarios y auditoría."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import csrf
from app.auth.dependencies import require_permiso, requiere_red_interna
from app.auth.permissions import ROL_ETIQUETAS
from app.auth.router_common import _agente, _ip, _sin_cache
from app.auth.service import (
    LETRAS_TERNA,
    PAGINA_AUDITORIA,
    POLITICAS_MFA,
    ROLES_USUARIO,
    cambiar_estado_usuario,
    cambiar_rol_usuario,
    crear_usuario_admin,
    exportar_auditoria,
    listar_auditoria,
    listar_usuarios,
    reset_mfa_usuario,
    reset_password_usuario,
)
from app.core.database import get_db
from app.models import Usuario
from app.web import templates

router = APIRouter()

def _guardas(*permisos: str) -> list:
    """Lista de dependencias: red interna primero, permiso después."""
    return [Depends(requiere_red_interna), Depends(require_permiso(*permisos))]


def _usuario_actual(request: Request) -> Usuario | None:
    """Usuario del gate (``None`` con la bandera de autenticación apagada)."""
    return getattr(request.state, "usuario", None)


def _contexto_usuarios(request: Request, db: Session, *, errores: list[str] | tuple[str, ...] = (),
                       password_temporal: str | None = None,
                       password_para: str = "",
                       usuario_creado: Usuario | None = None,
                       form: dict | None = None, abrir_alta: bool = False,
                       codigo: int = 200) -> HTMLResponse:
    """Pinta ``auth/usuarios.html`` conservando filtros y valores del alta."""
    q = (request.query_params.get("q") or "").strip()
    rol = (request.query_params.get("rol") or "").strip()
    activo = (request.query_params.get("activo") or "").strip()
    datos = listar_usuarios(
        db, q=q, rol=rol, activo=activo,
        page=request.query_params.get("page") or 1)
    return _sin_cache(templates.TemplateResponse(
        request, "auth/usuarios.html",
        {
            "proyecto": None,
            "migas": [{"label": "Inicio", "href": "/"}, {"label": "Usuarios"}],
            "filas": datos.filas,
            "total": datos.total,
            "paginas": datos.paginas,
            "page": datos.page,
            "conteos": datos.conteos,
            "roles": ROLES_USUARIO,
            "etiquetas_rol": ROL_ETIQUETAS,
            "letras": LETRAS_TERNA,
            "politicas": POLITICAS_MFA,
            "f_q": q,
            "f_rol": rol,
            "f_activo": activo,
            "errores": list(errores),
            "password_temporal": password_temporal,
            "password_para": password_para,
            "usuario_creado": usuario_creado,
            "form": form or {},
            "abrir_alta": abrir_alta,
        },
        status_code=codigo,
    ))


@router.get("/usuarios", response_class=HTMLResponse,
            dependencies=_guardas("usuarios.gestionar"))
def usuarios_listar(request: Request, db: Session = Depends(get_db)):
    """Listado de cuentas con filtros, contadores y acciones por fila."""
    return _contexto_usuarios(request, db)


@router.post("/usuarios", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect),
                           *_guardas("usuarios.gestionar")])
def usuarios_crear(request: Request, db: Session = Depends(get_db),
                   nombre: str = Form(""), username: str = Form(""),
                   email: str = Form(""), rol: str = Form(""),
                   letra: str = Form(""), mfa_policy: str = Form(""),
                   password: str = Form(""), confirmacion: str = Form(""),
                   generar_password: str | None = Form(None)):
    """Alta local desde la administración (sin redirección si hay que mostrar la clave)."""
    valores = {"nombre": nombre, "username": username, "email": email,
               "rol": rol, "letra": letra, "mfa_policy": mfa_policy}
    try:
        resultado = crear_usuario_admin(
            db, actor=_usuario_actual(request), nombre=nombre,
            username=username, email=email, rol=rol, letra=letra,
            mfa_policy=mfa_policy, password=password,
            confirmacion=confirmacion, generar_password=bool(generar_password),
            ip=_ip(request), user_agent=_agente(request),
        )
    except IntegrityError:
        # Carrera entre dos altas simultáneas: el índice único manda.
        db.rollback()
        return _contexto_usuarios(
            request, db, errores=("Ya existe una cuenta con ese usuario o correo.",),
            form=valores, abrir_alta=True)

    if not resultado.ok:
        return _contexto_usuarios(
            request, db, errores=resultado.errores, form=valores,
            abrir_alta=True)

    # La contraseña temporal (si la generó el sistema) se muestra UNA sola vez
    # en esta respuesta: no viaja en la URL ni se guarda en claro.
    return _contexto_usuarios(
        request, db, usuario_creado=resultado.usuario,
        password_temporal=resultado.password,
        password_para=resultado.usuario.nombre)


@router.post("/usuarios/{usuario_id}/rol", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect),
                           *_guardas("usuarios.gestionar")])
def usuarios_rol(request: Request, usuario_id: int,
                 db: Session = Depends(get_db), rol: str = Form(""),
                 letra: str = Form(""), motivo: str = Form("")):
    """Cambia el rol con motivo obligatorio; nunca sobre la propia cuenta."""
    usuario, errores = cambiar_rol_usuario(
        db, _usuario_actual(request), usuario_id, rol, motivo, letra=letra,
        ip=_ip(request), user_agent=_agente(request))
    if usuario is None:
        raise HTTPException(status_code=404, detail="La cuenta indicada no existe.")
    if errores:
        return _contexto_usuarios(request, db, errores=errores)
    return RedirectResponse("/auth/usuarios?ok=usuario_actualizado",
                            status_code=303)


@router.post("/usuarios/{usuario_id}/estado", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect),
                           *_guardas("usuarios.gestionar")])
def usuarios_estado(request: Request, usuario_id: int,
                    db: Session = Depends(get_db), activo: str = Form(""),
                    motivo: str = Form("")):
    """Activa o desactiva una cuenta; desactivar revoca sus sesiones."""
    if activo not in ("0", "1"):
        return _contexto_usuarios(
            request, db, errores=("El estado solicitado no es válido.",))
    usuario, errores = cambiar_estado_usuario(
        db, _usuario_actual(request), usuario_id, activo == "1", motivo,
        ip=_ip(request), user_agent=_agente(request))
    if usuario is None:
        raise HTTPException(status_code=404, detail="La cuenta indicada no existe.")
    if errores:
        return _contexto_usuarios(request, db, errores=errores)
    return RedirectResponse("/auth/usuarios?ok=usuario_actualizado",
                            status_code=303)


@router.post("/usuarios/{usuario_id}/reset-password", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect),
                           *_guardas("usuarios.gestionar")])
def usuarios_reset_password(request: Request, usuario_id: int,
                            db: Session = Depends(get_db),
                            motivo: str = Form("")):
    """Genera una contraseña temporal y la muestra una única vez (sin 303).

    No se redirige a propósito: la clave solo existe en esta respuesta. Si se
    recarga la página, el navegador repite el POST y se emite otra clave (la
    anterior deja de valer); el aviso de la vista lo advierte.
    """
    usuario, errores, temporal = reset_password_usuario(
        db, _usuario_actual(request), usuario_id, motivo,
        ip=_ip(request), user_agent=_agente(request))
    if usuario is None:
        raise HTTPException(status_code=404, detail="La cuenta indicada no existe.")
    if errores:
        return _contexto_usuarios(request, db, errores=errores)
    return _contexto_usuarios(
        request, db, password_temporal=temporal, password_para=usuario.nombre)


@router.post("/usuarios/{usuario_id}/reset-mfa", response_class=HTMLResponse,
             dependencies=[Depends(csrf.csrf_protect),
                           *_guardas("usuarios.gestionar")])
def usuarios_reset_mfa(request: Request, usuario_id: int,
                       db: Session = Depends(get_db), motivo: str = Form("")):
    """Borra TOTP y códigos de respaldo; el próximo acceso fuerza el enrolamiento."""
    usuario, errores = reset_mfa_usuario(
        db, _usuario_actual(request), usuario_id, motivo,
        ip=_ip(request), user_agent=_agente(request))
    if usuario is None:
        raise HTTPException(status_code=404, detail="La cuenta indicada no existe.")
    if errores:
        return _contexto_usuarios(request, db, errores=errores)
    return RedirectResponse("/auth/usuarios?ok=mfa_reseteado", status_code=303)


def _contexto_auditoria(request: Request, db: Session, *,
                        codigo: int = 200) -> HTMLResponse:
    """Pinta ``auth/auditoria.html`` con los filtros y el enlace CSV."""
    accion = (request.query_params.get("accion") or "").strip()
    actor = (request.query_params.get("actor") or "").strip()
    desde = (request.query_params.get("desde") or "").strip()
    hasta = (request.query_params.get("hasta") or "").strip()
    q = (request.query_params.get("q") or "").strip()
    datos = listar_auditoria(
        db, accion=accion, actor=actor, desde=desde, hasta=hasta, q=q,
        page=request.query_params.get("page") or 1,
        por_pagina=PAGINA_AUDITORIA)
    parametros = {clave: valor for clave, valor in (
        ("accion", accion), ("actor", actor), ("desde", desde),
        ("hasta", hasta), ("q", q)) if valor}
    csv_url = "/auth/auditoria.csv"
    if parametros:
        csv_url = f"{csv_url}?{urlencode(parametros)}"
    return _sin_cache(templates.TemplateResponse(
        request, "auth/auditoria.html",
        {
            "proyecto": None,
            "migas": [{"label": "Inicio", "href": "/"}, {"label": "Auditoría"}],
            "filas": datos.filas,
            "total": datos.total,
            "paginas": datos.paginas,
            "page": datos.page,
            "por_pagina": PAGINA_AUDITORIA,
            "f_accion": accion,
            "f_actor": actor,
            "f_desde": desde,
            "f_hasta": hasta,
            "f_q": q,
            "csv_url": csv_url,
        },
        status_code=codigo,
    ))


@router.get("/auditoria", response_class=HTMLResponse,
            dependencies=_guardas("auditoria.ver"))
def auditoria_listar(request: Request, db: Session = Depends(get_db)):
    """Vista de auditoría: filtros, paginación y detalle por evento."""
    return _contexto_auditoria(request, db)


@router.get("/auditoria.csv",
            dependencies=_guardas("auditoria.ver"))
def auditoria_csv(request: Request, db: Session = Depends(get_db)):
    """Exporta hasta 10 000 filas del filtro actual con BOM UTF-8 para Excel."""
    texto = exportar_auditoria(
        db,
        accion=request.query_params.get("accion"),
        actor=request.query_params.get("actor"),
        desde=request.query_params.get("desde"),
        hasta=request.query_params.get("hasta"),
        q=request.query_params.get("q"),
    )
    return Response(
        content=("\ufeff" + texto).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=auditoria_eet.csv",
            "Cache-Control": "no-store",
        },
    )
