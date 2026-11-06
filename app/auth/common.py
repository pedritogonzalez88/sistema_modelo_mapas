"""Constantes, resultados y utilidades compartidas de autenticación."""
import time
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models import LoginAttempt, Usuario

_MAX_IDENTIFICADOR = 255
# Granularidad del "toque" de last_seen_at.
_INTERVALO_TOQUE = timedelta(minutes=1)

#: Políticas de segundo factor admitidas en ``usuarios.mfa_policy``.

#: Intentos permitidos en el challenge antes de invalidarlo.
MAX_INTENTOS_MFA = 5
#: Vida del challenge posterior a la contraseña.
VIGENCIA_DESAFIO = timedelta(minutes=5)
#: Códigos de respaldo emitidos en cada enrolamiento o regeneración.
CODIGOS_RESPALDO = 10

#: Vida del código enviado por correo (Fase A4).
VIGENCIA_CODIGO_CORREO = timedelta(minutes=10)
#: Intentos admitidos por código de correo antes de invalidarlo.
MAX_INTENTOS_CODIGO_CORREO = 5
#: Espera mínima entre envíos de códigos.
COOLDOWN_ENVIO_CORREO = timedelta(seconds=60)
#: Códigos máximos por usuario en una hora.
MAX_ENVIOS_CORREO_HORA = 5

#: Vida del enlace de restablecimiento de contraseña.
VIGENCIA_RESET = timedelta(minutes=30)
#: Solicitudes de restablecimiento por usuario en una hora.
MAX_RESET_POR_USUARIO_HORA = 3
#: Solicitudes de restablecimiento por IP en una hora.
MAX_RESET_POR_IP_HORA = 10

#: Ventana común del rate limiting por IP (Fase A6a).
VENTANA_RATE_LIMIT = timedelta(minutes=15)
#: Fallos de segundo factor por IP en la ventana antes de cortar el acceso.
MAX_FALLOS_MFA_POR_IP = 10
#: Códigos por hora que se pueden pedir desde una misma IP.
MAX_ENVIOS_CORREO_POR_IP_HORA = 10

#: Retardo mínimo de un intento LDAP fallido. Argon2id tarda decenas de ms;
#: un bind que falla en 1 ms delataría que la cuenta no existe o que el DC no
#: respondió. En pruebas se pone en 0 con ``monkeypatch``.
RETARDO_MINIMO_LDAP = 0.25

#: Texto neutro de la recuperación: idéntico exista o no la cuenta.
RESPUESTA_RECUPERACION = (
    "Si la cuenta existe, enviamos instrucciones al correo registrado."
)


@dataclass(frozen=True)
class ResultadoLogin:
    """Resultado de :func:`autenticar`.

    ``motivo`` es ``"ok"``, ``"credenciales"`` o ``"bloqueado"``. La vista usa
    ``espera_min`` para decir cuánto falta cuando la cuenta está bloqueada; el
    mensaje de credenciales es siempre genérico.
    """

    usuario: Usuario | None = None
    motivo: str = "ok"
    espera_min: int = 0

    @property
    def ok(self) -> bool:
        return self.usuario is not None


@dataclass(frozen=True)
class EstadoMfa:
    """Estado del segundo factor de un usuario (Fases A3 y A4).

    ``requiere_desafio`` es el caso "hay un segundo factor viable y la política
    lo exige": el login no crea sesión y pasa por ``/auth/mfa``.
    ``metodo`` indica el factor por defecto: ``"totp"`` (autenticador) o
    ``"email"`` (código al correo). Puede quedar ``None`` cuando no hay canal
    disponible (sin TOTP y sin correo), en cuyo caso no se bloquea el acceso.
    ``requiere_enrolamiento`` es el caso "su política exige TOTP y todavía no
    se enroló": el login crea sesión, pero el gate solo deja ver
    ``/auth/seguridad`` hasta completar la activación.
    """

    policy: str = "any"
    enrolado: bool = False
    requiere_desafio: bool = False
    requiere_enrolamiento: bool = False
    #: Fecha del enrolamiento en formato legible (solo para la vista).
    enrolado_desde: str | None = None
    #: Métodos realmente disponibles para este usuario.
    puede_totp: bool = False
    puede_email: bool = False
    #: Factor que pide el login: ``"totp"``, ``"email"`` o ``None``.
    metodo: str | None = None


@dataclass(frozen=True)
class ResultadoMfa:
    """Resultado de :func:`verificar_desafio`.

    ``motivo`` es ``"ok"``, ``"invalido"``, ``"limite"`` o ``"ausente"``.
    ``metodo`` distingue ``"totp"`` de ``"respaldo"`` para la auditoría.
    """

    motivo: str = "ausente"
    usuario: Usuario | None = None
    metodo: str | None = None

    @property
    def ok(self) -> bool:
        return self.motivo == "ok" and self.usuario is not None


def _esperar_retardo_ldap(inicio: float) -> None:
    """Completa el tiempo mínimo de un fallo LDAP (si fue demasiado rápido)."""
    if RETARDO_MINIMO_LDAP <= 0:
        return
    restante = RETARDO_MINIMO_LDAP - (time.monotonic() - inicio)
    if restante > 0:
        time.sleep(restante)


def _ip_corta(ip: str | None) -> str | None:
    """Ajusta la IP a la columna (``String(45)``, suficiente para IPv6)."""
    return str(ip)[:45] if ip else None



def _registrar_intento(db: Session, identificador: str, ip: str | None,
                       exito: bool, factor: str = "password") -> None:
    db.add(LoginAttempt(
        identificador=(identificador or "")[:_MAX_IDENTIFICADOR],
        ip=_ip_corta(ip),
        exito=exito,
        factor=factor,
    ))


# --- Rate limiting por IP (Fase A6a) -------------------------------------------
def fallos_por_ip(db: Session, ip: str | None, *, factor: str | None = None,
                  ventana: timedelta = VENTANA_RATE_LIMIT) -> int:
    """Fallos registrados desde ``ip`` en la ventana (opcionalmente por factor).

    Sin IP no hay límite: las pruebas y las tareas internas invocan el servicio
    sin cliente, y bloquear ``None`` cerraría el login entero.
    """
    if not ip:
        return 0
    condiciones = [
        LoginAttempt.ip == _ip_corta(ip),
        LoginAttempt.exito.is_(False),
        LoginAttempt.created_at >= utcnow() - ventana,
    ]
    if factor is not None:
        condiciones.append(LoginAttempt.factor == factor)
    total = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(*condiciones))
    return int(total or 0)


def ip_bloqueada(db: Session, ip: str | None, maximo: int, *,
                 factor: str | None = None,
                 ventana: timedelta = VENTANA_RATE_LIMIT) -> bool:
    """``True`` si ``ip`` ya agotó ``maximo`` fallos en la ventana."""
    return fallos_por_ip(db, ip, factor=factor, ventana=ventana) >= int(maximo)
