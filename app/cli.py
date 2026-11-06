"""Administración por línea de comandos (Fases A1, A5 y A6.12).

Comandos:

    uv run python -m app.cli crear-admin --email ... --username ... --nombre ...
    uv run python -m app.cli crear-usuario --email ... --username ... --nombre ... --rol ...
    uv run python -m app.cli seed-usuarios [--con-password]
    uv run python -m app.cli limpiar-sesiones --dias 30
    uv run python -m app.cli ldap-check [--login usuario]
    uv run python -m app.cli ldap-mapeo
    uv run python -m app.cli reenviar-correo [--limit 20]
    uv run python -m app.cli re-cifrar
    uv run python -m app.cli estado-auth

Reglas:

- La contraseña se lee de ``EET_ADMIN_PASSWORD`` (``crear-admin``) o
  ``EET_USER_PASSWORD`` (``crear-usuario``) y, si no están definidas, se pide
  por terminal dos veces. Nunca hay contraseña por defecto y nunca se imprime.
- El comando no crea el esquema: en producción corresponde a ``alembic upgrade
  head`` (el contenedor lo hace al arrancar) y en desarrollo a ``init_db()``.
- Toda alta queda auditada con actor ``cli``.
- ``ldap-check`` **nunca** imprime ``EET_LDAP_BIND_PASSWORD`` ni contraseñas, y
  con ``EET_LDAP_ENABLED=false`` no abre ninguna conexión (sale con 0: no es un
  error, el directorio simplemente no está configurado).
- ``seed-usuarios`` es idempotente: adopta las filas genéricas existentes en
  lugar de duplicarlas y solo genera contraseñas con ``--con-password``.
  Esas contraseñas son temporales, se imprimen una sola vez y no se guardan en
  claro ni se registran en la auditoría.
- Los comandos de operación (``reenviar-correo``, ``re-cifrar``,
  ``estado-auth``) son idempotentes, no escriben secretos en la salida y usan
  códigos de salida explícitos: 0 correcto, 1 con fallos parciales, 2 sin
  configuración de cifrado.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit, mailer
from app.auth.providers import ROLES_PRIORIDAD, LdapProvider
from app.auth.service import generar_password_temporal, limpiar_expiradas
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import (
    SecretBox,
    hash_password,
    secret_box,
    utcnow,
    validar_password,
)
from app.models import (
    AuthToken,
    EmailOutbox,
    MfaTotp,
    RecoveryCode,
    Sesion,
    Usuario,
)
from app.seed_data import USUARIOS_SEMILLA, mfa_policy_para_rol

ROLES = ("admin", "supervision", "cargador", "asistente", "consulta")
LETRAS = ("A", "B", "C")
MFA_POLITICAS = ("disabled", "email", "totp", "any")
# Roles críticos: TOTP obligatorio por política (análisis §2 y §6.4).
MFA_POR_ROL = {"admin": "totp", "supervision": "totp"}


def _password_de_entorno(variable: str) -> str:
    return (os.environ.get(variable) or "").strip()


def _leer_password(variable: str) -> str:
    """Obtiene la contraseña del entorno o de la terminal (nunca por defecto)."""
    desde_entorno = _password_de_entorno(variable)
    if desde_entorno:
        return desde_entorno
    if not sys.stdin.isatty():
        raise SystemExit(
            f"No hay terminal interactiva: defina {variable} con la contraseña."
        )
    primera = getpass.getpass("Contraseña: ")
    segunda = getpass.getpass("Repita la contraseña: ")
    if primera != segunda:
        raise SystemExit("Las contraseñas no coinciden.")
    return primera


def _existe(db: Session, username: str, email: str) -> str | None:
    """Devuelve el campo duplicado, si lo hay (comparación en minúsculas)."""
    fila = db.scalar(
        select(Usuario).where(or_(
            func.lower(Usuario.username) == username.casefold(),
            func.lower(Usuario.email) == email.casefold(),
        )).limit(1)
    )
    if fila is None:
        return None
    if (fila.username or "").casefold() == username.casefold():
        return "username"
    return "email"


def _crear_usuario(db: Session, *, nombre: str, username: str, email: str,
                   rol: str, letra: str | None, password: str,
                   must_change: bool,
                   mfa_policy: str | None = None) -> Usuario:
    if rol not in ROLES:
        raise SystemExit(f"Rol inválido: {rol}. Use uno de: {', '.join(ROLES)}.")
    if letra and letra not in LETRAS:
        raise SystemExit("La letra de terna debe ser A, B o C.")
    if rol == "asistente" and not letra:
        raise SystemExit("El rol asistente exige --letra (A, B o C).")
    if rol != "asistente" and letra:
        raise SystemExit("Solo el rol asistente admite --letra.")
    if mfa_policy is not None and mfa_policy not in MFA_POLITICAS:
        raise SystemExit(
            f"Política 2FA inválida: {mfa_policy}. Use una de: "
            f"{', '.join(MFA_POLITICAS)}.")

    username = username.strip()
    email = email.strip()
    if not username or not email:
        raise SystemExit("Usuario y correo son obligatorios.")

    duplicado = _existe(db, username, email)
    if duplicado:
        raise SystemExit(f"Ya existe una cuenta con ese {duplicado}.")

    errores = validar_password(password, username, email)
    if errores:
        raise SystemExit("Contraseña rechazada:\n- " + "\n- ".join(errores))

    usuario = Usuario(
        nombre=nombre.strip(),
        username=username,
        email=email.casefold(),
        rol=rol,
        letra_terna=letra,
        auth_provider="local",
        password_hash=hash_password(password),
        password_changed_at=utcnow(),
        must_change_password=must_change,
        is_active=True,
        mfa_policy=mfa_policy or MFA_POR_ROL.get(rol, "any"),
        role_source="manual",
    )
    db.add(usuario)
    db.flush()
    audit.registrar(
        db, audit.ACTOR_CLI, "user_created", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"rol": rol, "username": username,
                 "must_change_password": must_change,
                 "mfa_policy": usuario.mfa_policy},
    )
    db.commit()
    return usuario


def _cmd_crear_admin(args: argparse.Namespace) -> int:
    password = _leer_password("EET_ADMIN_PASSWORD")
    with SessionLocal() as db:
        usuario = _crear_usuario(
            db, nombre=args.nombre, username=args.username, email=args.email,
            rol="admin", letra=None, password=password,
            must_change=bool(args.must_change),
        )
        # Se lee dentro de la sesión: tras `commit` los atributos quedan
        # expirados y fuera del bloque ya no hay conexión para refrescarlos.
        print(f"Administrador creado: id={usuario.id} usuario={usuario.username}")
    return 0


def _cmd_crear_usuario(args: argparse.Namespace) -> int:
    password = _leer_password("EET_USER_PASSWORD")
    with SessionLocal() as db:
        usuario = _crear_usuario(
            db, nombre=args.nombre, username=args.username, email=args.email,
            rol=args.rol, letra=args.letra, password=password,
            # Las cuentas creadas por administración deben estrenar contraseña.
            must_change=True,
            mfa_policy=args.mfa_policy,
        )
        print(f"Usuario creado: id={usuario.id} usuario={usuario.username} "
              f"rol={usuario.rol} mfa_policy={usuario.mfa_policy}")
    return 0


class _ConflictoSemilla(Exception):
    """La base no permite aplicar la semilla sin pisar datos de otra cuenta.

    Se lanza antes de modificar nada: el comando informa y sale con 1.
    """


def _usuario_por_username(db: Session, username: str) -> Usuario | None:
    """Busca la cuenta por ``username`` sin distinguir mayúsculas."""
    return db.scalar(
        select(Usuario)
        .where(func.lower(Usuario.username) == username.casefold())
        .order_by(Usuario.id)
        .limit(1)
    )


def _fila_a_adoptar(db: Session, datos: dict) -> Usuario | None:
    """Fila genérica que corresponde a una cuenta nominal, si existe.

    Las semillas históricas no tienen ``username``. Para no duplicar cuentas:

    - asistente: ``rol='asistente'`` con la misma letra de terna y sin usuario;
    - cargador/supervision: la única fila de ese rol sin usuario;
    - admin: la fila con rol ``admin`` sin usuario o con el usuario genérico
      ``admin`` (instalaciones previas); si hay más de una, no se adopta.

    Si hay más de una candidata no se adivina: se aborta con un mensaje claro.
    """
    condiciones = [Usuario.rol == datos["rol"]]
    if datos["rol"] == "asistente":
        condiciones.extend([Usuario.username.is_(None),
                            Usuario.letra_terna == datos["letra_terna"]])
    elif datos["rol"] == "admin":
        condiciones.append(or_(Usuario.username.is_(None),
                               func.lower(Usuario.username) == "admin"))
    else:
        condiciones.append(Usuario.username.is_(None))
    candidatas = db.scalars(
        select(Usuario).where(*condiciones).order_by(Usuario.id)).all()
    if len(candidatas) > 1:
        ids = ", ".join(str(fila.id) for fila in candidatas)
        raise _ConflictoSemilla(
            f"Hay {len(candidatas)} filas genéricas candidatas para "
            f"'{datos['username']}' (ids {ids}); no se adopta ninguna "
            "para no duplicar cuentas.")
    return candidatas[0] if candidatas else None


def _plan_semilla(db: Session) -> list[tuple[dict, Usuario | None]]:
    """Resuelve, sin modificar nada, qué fila recibe cada cuenta nominal.

    Devuelve pares ``(datos, fila)`` con ``fila=None`` cuando hay que crear la
    cuenta. Valida duplicados internos, correos ya ocupados y adopciones
    repetidas antes de aplicar el más mínimo cambio.
    """
    usuarios_vistos: set[str] = set()
    emails_vistos: set[str] = set()
    plan: list[tuple[dict, Usuario | None]] = []
    adoptadas: dict[int, str] = {}

    for datos in USUARIOS_SEMILLA:
        username = datos["username"].casefold()
        email = datos["email"].casefold()
        if username in usuarios_vistos:
            raise _ConflictoSemilla(f"Usuario repetido en la semilla: {username}.")
        if email in emails_vistos:
            raise _ConflictoSemilla(f"Correo repetido en la semilla: {email}.")
        usuarios_vistos.add(username)
        emails_vistos.add(email)

        fila = _usuario_por_username(db, datos["username"])
        if fila is None:
            fila = _fila_a_adoptar(db, datos)
        if fila is not None:
            if fila.id in adoptadas:
                raise _ConflictoSemilla(
                    f"Las cuentas '{adoptadas[fila.id]}' y '{datos['username']}' "
                    f"apuntan a la misma fila (id={fila.id}).")
            adoptadas[fila.id] = datos["username"]

        plan.append((datos, fila))

    for datos, fila in plan:
        otra = db.scalar(
            select(Usuario)
            .where(func.lower(Usuario.email) == datos["email"].casefold())
            .order_by(Usuario.id)
            .limit(1)
        )
        if otra is not None and (fila is None or otra.id != fila.id):
            raise _ConflictoSemilla(
                f"El correo {datos['email']} ya pertenece a la cuenta id={otra.id} "
                f"({otra.username or 'sin usuario'}); no se sobrescribe.")
    return plan


def _password_temporal(username: str, email: str) -> str:
    """Contraseña temporal válida para la política (sin ambigüedades)."""
    for _ in range(5):
        candidata = generar_password_temporal()
        if not validar_password(candidata, username, email):
            return candidata
    raise _ConflictoSemilla(
        f"No se pudo generar una contraseña temporal válida para {username}.")


def _aplicar_semilla(db: Session, plan: list[tuple[dict, Usuario | None]],
                     con_password: bool, reactivar: bool = False) -> list[dict]:
    """Crea o actualiza las cuentas del plan y audita cada cambio.

    Conserva lo que no administra esta semilla: contraseña y TOTP existentes
    (salvo ``--con-password``), códigos de respaldo, bloqueos, último acceso,
    estado activo/inactivo (salvo ``--reactivar``) y cualquier otro dato de
    auditoría del titular. Al cambiar la contraseña revoca las sesiones y los
    tokens pendientes de esa cuenta.
    """
    resultados: list[dict] = []
    for datos, fila in plan:
        creado = fila is None
        if fila is None:
            fila = Usuario(auth_provider="local", is_active=True)
            db.add(fila)
        elif reactivar:
            fila.is_active = True

        fila.nombre = datos["nombre"]
        fila.username = datos["username"]
        fila.email = datos["email"].casefold()
        fila.rol = datos["rol"]
        fila.letra_terna = datos["letra_terna"]
        fila.mfa_policy = mfa_policy_para_rol(datos["rol"])

        password = None
        if con_password:
            password = _password_temporal(fila.username, fila.email)
            fila.password_hash = hash_password(password)
            fila.password_changed_at = utcnow()
            fila.must_change_password = True

        db.flush()  # id disponible para la auditoría y las revocaciones
        if con_password:
            ahora = utcnow()
            db.execute(update(Sesion).where(
                Sesion.usuario_id == fila.id,
                Sesion.revoked_at.is_(None),
            ).values(revoked_at=ahora))
            db.execute(update(AuthToken).where(
                AuthToken.usuario_id == fila.id,
                AuthToken.used_at.is_(None),
            ).values(used_at=ahora))

        audit.registrar(
            db, audit.ACTOR_CLI,
            "user_created" if creado else "user_updated",
            entidad="usuario", entidad_id=fila.id,
            detalle={"rol": fila.rol, "username": fila.username,
                     "mfa_policy": fila.mfa_policy,
                     "temporal_generada": bool(con_password),
                     "reactivado": bool(reactivar and not creado),
                     "sesiones_revocadas": bool(con_password)},
        )
        resultados.append({"id": fila.id, "nombre": fila.nombre,
                           "username": fila.username, "rol": fila.rol,
                           "letra": fila.letra_terna, "creado": creado,
                           "password": password})
    db.commit()
    return resultados


def _tabla_semilla(resultados: list[dict], con_password: bool) -> None:
    """Tabla de ancho fijo. La contraseña, si existe, solo vive en esta salida."""
    encabezados = ["NOMBRE", "USUARIO", "ROL", "LETRA"]
    if con_password:
        encabezados.append("CONTRASEÑA TEMPORAL")
    filas = [encabezados]
    for resultado in resultados:
        fila = [resultado["nombre"], resultado["username"], resultado["rol"],
                resultado["letra"] or "-"]
        if con_password:
            fila.append(resultado["password"] or "-")
        filas.append(fila)
    anchos = [max(len(fila[columna]) for fila in filas)
              for columna in range(len(encabezados))]
    for indice, fila in enumerate(filas):
        linea = "  ".join(celda.ljust(anchos[pos])
                          for pos, celda in enumerate(fila)).rstrip()
        print(linea)
        if indice == 0:
            print("  ".join("-" * ancho for ancho in anchos))


def _cmd_seed_usuarios(args: argparse.Namespace) -> int:
    """Aprovisiona las cinco cuentas nominales de forma idempotente."""
    con_password = bool(args.con_password)
    try:
        with SessionLocal() as db:
            plan = _plan_semilla(db)
            resultados = _aplicar_semilla(
                db, plan, con_password,
                reactivar=bool(getattr(args, "reactivar", False)))
    except _ConflictoSemilla as exc:
        print(f"Error: {exc}")
        print("No se modificó ninguna cuenta.")
        return 1

    creadas = sum(1 for fila in resultados if fila["creado"])
    print(f"Cuentas semilla procesadas: {len(resultados)} "
          f"(creadas {creadas} · actualizadas {len(resultados) - creadas})")
    print()
    _tabla_semilla(resultados, con_password)
    print()
    if con_password:
        print("Las contraseñas son temporales y se muestran solo en esta salida: "
              "no se guardan en claro ni en la auditoría.")
        print("Entréguelas por un canal seguro; el primer acceso exige cambiarlas "
              "(must_change_password=true).")
        print("Repetir el comando con --con-password genera contraseñas nuevas.")
    else:
        print("No se modificaron contraseñas: se asignan desde "
              "/auth/usuarios → «Generar contraseña temporal».")
    return 0


def _cmd_limpiar_sesiones(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        borradas = limpiar_expiradas(db, dias=args.dias)
    print(f"Sesiones eliminadas: {borradas}")
    return 0


def _modo_tls() -> str:
    url = (settings.ldap_url or "").casefold()
    if url.startswith("ldaps://"):
        return "LDAPS (TLS directo)"
    if settings.ldap_starttls:
        return "StartTLS sobre ldap://"
    return "sin TLS (no recomendado)"


def _cmd_ldap_check(args: argparse.Namespace) -> int:
    """Diagnóstico del directorio; nunca imprime la contraseña de servicio."""
    if not settings.ldap_enabled:
        print("Directorio deshabilitado (EET_LDAP_ENABLED=false): "
              "no se abrió ninguna conexión.")
        print("Para habilitarlo, complete las variables EET_LDAP_* y ponga "
              "EET_LDAP_ENABLED=true; luego reinicie el servicio.")
        return 0

    print("Diagnóstico del directorio LDAP/Active Directory")
    print(f"  URL:              {settings.ldap_url or '(sin definir)'}")
    print(f"  Base DN:          {settings.ldap_base_dn or '(sin definir)'}")
    print(f"  Cuenta servicio:  {settings.ldap_bind_dn or '(sin definir)'}")
    print(f"  Filtro usuario:   {settings.ldap_user_filter}")
    print(f"  Transporte:       {_modo_tls()}")
    print(f"  Atributos:        login={settings.ldap_attr_login} "
          f"nombre={settings.ldap_attr_nombre} email={settings.ldap_attr_email} "
          f"grupos={settings.ldap_group_attr}")
    print()

    diagnostico = LdapProvider().diagnostico(args.login)
    for paso in diagnostico.pasos:
        marca = "OK   " if paso.ok else "ERROR"
        detalle = f": {paso.detalle}" if paso.detalle else ""
        print(f"  [{marca}] {paso.nombre}{detalle}")

    if diagnostico.grupos:
        print("  Grupos de ejemplo: " + ", ".join(diagnostico.grupos))
    if diagnostico.usuario:
        print()
        print("  Usuario consultado:")
        grupos = diagnostico.usuario["grupos"]
        print("    Grupos: "
              + (", ".join(grupos[:10]) if grupos else "(sin grupos)"))
        print(f"    Rol resultante: "
              f"{diagnostico.usuario['rol'] or 'sin rol (acceso denegado)'}")

    print()
    if diagnostico.ok:
        print("Resultado: configuración operativa. Puede activar el acceso "
              "LDAP con EET_LDAP_ENABLED=true.")
        return 0
    print("Resultado: revise los errores anteriores. El acceso LDAP queda "
          "denegado mientras falle el diagnóstico.")
    return 1


def _cmd_ldap_mapeo(args: argparse.Namespace) -> int:
    """Muestra el mapa grupo→rol configurado (sin secretos)."""
    try:
        mapa = settings.ldap_role_map_dict
    except ValueError as exc:
        print(f"Error en EET_LDAP_ROLE_MAP: {exc}")
        return 1

    print("Mapa de grupos LDAP -> rol (EET_LDAP_ROLE_MAP):")
    if not mapa:
        print("  (sin grupos configurados: todo acceso LDAP queda denegado)")
    for grupo in sorted(mapa):
        print(f"  {grupo}  ->  {mapa[grupo]}")
    print()
    print("Prioridad con varios grupos: " + " > ".join(ROLES_PRIORIDAD))
    print("Rol por defecto sin coincidencia: "
          + (settings.ldap_default_role or "(denegar)"))
    print("Aprovisionamiento automático: "
          + ("activado" if settings.ldap_auto_provision else "desactivado"))
    print("Estado del directorio: "
          + ("habilitado" if settings.ldap_enabled else "deshabilitado"))
    return 0


# --- Operación (A6.12) ----------------------------------------------------------
#: Marca de los cuerpos cifrados en ``email_outbox`` (misma que el mailer).
PREFIJO_CIFRADO = mailer.PREFIJO_CIFRADO


def _recifrar_blob(caja: SecretBox, blob: bytes,
                   key_id: str) -> tuple[str, bytes | None, str | None]:
    """Re-cifra un blob con la clave vigente.

    Devuelve ``(estado, nuevo_blob, nuevo_key_id)`` donde ``estado`` es:

    - ``"actual"``: ya usa la clave vigente (no se toca).
    - ``"migrado"``: se descifró con la clave antigua y se volvió a cifrar.
    - ``"fallo"``: la clave no está disponible o el blob está corrupto
      (no se pierde la fila: se informa el conteo).
    """
    if key_id == caja.key_id:
        # Verifica que el blob actual realmente descifra: un blob corrupto no
        # debe pasar como "actual" solo porque su key_id coincide.
        try:
            caja.decrypt(blob, key_id)
        except (RuntimeError, ValueError):
            return "fallo", None, None
        return "actual", None, None
    try:
        claro = caja.decrypt(blob, key_id)
    except (RuntimeError, ValueError):
        return "fallo", None, None
    nuevo_blob, nuevo_key_id = caja.encrypt(claro)
    return "migrado", nuevo_blob, nuevo_key_id


def _recifrar_cuerpo(caja: SecretBox, cuerpo: str) -> tuple[str, str | None]:
    """Re-cifra un cuerpo ``enc:<key_id>:<token>`` de la cola de correo.

    Los cuerpos que no están cifrados (``"ajeno"``) se dejan intactos: son
    correos sin datos sensibles y su formato es el sobre JSON en claro.
    """
    if not cuerpo or not cuerpo.startswith(PREFIJO_CIFRADO):
        return "ajeno", None
    resto = cuerpo[len(PREFIJO_CIFRADO):]
    identificador, separador, token = resto.partition(":")
    if not separador or not identificador or not token:
        return "fallo", None
    if identificador == caja.key_id:
        try:
            caja.decrypt(token.encode("ascii"), identificador)
        except (RuntimeError, ValueError, UnicodeEncodeError):
            return "fallo", None
        return "actual", None
    try:
        claro = caja.decrypt(token.encode("ascii"), identificador)
    except (RuntimeError, ValueError, UnicodeEncodeError):
        return "fallo", None
    nuevo_blob, nuevo_key_id = caja.encrypt(claro)
    return "migrado", f"{PREFIJO_CIFRADO}{nuevo_key_id}:{nuevo_blob.decode('ascii')}"


def _cmd_reenviar_correo(args: argparse.Namespace) -> int:
    """Reintenta los correos pendientes de la cola (job manual o por cron)."""
    with SessionLocal() as db:
        resumen = mailer.reintentar_pendientes(db, limit=args.limit)
    print(f"Correos revisados: {resumen['revisados']} · "
          f"enviados: {resumen['enviados']} · fallidos: {resumen['fallidos']}")
    if resumen["revisados"] == 0:
        print("No hay correos pendientes en la cola.")
    elif resumen["fallidos"]:
        print("Quedan correos fallidos: revise el relay en "
              "docs/runbook-autenticacion.md §10.1.")
    return 0 if resumen["fallidos"] == 0 else 1


def _cmd_re_cifrar(args: argparse.Namespace) -> int:
    """Re-cifra secretos TOTP y cuerpos de la cola con la clave vigente."""
    try:
        caja = secret_box()
    except (RuntimeError, ValueError) as exc:
        print(f"No hay claves de cifrado utilizables: {exc}")
        return 2

    migrados = actuales = fallos = 0
    with SessionLocal() as db:
        for fila in db.scalars(select(MfaTotp)):
            estado, blob, key_id = _recifrar_blob(caja, fila.secret_enc,
                                                  fila.key_id)
            if estado == "migrado":
                fila.secret_enc = blob
                fila.key_id = key_id
                migrados += 1
            elif estado == "actual":
                actuales += 1
            else:
                fallos += 1
        for fila in db.scalars(select(EmailOutbox).where(
                EmailOutbox.cuerpo.startswith(PREFIJO_CIFRADO))):
            estado, nuevo = _recifrar_cuerpo(caja, fila.cuerpo)
            if estado == "migrado":
                fila.cuerpo = nuevo
                migrados += 1
            elif estado == "actual":
                actuales += 1
            else:
                fallos += 1
        db.commit()

    print(f"Clave vigente: {caja.key_id} · disponibles: "
          f"{', '.join(caja.key_ids)}")
    print(f"Re-cifrados: {migrados} · ya en la clave vigente: {actuales} · "
          f"fallos: {fallos}")
    if fallos:
        print("Los fallos conservan su cifrado original: no se perdió ningún "
              "dato. Verifique que la clave anterior siga en EET_FERNET_KEYS.")
    return 0 if fallos == 0 else 1


def _cmd_estado_auth(args: argparse.Namespace) -> int:
    """Resumen operativo de identidad, sesiones y correo (sin secretos)."""
    ahora = utcnow()
    with SessionLocal() as db:
        por_rol = db.execute(
            select(Usuario.rol, func.count())
            .group_by(Usuario.rol).order_by(Usuario.rol)).all()
        por_proveedor = db.execute(
            select(Usuario.auth_provider, func.count())
            .group_by(Usuario.auth_provider)
            .order_by(Usuario.auth_provider)).all()
        total = db.scalar(select(func.count()).select_from(Usuario)) or 0
        activos = db.scalar(
            select(func.count()).select_from(Usuario)
            .where(Usuario.is_active.is_(True))) or 0

        sesiones = db.scalar(
            select(func.count()).select_from(Sesion).where(
                Sesion.revoked_at.is_(None),
                Sesion.expires_at > ahora)) or 0

        desafios = db.scalar(
            select(func.count()).select_from(AuthToken).where(
                AuthToken.purpose == "mfa",
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > ahora)) or 0

        respaldo = db.scalar(
            select(func.count()).select_from(RecoveryCode).where(
                RecoveryCode.used_at.is_(None))) or 0

        pendientes = db.scalar(
            select(func.count()).select_from(EmailOutbox).where(
                EmailOutbox.enviado_en.is_(None),
                EmailOutbox.intentos < mailer.MAX_INTENTOS)) or 0
        agotados = db.scalar(
            select(func.count()).select_from(EmailOutbox).where(
                EmailOutbox.enviado_en.is_(None),
                EmailOutbox.intentos >= mailer.MAX_INTENTOS)) or 0

        ultimo = db.scalar(select(func.max(Usuario.last_login_at)))

    orden = {rol: posicion for posicion, rol in enumerate(ROLES)}
    roles = sorted(por_rol, key=lambda fila: (orden.get(fila[0], len(orden)),
                                              fila[0] or ""))

    marca = ahora.strftime("%Y-%m-%d %H:%M")
    print(f"Estado de autenticación (UTC · {marca})")
    print(f"  Usuarios: {total} (activos {activos} · inactivos {total - activos})")
    print("    por rol:       " + (" · ".join(f"{rol} {n}" for rol, n in roles)
                                   or "(sin usuarios)"))
    print("    por proveedor: " + (" · ".join(f"{prov} {n}"
                                              for prov, n in por_proveedor)
                                   or "(sin usuarios)"))
    print(f"  Sesiones activas: {sesiones}")
    print(f"  Desafíos 2FA pendientes: {desafios}")
    print(f"  Códigos de respaldo sin usar: {respaldo}")
    print(f"  Correo: {pendientes} pendiente(s) · {agotados} agotado(s)")
    print("  Último acceso: "
          + (ultimo.strftime("%Y-%m-%d %H:%M") if ultimo else "(sin accesos)"))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Administración del Validador EET (Fases A1-A6).",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    admin = sub.add_parser("crear-admin", help="Crea una cuenta administradora local.")
    admin.add_argument("--email", required=True)
    admin.add_argument("--username", required=True)
    admin.add_argument("--nombre", required=True)
    admin.add_argument("--must-change", action="store_true",
                       help="Exige cambiar la contraseña en el primer acceso.")
    admin.set_defaults(func=_cmd_crear_admin)

    usuario = sub.add_parser("crear-usuario", help="Crea una cuenta de operación.")
    usuario.add_argument("--email", required=True)
    usuario.add_argument("--username", required=True)
    usuario.add_argument("--nombre", required=True)
    usuario.add_argument("--rol", required=True, choices=ROLES)
    usuario.add_argument("--letra", choices=LETRAS,
                         help="Letra de terna; obligatoria para el rol asistente.")
    usuario.add_argument("--mfa-policy", choices=MFA_POLITICAS, default=None,
                         dest="mfa_policy",
                         help="Segundo factor exigido: disabled | email | totp | any. "
                              "Por defecto «any», salvo los roles críticos "
                              "(admin, supervisión), que quedan en «totp».")
    usuario.set_defaults(func=_cmd_crear_usuario)

    semilla = sub.add_parser(
        "seed-usuarios",
        help="Crea o completa las cuentas semilla nominales (idempotente).")
    semilla.add_argument(
        "--con-password", action="store_true", dest="con_password",
        help="Genera una contraseña temporal nueva para cada cuenta (también "
             "si ya tenía una), revoca sus sesiones y la muestra una sola vez.")
    semilla.add_argument(
        "--reactivar", action="store_true",
        help="Reactiva cuentas semilla deshabilitadas. Sin esta bandera se "
             "conserva el estado activo/inactivo actual.")
    semilla.set_defaults(func=_cmd_seed_usuarios)

    limpiar = sub.add_parser("limpiar-sesiones",
                             help="Borra sesiones muertas hace más de N días.")
    limpiar.add_argument("--dias", type=int, default=30)
    limpiar.set_defaults(func=_cmd_limpiar_sesiones)

    ldap = sub.add_parser(
        "ldap-check",
        help="Diagnostica conexión, bind, base y mapeo de grupos LDAP.")
    ldap.add_argument("--login", default=None,
                      help="Usuario a consultar (opcional): muestra sus grupos "
                           "y el rol resultante.")
    ldap.set_defaults(func=_cmd_ldap_check)

    mapeo = sub.add_parser(
        "ldap-mapeo",
        help="Muestra el mapa grupo LDAP -> rol configurado (sin secretos).")
    mapeo.set_defaults(func=_cmd_ldap_mapeo)

    reenvio = sub.add_parser(
        "reenviar-correo",
        help="Reintenta los correos pendientes de la cola de salida.")
    reenvio.add_argument("--limit", type=int, default=20,
                         help="Máximo de correos a reintentar (por defecto 20).")
    reenvio.set_defaults(func=_cmd_reenviar_correo)

    recifrar = sub.add_parser(
        "re-cifrar",
        help="Re-cifra secretos TOTP y cuerpos de la cola con la clave vigente.")
    recifrar.set_defaults(func=_cmd_re_cifrar)

    estado = sub.add_parser(
        "estado-auth",
        help="Resumen de cuentas, sesiones y correo (sin secretos).")
    estado.set_defaults(func=_cmd_estado_auth)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.comando == "limpiar-sesiones" and args.dias < 0:
        raise SystemExit("--dias no puede ser negativo.")
    if args.comando == "reenviar-correo" and args.limit < 1:
        raise SystemExit("--limit debe ser positivo.")
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - punto de entrada
    raise SystemExit(main())
