"""Administración de usuarios, roles y exportación de auditoría."""
import csv
import io
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit
from app.core.security import hash_password, utcnow, validar_password
from app.models import (
    Auditoria,
    AuthToken,
    MfaEmailCode,
    MfaTotp,
    RecoveryCode,
    Sesion,
    Usuario,
)
from app.seed_data import mfa_policy_para_rol

POLITICAS_MFA = ("disabled", "email", "totp", "any")

PAGINA_USUARIOS = 25
#: Filas por página de la vista de auditoría.
PAGINA_AUDITORIA = 50
#: Tope de filas de la exportación CSV (la vista no exporta la tabla completa).
MAX_FILAS_CSV_AUDITORIA = 10_000

#: Roles admitidos en el alta y el cambio de rol (matriz §6.4).
ROLES_USUARIO = ("admin", "supervision", "cargador", "asistente", "consulta")
#: Letras de la terna; obligatorias solo para el rol ``asistente``.
LETRAS_TERNA = ("A", "B", "C")
#: Segundo factor por defecto según el rol (análisis §2): los roles críticos
#: nacen con TOTP obligatorio; el resto admite TOTP o correo.
MFA_POR_ROL = {"admin": "totp", "supervision": "totp"}
MFA_POR_DEFECTO = "any"

#: Etiquetas legibles de la política de segundo factor en la tabla de usuarios.
MFA_ETIQUETAS = {
    "totp": "TOTP",
    "email": "Correo",
    "any": "TOTP o correo",
    "disabled": "Deshabilitado",
}

# Alfabeto de las contraseñas temporales: sin caracteres ambiguos (I/O/1/0)
# para que se puedan dictar o transcribir sin errores.
_ALFABETO_TEMPORAL = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "abcdefghijkmnopqrstuvwxyz"
    "23456789"
    "!@#$%*-_"
)
_LARGO_TEMPORAL = 16
#: Usuario admitido: 3–100 caracteres de letras, números, punto, guion y «_».
_PATRON_USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,100}$")
#: Tope del motivo registrado en la auditoría.
_MAX_MOTIVO = 200


@dataclass(frozen=True)
class ResultadoAlta:
    """Resultado de :func:`crear_usuario_admin`.

    ``password`` solo trae valor cuando la contraseña la generó el sistema:
    es el único momento en que existe en claro y la vista la muestra una vez.
    """

    usuario: Usuario | None = None
    errores: tuple[str, ...] = ()
    password: str | None = None

    @property
    def ok(self) -> bool:
        return self.usuario is not None


@dataclass(frozen=True)
class PaginaUsuarios:
    """Página del listado de cuentas con su etiqueta de segundo factor."""

    filas: tuple[tuple[Usuario, str], ...]
    total: int
    paginas: int
    page: int
    conteos: dict[str, int]


@dataclass(frozen=True)
class PaginaAuditoria:
    """Página de la vista de auditoría con el detalle ya serializado."""

    filas: tuple[tuple[Auditoria, str], ...]
    total: int
    paginas: int
    page: int


def generar_password_temporal(largo: int = _LARGO_TEMPORAL) -> str:
    """Contraseña aleatoria de 16 caracteres para entrega presencial.

    Usa ``secrets`` (no ``random``) y un alfabeto sin caracteres ambiguos. No
    depende de la identidad del usuario, así que nunca coincide con su nombre
    de usuario ni con su correo en la práctica; la política se valida igual al
    asignarla.
    """
    tamano = max(12, int(largo))
    return "".join(secrets.choice(_ALFABETO_TEMPORAL) for _ in range(tamano))


def mfa_etiqueta(usuario: Usuario, totp_confirmado: bool) -> str:
    """Texto de la columna «2FA» para la tabla de usuarios."""
    politica = (getattr(usuario, "mfa_policy", None) or MFA_POR_DEFECTO).strip().lower()
    if politica == "totp" and not totp_confirmado:
        return "TOTP (pendiente)"
    if politica == "any" and totp_confirmado:
        return "TOTP o correo (TOTP activo)"
    return MFA_ETIQUETAS.get(politica, politica)


def _motivo(motivo: str | None) -> tuple[str, list[str]]:
    """Normaliza y valida el motivo obligatorio de una acción sensible."""
    texto = (motivo or "").strip()
    if not texto:
        return "", ["Indique el motivo: queda registrado en la auditoría."]
    if len(texto) > _MAX_MOTIVO:
        return texto[:_MAX_MOTIVO], [
            f"El motivo no puede superar los {_MAX_MOTIVO} caracteres."
        ]
    return texto, []


def _admins_activos(db: Session, excepto_id: int | None = None) -> int:
    """Cantidad de administradores activos, opcionalmente excluyendo uno."""
    condiciones = [Usuario.rol == "admin", Usuario.is_active.is_(True)]
    if excepto_id is not None:
        condiciones.append(Usuario.id != excepto_id)
    total = db.scalar(
        select(func.count()).select_from(Usuario).where(*condiciones))
    return int(total or 0)


def _invalidar_acceso(db: Session, usuario_id: int) -> int:
    """Revoca sesiones y anula tokens/códigos pendientes del usuario.

    Devuelve la cantidad de sesiones revocadas. No confirma la transacción: el
    caso de uso la cierra junto con su evento de auditoría.
    """
    ahora = utcnow()
    revocadas = db.execute(
        update(Sesion)
        .where(Sesion.usuario_id == usuario_id, Sesion.revoked_at.is_(None))
        .values(revoked_at=ahora)
    ).rowcount or 0
    db.execute(
        update(AuthToken)
        .where(AuthToken.usuario_id == usuario_id, AuthToken.used_at.is_(None))
        .values(used_at=ahora)
    )
    db.execute(
        update(MfaEmailCode)
        .where(
            MfaEmailCode.usuario_id == usuario_id,
            MfaEmailCode.consumed_at.is_(None),
        )
        .values(consumed_at=ahora)
    )
    return int(revocadas)


def _pagina(valor) -> int:
    """Convierte el parámetro ``page`` en un entero ≥ 1 (tolerante a basura)."""
    try:
        return max(1, int(valor or 1))
    except (TypeError, ValueError):
        return 1


def listar_usuarios(db: Session, *, q: str | None = None, rol: str | None = None,
                    activo: str | None = None, page: int = 1,
                    por_pagina: int = PAGINA_USUARIOS) -> PaginaUsuarios:
    """Cuentas filtradas y paginadas, con los contadores globales por rol.

    ``activo`` acepta ``"1"``, ``"0"`` o vacío. Un rol desconocido se ignora
    (no filtra) en lugar de romper la vista.
    """
    condiciones = []
    texto = (q or "").strip()
    if texto:
        patron = f"%{texto}%"
        condiciones.append(or_(
            Usuario.nombre.ilike(patron),
            Usuario.username.ilike(patron),
            Usuario.email.ilike(patron),
        ))
    if rol in ROLES_USUARIO:
        condiciones.append(Usuario.rol == rol)
    if activo in ("1", "0"):
        condiciones.append(Usuario.is_active.is_(activo == "1"))

    total = int(db.scalar(
        select(func.count()).select_from(Usuario).where(*condiciones)) or 0)
    paginas = max(1, ceil(total / max(1, int(por_pagina))))
    pagina = min(_pagina(page), paginas)
    usuarios = list(db.scalars(
        select(Usuario)
        .where(*condiciones)
        .order_by(Usuario.nombre.asc(), Usuario.id.asc())
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
    ))
    confirmados: set[int] = set()
    if usuarios:
        confirmados = set(db.scalars(
            select(MfaTotp.usuario_id).where(
                MfaTotp.usuario_id.in_([fila.id for fila in usuarios]),
                MfaTotp.confirmed_at.is_not(None),
            )
        ).all())

    conteos = {clave: 0 for clave in ROLES_USUARIO}
    for clave, cantidad in db.execute(
            select(Usuario.rol, func.count()).group_by(Usuario.rol)).all():
        if clave in conteos:
            conteos[clave] = int(cantidad or 0)

    return PaginaUsuarios(
        filas=tuple((fila, mfa_etiqueta(fila, fila.id in confirmados))
                    for fila in usuarios),
        total=total,
        paginas=paginas,
        page=pagina,
        conteos=conteos,
    )


def crear_usuario_admin(db: Session, *, actor: Usuario | None, nombre: str,
                        username: str, email: str, rol: str, letra: str | None,
                        mfa_policy: str | None, password: str,
                        confirmacion: str, generar_password: bool,
                        ip: str | None = None,
                        user_agent: str | None = None) -> ResultadoAlta:
    """Crea una cuenta local desde la administración.

    Devuelve todos los incumplimientos juntos para que el formulario se
    corrija en un solo intento. La cuenta nace con ``must_change_password`` y
    sin segundo factor enrolado: el enrolamiento ocurre en el primer acceso.
    """
    errores: list[str] = []

    nombre_limpio = (nombre or "").strip()
    if not nombre_limpio:
        errores.append("El nombre es obligatorio.")
    elif len(nombre_limpio) > 200:
        errores.append("El nombre no puede superar los 200 caracteres.")

    usuario_limpio = (username or "").strip()
    if not _PATRON_USERNAME.match(usuario_limpio):
        errores.append(
            "El usuario debe tener entre 3 y 100 caracteres y solo admite "
            "letras, números, punto, guion y guion bajo."
        )

    correo_limpio = (email or "").strip()
    try:
        correo_limpio = validate_email(
            correo_limpio, check_deliverability=False).normalized
    except EmailNotValidError:
        errores.append("El correo no tiene un formato válido.")

    rol_limpio = (rol or "").strip().lower()
    if rol_limpio not in ROLES_USUARIO:
        errores.append("El rol indicado no es válido.")

    letra_limpia: str | None = None
    if rol_limpio == "asistente":
        letra_limpia = (letra or "").strip().upper()
        if letra_limpia not in LETRAS_TERNA:
            errores.append("El rol asistente exige letra de terna (A, B o C).")

    politica = (mfa_policy or "").strip().lower()
    if not politica:
        politica = MFA_POR_ROL.get(rol_limpio, MFA_POR_DEFECTO)
    elif politica not in POLITICAS_MFA:
        errores.append("La política de segundo factor no es válida.")
        politica = MFA_POR_DEFECTO
    # Los roles críticos no pueden nacer sin TOTP, ni siquiera si el formulario
    # envía explícitamente "disabled" o "email".
    if rol_limpio in MFA_POR_ROL and politica != "totp":
        errores.append(
            "Los roles Administración y Supervisión exigen TOTP obligatorio; "
            "no se admite otra política de segundo factor."
        )

    if usuario_limpio and correo_limpio:
        duplicados = db.scalars(
            select(Usuario).where(or_(
                func.lower(Usuario.username) == usuario_limpio.casefold(),
                func.lower(Usuario.email) == correo_limpio.casefold(),
            ))
        ).all()
        for fila in duplicados:
            if (fila.username or "").casefold() == usuario_limpio.casefold():
                errores.append(
                    f"Ya existe una cuenta con el usuario «{usuario_limpio}».")
            if (fila.email or "").casefold() == correo_limpio.casefold():
                errores.append(
                    f"Ya existe una cuenta con el correo «{correo_limpio}».")

    temporal: str | None = None
    if generar_password:
        temporal = generar_password_temporal()
        password_limpia = temporal
    else:
        password_limpia = password or ""
        if password_limpia != (confirmacion or ""):
            errores.append("La contraseña y su confirmación no coinciden.")
        errores.extend(validar_password(
            password_limpia, usuario_limpio, correo_limpio))

    if errores:
        return ResultadoAlta(errores=tuple(errores))

    usuario = Usuario(
        nombre=nombre_limpio,
        username=usuario_limpio,
        email=correo_limpio,
        rol=rol_limpio,
        letra_terna=letra_limpia,
        auth_provider="local",
        password_hash=hash_password(password_limpia),
        password_changed_at=utcnow(),
        must_change_password=True,
        is_active=True,
        mfa_policy=politica,
        role_source="manual",
    )
    db.add(usuario)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "user_created", entidad="usuario",
        entidad_id=usuario.id,
        detalle={
            "rol": rol_limpio,
            "username": usuario_limpio,
            "letra_terna": letra_limpia,
            "mfa_policy": politica,
            "must_change_password": True,
            "password_generada": temporal is not None,
        },
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return ResultadoAlta(usuario=usuario, password=temporal)


def cambiar_rol_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                        nuevo_rol: str, motivo: str, *,
                        letra: str | None = None, ip: str | None = None,
                        user_agent: str | None = None,
                        ) -> tuple[Usuario | None, list[str]]:
    """Cambia el rol (y la letra de terna) de una cuenta, con motivo.

    Rechaza el cambio sobre la propia cuenta y la degradación del último
    administrador activo. La cuenta nunca se desactiva aquí.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."]

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)

    rol_limpio = (nuevo_rol or "").strip().lower()
    if rol_limpio not in ROLES_USUARIO:
        errores.append("El rol indicado no es válido.")

    if actor is not None and actor.id == usuario.id:
        errores.append("No puede cambiar su propio rol.")

    letra_limpia: str | None = None
    if rol_limpio == "asistente":
        letra_limpia = (letra or usuario.letra_terna or "").strip().upper()
        if letra_limpia not in LETRAS_TERNA:
            errores.append(
                "El rol asistente requiere letra de terna (A, B o C).")
    else:
        letra_limpia = None

    if (usuario.rol == "admin" and usuario.is_active
            and rol_limpio != "admin"
            and _admins_activos(db, excepto_id=usuario.id) == 0):
        errores.append(
            "No puede quitar el rol al último administrador activo: asigne "
            "otro administrador antes de degradar esta cuenta.")

    if errores:
        return usuario, errores

    anterior, letra_anterior = usuario.rol, usuario.letra_terna
    politica_anterior = usuario.mfa_policy
    cambio_rol = anterior != rol_limpio
    usuario.rol = rol_limpio
    usuario.letra_terna = letra_limpia
    if cambio_rol:
        # La política de segundo factor se deriva del rol: promover a admin o
        # supervisión debe exigir TOTP aunque la cuenta se haya creado sin él.
        usuario.mfa_policy = mfa_policy_para_rol(rol_limpio)
        # Un rol impuesto por un administrador deja de ser propiedad del
        # directorio: si no, el siguiente login LDAP lo revertiría.
        usuario.role_source = "manual"
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "role_changed", entidad="usuario",
        entidad_id=usuario.id,
        detalle={
            "de": anterior,
            "a": rol_limpio,
            "letra_anterior": letra_anterior,
            "letra": letra_limpia,
            "mfa_policy_anterior": politica_anterior,
            "mfa_policy": usuario.mfa_policy,
            "role_source": usuario.role_source,
            "motivo": motivo_limpio,
        },
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, []


def cambiar_estado_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                           activar: bool, motivo: str, *,
                           ip: str | None = None,
                           user_agent: str | None = None,
                           ) -> tuple[Usuario | None, list[str]]:
    """Activa o desactiva una cuenta (baja lógica) con motivo.

    Desactivar revoca todas las sesiones y anula tokens y códigos pendientes.
    Activar limpia el bloqueo por intentos fallidos para que la cuenta pueda
    volver a entrar sin esperar la ventana de 15 minutos.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."]

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)

    if not activar and actor is not None and actor.id == usuario.id:
        errores.append("No puede desactivar su propia cuenta.")
    if (not activar and usuario.rol == "admin" and usuario.is_active
            and _admins_activos(db, excepto_id=usuario.id) == 0):
        errores.append(
            "No puede desactivar al último administrador activo: asigne otro "
            "administrador antes de dar de baja esta cuenta.")
    if usuario.is_active == activar:
        errores.append(
            "La cuenta ya está activa." if activar else "La cuenta ya está inactiva.")

    if errores:
        return usuario, errores

    usuario.is_active = activar
    revocadas = 0
    if activar:
        usuario.failed_attempts = 0
        usuario.locked_until = None
    else:
        revocadas = _invalidar_acceso(db, usuario.id)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor),
        "user_enabled" if activar else "user_disabled",
        entidad="usuario", entidad_id=usuario.id,
        detalle={"motivo": motivo_limpio, "sesiones_revocadas": revocadas},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, []


def reset_password_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                           motivo: str, *, ip: str | None = None,
                           user_agent: str | None = None,
                           ) -> tuple[Usuario | None, list[str], str | None]:
    """Asigna una contraseña temporal y devuelve su valor en claro una vez.

    Prohibido sobre la propia cuenta (para eso está el cambio de contraseña) y
    sobre cuentas cuyo proveedor no es local. Revoca sesiones, anula tokens y
    códigos, y exige cambio en el próximo acceso.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."], None

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)
    if actor is not None and actor.id == usuario.id:
        errores.append(
            "No puede restablecer su propia contraseña desde aquí: use "
            "«Cambiar contraseña» en su cuenta.")
    if usuario.auth_provider != "local":
        errores.append(
            "La contraseña de esta cuenta se administra fuera del sistema.")

    if errores:
        return usuario, errores, None

    temporal = generar_password_temporal()
    usuario.password_hash = hash_password(temporal)
    usuario.password_changed_at = utcnow()
    usuario.must_change_password = True
    usuario.failed_attempts = 0
    usuario.locked_until = None
    revocadas = _invalidar_acceso(db, usuario.id)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "password_reset_admin", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"motivo": motivo_limpio, "sesiones_revocadas": revocadas,
                 "temporal": True},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, [], temporal


def reset_mfa_usuario(db: Session, actor: Usuario | None, usuario_id: int,
                      motivo: str, *, ip: str | None = None,
                      user_agent: str | None = None,
                      ) -> tuple[Usuario | None, list[str]]:
    """Reinicia el segundo factor: borra TOTP y códigos de respaldo.

    Prohibido sobre la propia cuenta: un error del administrador dejaría su
    propia cuenta sin el segundo factor que su rol exige. Para la propia cuenta
    existe el flujo de ``/auth/seguridad`` y, si se perdió el teléfono, otro
    administrador ejecuta este reset. Deja ``mfa_enrolled_at`` en NULL para que
    la política TOTP fuerce el re-enrolamiento en el próximo acceso.
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return None, ["La cuenta indicada no existe."]

    errores: list[str] = []
    motivo_limpio, errores_motivo = _motivo(motivo)
    errores.extend(errores_motivo)
    if actor is not None and actor.id == usuario.id:
        errores.append(
            "No puede reiniciar su propio segundo factor desde aquí: solicite "
            "el reset a otro administrador.")

    if errores:
        return usuario, errores

    fila_totp = db.get(MfaTotp, usuario.id)
    tenia_totp = fila_totp is not None
    if fila_totp is not None:
        db.delete(fila_totp)
    codigos = db.execute(
        delete(RecoveryCode).where(RecoveryCode.usuario_id == usuario.id)
    ).rowcount or 0
    usuario.mfa_enrolled_at = None
    revocadas = _invalidar_acceso(db, usuario.id)
    db.flush()
    audit.registrar(
        db, audit.actor_de(actor), "mfa_reset_admin", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"motivo": motivo_limpio, "totp_eliminado": tenia_totp,
                 "codigos_eliminados": int(codigos),
                 "sesiones_revocadas": revocadas},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return usuario, []


# --- Vista y exportación de auditoría (Fase A6b) -------------------------------
def _fecha_filtro(valor: str | None) -> datetime | None:
    """Parsea ``AAAA-MM-DD``; un valor inválido se ignora (no rompe la vista)."""
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:
        return None


def _condiciones_auditoria(accion: str | None, actor: str | None,
                           desde: str | None, hasta: str | None,
                           q: str | None) -> list:
    """Filtros compartidos por la vista y la exportación CSV."""
    condiciones = []
    if (accion or "").strip():
        condiciones.append(Auditoria.accion.ilike(f"%{accion.strip()}%"))
    if (actor or "").strip():
        condiciones.append(Auditoria.actor.ilike(f"%{actor.strip()}%"))
    desde_dt = _fecha_filtro(desde)
    hasta_dt = _fecha_filtro(hasta)
    if desde_dt is not None:
        condiciones.append(Auditoria.fecha >= desde_dt)
    if hasta_dt is not None:
        # El filtro «hasta» incluye todo el día indicado.
        condiciones.append(Auditoria.fecha < hasta_dt + timedelta(days=1))
    if (q or "").strip():
        patron = f"%{q.strip()}%"
        condiciones.append(or_(
            Auditoria.entidad.ilike(patron),
            Auditoria.entidad_id.ilike(patron),
        ))
    return condiciones


def _detalle_json(fila: Auditoria) -> str:
    """Detalle del evento en JSON legible (sin secretos: se sanearon al escribir)."""
    try:
        return json.dumps(fila.detalle or {}, ensure_ascii=False,
                          indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):  # detalle corrupto: no romper la página
        return "{}"


def listar_auditoria(db: Session, *, accion: str | None = None,
                     actor: str | None = None, desde: str | None = None,
                     hasta: str | None = None, q: str | None = None,
                     page: int = 1,
                     por_pagina: int = PAGINA_AUDITORIA) -> PaginaAuditoria:
    """Eventos de auditoría filtrados y paginados, del más reciente al más viejo."""
    condiciones = _condiciones_auditoria(accion, actor, desde, hasta, q)
    total = int(db.scalar(
        select(func.count()).select_from(Auditoria).where(*condiciones)) or 0)
    paginas = max(1, ceil(total / max(1, int(por_pagina))))
    pagina = min(_pagina(page), paginas)
    filas = list(db.scalars(
        select(Auditoria)
        .where(*condiciones)
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
    ))
    return PaginaAuditoria(
        filas=tuple((fila, _detalle_json(fila)) for fila in filas),
        total=total, paginas=paginas, page=pagina)


def _celda_csv(valor) -> str:
    """Sanea una celda para Excel/CSV: evita la inyección de fórmulas."""
    from app.core.excel import celda_segura

    return str(celda_segura(valor))


def exportar_auditoria(db: Session, *, accion: str | None = None,
                       actor: str | None = None, desde: str | None = None,
                       hasta: str | None = None, q: str | None = None,
                       limite: int = MAX_FILAS_CSV_AUDITORIA) -> str:
    """CSV de la auditoría filtrada (máximo :data:`MAX_FILAS_CSV_AUDITORIA`).

    El BOM UTF-8 lo agrega el router al codificar; aquí solo se genera el
    contenido con ``csv.writer`` (comillas cuando hacen falta y CRLF).
    """
    condiciones = _condiciones_auditoria(accion, actor, desde, hasta, q)
    filas = db.scalars(
        select(Auditoria)
        .where(*condiciones)
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .limit(max(1, int(limite)))
    ).all()

    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    escritor.writerow([
        "fecha", "actor", "accion", "entidad", "entidad_id", "ip",
        "user_agent", "detalle",
    ])
    for fila in filas:
        escritor.writerow([
            _celda_csv(fila.fecha.strftime("%Y-%m-%d %H:%M:%S")
                       if fila.fecha else ""),
            _celda_csv(fila.actor),
            _celda_csv(fila.accion),
            _celda_csv(fila.entidad),
            _celda_csv(fila.entidad_id),
            _celda_csv(fila.ip),
            _celda_csv(fila.user_agent),
            _celda_csv(_detalle_json(fila).replace("\n", " ")),
        ])
    return buffer.getvalue()
