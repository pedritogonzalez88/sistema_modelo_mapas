"""Segundo factor: TOTP, códigos de respaldo y challenges."""
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.auth import audit
from app.auth.common import (
    CODIGOS_RESPALDO,
    MAX_FALLOS_MFA_POR_IP,
    MAX_INTENTOS_MFA,
    VENTANA_RATE_LIMIT,
    VIGENCIA_DESAFIO,
    EstadoMfa,
    ResultadoMfa,
    _ip_corta,
    ip_bloqueada,
)
from app.auth.notifications import _notificar_enrolamiento
from app.auth.sessions import _registrar_intento
from app.core.config import settings
from app.core.security import (
    constant_time_eq,
    generar_codigos_respaldo,
    generar_secreto,
    hmac_code,
    new_token,
    normalizar_codigo,
    secret_box,
    sha256_hex,
    totp_uri,
    utcnow,
    verificar_totp,
)
from app.models import AuthToken, MfaTotp, RecoveryCode, Sesion, Usuario


def _fila_totp(db: Session, usuario_id: int) -> MfaTotp | None:
    """Fila de secreto TOTP del usuario (confirmada o pendiente)."""
    return db.get(MfaTotp, usuario_id)


def secreto_totp(fila: MfaTotp | None) -> str | None:
    """Descifra el secreto de la fila; ``None`` si no se puede leer.

    Un fallo de descifrado (clave rotada y perdida, blob alterado) se trata
    como "sin segundo factor disponible" en lugar de romper la petición: el
    usuario conserva los códigos de respaldo y el reset asistido.
    """
    if fila is None:
        return None
    try:
        return secret_box().decrypt(fila.secret_enc, fila.key_id)
    except (ValueError, RuntimeError):
        return None


def estado_mfa(db: Session, usuario: Usuario | None) -> EstadoMfa:
    """Estado del segundo factor: política, canales disponibles y qué exige.

    Orden de decisión (Fase A4):

    1. TOTP confirmado con política ``totp`` o ``any`` → autenticador.
    2. Política ``email`` o ``any`` y correo registrado → código por correo.
    3. Política ``totp`` sin TOTP → enrolamiento forzado (A3).
    4. Sin canal viable → no se exige segundo factor (no se bloquea el acceso).
    """
    if usuario is None:
        return EstadoMfa()
    policy = (usuario.mfa_policy or "any").strip().lower()
    fila = _fila_totp(db, usuario.id)
    enrolado = bool(fila is not None and fila.confirmed_at is not None)
    correo = bool((usuario.email or "").strip())

    metodo: str | None = None
    if enrolado and policy in ("totp", "any"):
        metodo = "totp"
    elif policy in ("email", "any") and correo:
        metodo = "email"

    return EstadoMfa(
        policy=policy,
        enrolado=enrolado,
        requiere_desafio=metodo is not None,
        requiere_enrolamiento=(policy == "totp" and not enrolado),
        enrolado_desde=(fila.confirmed_at.strftime("%Y-%m-%d %H:%M")
                        if enrolado and fila.confirmed_at else None),
        puede_totp=enrolado,
        puede_email=correo and policy in ("email", "any"),
        metodo=metodo,
    )


def codigos_restantes(db: Session, usuario_id: int) -> int:
    """Cantidad de códigos de respaldo sin usar."""
    total = db.scalar(
        select(func.count()).select_from(RecoveryCode).where(
            RecoveryCode.usuario_id == usuario_id,
            RecoveryCode.used_at.is_(None),
        )
    )
    return int(total or 0)


def _emitir_codigos_respaldo(db: Session, usuario: Usuario) -> list[str]:
    """Invalida los códigos sin usar y emite :data:`CODIGOS_RESPALDO` nuevos.

    Solo se persiste el HMAC con el pepper: ni la base ni los logs permiten
    reconstruir los códigos que se muestran una única vez en pantalla.
    """
    db.execute(
        delete(RecoveryCode).where(
            RecoveryCode.usuario_id == usuario.id,
            RecoveryCode.used_at.is_(None),
        )
    )
    codigos = generar_codigos_respaldo(CODIGOS_RESPALDO)
    ahora = utcnow()
    for codigo in codigos:
        db.add(RecoveryCode(
            usuario_id=usuario.id,
            code_hash=hmac_code(codigo, usuario.id, settings.auth_pepper),
            created_at=ahora,
        ))
    db.flush()
    return codigos


def iniciar_enrolamiento(db: Session, usuario: Usuario) -> tuple[str, str] | None:
    """Genera un secreto TOTP pendiente y devuelve ``(secreto, uri)``.

    Devuelve ``None`` si el usuario ya tiene TOTP confirmado: re-enrolar sin
    verificación previa dejaría la cuenta sin segundo factor si el usuario
    abandona el asistente a mitad de camino (ver ``docs/fase-a3-totp.md``).
    """
    if usuario is None or usuario.id is None:
        return None
    fila = _fila_totp(db, usuario.id)
    if fila is not None and fila.confirmed_at is not None:
        return None

    secreto = generar_secreto()
    blob, key_id = secret_box().encrypt(secreto)
    if fila is None:
        db.add(MfaTotp(
            usuario_id=usuario.id, secret_enc=blob, key_id=key_id,
            confirmed_at=None, last_used_step=None,
        ))
    else:
        # Se reemplaza el pendiente anterior: el usuario pudo haber perdido el
        # QR y vuelve a empezar. Nunca se toca un secreto ya confirmado.
        fila.secret_enc = blob
        fila.key_id = key_id
        fila.confirmed_at = None
        fila.last_used_step = None
    db.commit()
    nombre = usuario.email or usuario.username or "usuario"
    return secreto, totp_uri(secreto, nombre, settings.mfa_issuer)


def pendiente_enrolamiento(db: Session, usuario: Usuario) -> tuple[str, str] | None:
    """``(secreto, uri)`` del enrolamiento pendiente, si lo hay.

    Se usa para volver a pintar el QR cuando la confirmación falla: el secreto
    pendiente no se descarta por un código mal tipeado.
    """
    fila = _fila_totp(db, usuario.id)
    if fila is None or fila.confirmed_at is not None:
        return None
    secreto = secreto_totp(fila)
    if secreto is None:
        return None
    nombre = usuario.email or usuario.username or "usuario"
    return secreto, totp_uri(secreto, nombre, settings.mfa_issuer)


def confirmar_enrolamiento(db: Session, usuario: Usuario, codigo: str, *,
                           sesion: Sesion | None = None, ip: str | None = None,
                           user_agent: str | None = None,
                           ) -> tuple[list[str] | None, str | None]:
    """Activa el TOTP con un código válido del autenticador.

    Devuelve ``(codigos_de_respaldo, error)``; exactamente uno de los dos es
    ``None``. Al confirmar, la sesión en curso queda marcada con ``mfa_at``
    porque el usuario acaba de demostrar el segundo factor.
    """
    fila = _fila_totp(db, usuario.id)
    if fila is None or fila.confirmed_at is not None:
        return None, ("No hay una activación pendiente. Inicie la activación "
                      "del autenticador desde esta misma página.")
    secreto = secreto_totp(fila)
    if secreto is None:
        return None, ("No se pudo leer el secreto pendiente. Inicie de nuevo "
                      "la activación del autenticador.")
    paso = verificar_totp(secreto, codigo, None, settings.mfa_window)
    if paso is None:
        return None, ("El código no es válido. Verifique que el reloj del "
                      "teléfono esté en hora automática e intente otra vez.")

    ahora = utcnow()
    fila.confirmed_at = ahora
    fila.last_used_step = paso
    usuario.mfa_enrolled_at = ahora
    if sesion is not None and sesion.mfa_at is None:
        sesion.mfa_at = ahora
    db.flush()
    codigos = _emitir_codigos_respaldo(db, usuario)
    audit.registrar(
        db, audit.actor_de(usuario), "mfa_enrolled", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"metodo": "totp", "codigos_respaldo": len(codigos)},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    _notificar_enrolamiento(usuario)
    return codigos, None


def regenerar_respaldo(db: Session, usuario: Usuario, codigo: str, *,
                       ip: str | None = None,
                       user_agent: str | None = None,
                       ) -> tuple[list[str] | None, str | None]:
    """Emite 10 códigos nuevos exigiendo un código TOTP vigente.

    El TOTP usado se registra en ``last_used_step``: no se puede reutilizar el
    mismo código para regenerar dos veces.
    """
    fila = _fila_totp(db, usuario.id)
    if fila is None or fila.confirmed_at is None:
        return None, "Primero active el autenticador."
    secreto = secreto_totp(fila)
    if secreto is None:
        return None, ("No se pudo leer el secreto del autenticador; use un "
                      "código de respaldo o solicite un reset asistido.")
    paso = verificar_totp(secreto, codigo, fila.last_used_step, settings.mfa_window)
    if paso is None:
        return None, ("Código incorrecto o ya utilizado. Espere al próximo "
                      "código del autenticador e intente otra vez.")
    fila.last_used_step = paso
    db.flush()
    codigos = _emitir_codigos_respaldo(db, usuario)
    audit.registrar(
        db, audit.actor_de(usuario), "mfa_recovery_regenerated",
        entidad="usuario", entidad_id=usuario.id,
        detalle={"codigos_respaldo": len(codigos)}, ip=ip, user_agent=user_agent,
    )
    db.commit()
    return codigos, None


def consumir_codigo_respaldo(db: Session, usuario: Usuario, codigo: str) -> bool:
    """Consume un código de respaldo válido; ``True`` si se usó uno.

    Se comparan todos los hashes sin cortar el bucle para que el tiempo no
    revele cuál coincidió. El código queda marcado con ``used_at``.
    """
    normalizado = normalizar_codigo(codigo or "")
    if len(normalizado) != 9 or normalizado[4] != "-":
        return False
    esperado = hmac_code(normalizado, usuario.id, settings.auth_pepper)
    filas = db.scalars(
        select(RecoveryCode).where(
            RecoveryCode.usuario_id == usuario.id,
            RecoveryCode.used_at.is_(None),
        )
    ).all()
    elegido: RecoveryCode | None = None
    for fila in filas:
        if constant_time_eq(fila.code_hash, esperado):
            elegido = fila
    if elegido is None:
        return False
    # UPDATE condicional: si otra petición consumió el código primero, rowcount
    # es 0 y esta no lo acepta. Evita carreras de doble uso.
    resultado = db.execute(
        update(RecoveryCode)
        .where(RecoveryCode.id == elegido.id,
               RecoveryCode.used_at.is_(None))
        .values(used_at=utcnow())
    )
    return resultado.rowcount == 1


def _verificar_codigo(db: Session, usuario: Usuario, codigo: str) -> str | None:
    """Valida TOTP (antirreplay) o código de respaldo; devuelve el método."""
    fila = _fila_totp(db, usuario.id)
    if fila is not None and fila.confirmed_at is not None:
        secreto = secreto_totp(fila)
        if secreto:
            paso = verificar_totp(secreto, codigo, fila.last_used_step,
                                  settings.mfa_window)
            if paso is not None:
                # UPDATE condicional: solo el primer uso del paso gana.
                resultado = db.execute(
                    update(MfaTotp)
                    .where(MfaTotp.usuario_id == usuario.id,
                           or_(MfaTotp.last_used_step.is_(None),
                               MfaTotp.last_used_step < paso))
                    .values(last_used_step=paso)
                )
                if resultado.rowcount == 1:
                    return "totp"
    if consumir_codigo_respaldo(db, usuario, codigo):
        return "respaldo"
    return None


# --- Challenge de segundo factor ----------------------------------------------
def crear_desafio(db: Session, usuario: Usuario, ip: str | None = None) -> str:
    """Crea un challenge nuevo y devuelve el token en claro.

    Solo se guarda el ``sha256``. Los challenges anteriores **no** se invalidan:
    un atacante con la contraseña podría crear uno nuevo para dejar fuera al
    titular legítimo (DoS de la cookie MFA). Cada challenge vence a los 5
    minutos y los vencidos se purgan al crear el siguiente.
    """
    if usuario is None or usuario.id is None:
        raise ValueError("El challenge requiere un usuario persistido.")
    ahora = utcnow()
    db.execute(
        delete(AuthToken).where(
            AuthToken.usuario_id == usuario.id,
            AuthToken.purpose == "mfa",
            AuthToken.expires_at < ahora,
        )
    )
    token = new_token(32)
    db.add(AuthToken(
        usuario_id=usuario.id,
        purpose="mfa",
        token_hash=sha256_hex(token),
        expires_at=ahora + VIGENCIA_DESAFIO,
        mfa_attempts=0,
        ip=_ip_corta(ip),
    ))
    db.commit()
    return token


def cargar_desafio(db: Session, token: str | None) -> AuthToken | None:
    """Challenge vigente del token de la cookie ``eet_mfa`` (o ``None``)."""
    if not token:
        return None
    fila = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == sha256_hex(token),
            AuthToken.purpose == "mfa",
        )
    )
    if fila is None or fila.used_at is not None:
        return None
    if fila.expires_at is None or fila.expires_at <= utcnow():
        return None
    return fila


def verificar_desafio(db: Session, desafio: AuthToken | None, codigo: str, *,
                      ip: str | None = None,
                      user_agent: str | None = None) -> ResultadoMfa:
    """Valida el segundo factor y, si acierta, deja el challenge consumido.

    No crea la sesión: eso lo decide el router para poder responder con la
    cookie y el ``next`` ya validado. Cada intento cuenta en
    ``auth_tokens.mfa_attempts``; al llegar a :data:`MAX_INTENTOS_MFA` el
    challenge se invalida y hay que volver a empezar por la contraseña.

    Fase A6a: además del tope del challenge (por cuenta) se aplica uno por IP
    (:data:`MAX_FALLOS_MFA_POR_IP` fallos en 15 minutos), porque un atacante
    puede encadenar challenges de varias cuentas desde el mismo origen. Cada
    fallo queda en ``login_attempts`` con ``factor='mfa'``.
    """
    ahora = utcnow()
    if desafio is None:
        return ResultadoMfa(motivo="ausente")
    # Bloquea el challenge durante la validación: dos peticiones concurrentes
    # no pueden consumir el mismo código ni sobreescribir los intentos.
    bloqueado = db.scalar(
        select(AuthToken)
        .where(AuthToken.id == desafio.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if bloqueado is None:
        return ResultadoMfa(motivo="invalido")
    desafio = bloqueado
    if desafio.used_at is not None or desafio.expires_at is None \
            or desafio.expires_at <= ahora:
        return ResultadoMfa(motivo="invalido")

    usuario = db.get(Usuario, desafio.usuario_id)
    if usuario is None or not usuario.is_active:
        desafio.used_at = ahora
        db.commit()
        return ResultadoMfa(motivo="invalido")

    if ip_bloqueada(db, ip, MAX_FALLOS_MFA_POR_IP, factor="mfa"):
        desafio.used_at = ahora
        audit.registrar(
            db, audit.actor_de(usuario), "mfa_ip_locked", entidad="ip",
            detalle={"motivo": "fallos_por_ip", "maximo": MAX_FALLOS_MFA_POR_IP,
                     "ventana_min": int(VENTANA_RATE_LIMIT.total_seconds() // 60)},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="limite", usuario=usuario)

    desafio.mfa_attempts = int(desafio.mfa_attempts or 0) + 1
    metodo = _verificar_codigo(db, usuario, codigo or "")
    if metodo is not None:
        desafio.used_at = ahora
        audit.registrar(
            db, audit.actor_de(usuario), "mfa_ok", entidad="usuario",
            entidad_id=usuario.id,
            detalle={"metodo": metodo, "intentos": desafio.mfa_attempts},
            ip=ip, user_agent=user_agent,
        )
        db.commit()
        return ResultadoMfa(motivo="ok", usuario=usuario, metodo=metodo)

    limite = desafio.mfa_attempts >= MAX_INTENTOS_MFA
    if limite:
        desafio.used_at = ahora
    _registrar_intento(db, usuario.username or str(usuario.id), ip,
                       exito=False, factor="mfa")
    audit.registrar(
        db, audit.actor_de(usuario), "mfa_fail", entidad="usuario",
        entidad_id=usuario.id,
        detalle={"intentos": desafio.mfa_attempts,
                 "motivo": "limite" if limite else "codigo_invalido"},
        ip=ip, user_agent=user_agent,
    )
    db.commit()
    return ResultadoMfa(motivo="limite" if limite else "invalido",
                        usuario=usuario)
