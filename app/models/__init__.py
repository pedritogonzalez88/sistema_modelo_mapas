"""Modelo relacional MVP (Etapa 1 operativa).

Trazabilidad en 3 capas separadas:
  Interpretacion  = lo que interpreto cada asistente (origen: entrega versionada)
  ResultadoCruce  = lo que calculo el cruce automatico (regla versionada)
  FichaQAQC       = lo que resolvio la supervision (dictamen estructurado)

La clase consolidada nunca sobrescribe las interpretaciones originales.
"""
from datetime import date, datetime
from uuid import UUID, uuid4

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Proyecto(Base):
    __tablename__ = "proyectos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200), unique=True)
    periodo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    protocolo_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    srid: Mapped[int] = mapped_column(Integer, default=4326)
    creado_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    entregas: Mapped[list["Entrega"]] = relationship(back_populates="proyecto")
    puntos_malla: Mapped[list["PuntoMalla"]] = relationship(back_populates="proyecto")


class Usuario(Base):
    """Persona que opera el sistema.

    Los campos de identidad (``username``, ``email``, ``password_hash``...) se
    incorporaron con la Fase A0 y se dejan anulables a propósito: las filas
    semilla existentes no tienen credenciales y el backfill se hace desde la
    administración (Fase A1/A2).

    La unicidad de ``username`` y ``email`` se garantiza con índices únicos
    funcionales sobre ``lower(...)`` (ver al final del módulo), sin depender de
    la extensión ``CITEXT``.
    """

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    # cargador | supervision | consulta | asistente (+ admin en Fase A2)
    rol: Mapped[str] = mapped_column(String(30), default="consulta")
    # Letra de la terna (A/B/C) solo si rol = asistente
    letra_terna: Mapped[str | None] = mapped_column(String(1), nullable=True)

    # --- Identidad y credenciales ---
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # local | ldap | oidc (futuro)
    auth_provider: Mapped[str] = mapped_column(
        String(20), default="local", server_default="local")
    # NULL cuando el proveedor no es local (nunca se guardan hashes de AD)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # disabled | email | totp | any
    mfa_policy: Mapped[str] = mapped_column(
        String(20), default="any", server_default="any")
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # manual | ldap: si es manual, un login LDAP no sobrescribe el rol
    role_source: Mapped[str] = mapped_column(
        String(10), default="manual", server_default="manual")
    ldap_dn: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())


class PuntoMalla(Base):
    """Malla oficial de muestreo. Las coordenadas de una entrega se contrastan
    contra esta tabla; nunca desplazan el punto automaticamente."""

    __tablename__ = "puntos_malla"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)  # ID_PUNTO oficial
    geom = mapped_column(Geometry("POINT", srid=4326))
    estrato: Mapped[str | None] = mapped_column(String(100), nullable=True)
    origen: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # ORIGEN Tabla 9: anio de referencia del disenio (1990|2007|2017|2024)
    origen_anio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Peso muestral del estrato (superficie relativa consolidada)
    peso: Mapped[float | None] = mapped_column(Float, nullable=True)
    es_reserva: Mapped[bool] = mapped_column(default=False)
    activo: Mapped[bool] = mapped_column(default=True)

    proyecto: Mapped["Proyecto"] = relationship(back_populates="puntos_malla")


class Entrega(Base):
    """Recepcion semanal versionada. El archivo original se conserva con su
    huella SHA-256 como evidencia, incluso si la entrega es rechazada."""

    __tablename__ = "entregas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    asistente_letra: Mapped[str] = mapped_column(String(1))  # A | B | C
    semana: Mapped[str] = mapped_column(String(20), index=True)
    # avance | acumulado | correccion
    tipo: Mapped[str] = mapped_column(String(20), default="avance")
    version: Mapped[int] = mapped_column(Integer, default=1)
    archivo_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # pendiente | aceptada | rechazada
    estado: Mapped[str] = mapped_column(String(20), default="pendiente", index=True)
    detalle_validacion = mapped_column(JSONB, default=dict)
    recibida_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cargada_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    proyecto: Mapped["Proyecto"] = relationship(back_populates="entregas")
    interpretaciones: Mapped[list["Interpretacion"]] = relationship(back_populates="entrega")


class Interpretacion(Base):
    """Una clase 1-9 (o ausencia = NULL) para proyecto+punto+anio+asistente+version.
    Formato largo: las exportaciones reconstruyen las columnas A1990-A2024."""

    __tablename__ = "interpretaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entrega_id: Mapped[int] = mapped_column(ForeignKey("entregas.id"), index=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    asistente_letra: Mapped[str] = mapped_column(String(1))
    clase: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # True cuando la celda contenia SC: estado de resolucion, no codigo de cobertura
    es_sc: Mapped[bool] = mapped_column(default=False)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)

    entrega: Mapped["Entrega"] = relationship(back_populates="interpretaciones")


class ResultadoCruce(Base):
    """Ultimo cruce calculado por punto-anio. El historial de recalculos
    queda en auditoria; las interpretaciones de origen no se tocan."""

    __tablename__ = "resultados_cruce"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    a: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    b: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    c: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # unanimidad | mayoria | discrepancia | incompleto
    resultado: Mapped[str] = mapped_column(String(30), index=True)
    clase_consolidada: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    regla_version: Mapped[str] = mapped_column(String(30), default="tabla7-v1")
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class FichaQAQC(Base):
    """Expediente de resolucion de discrepancias absolutas. El anio afectado
    es obligatorio: identifica inequivocamente que cruce se resuelve."""

    __tablename__ = "fichas_qaqc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    estado: Mapped[str] = mapped_column(String(40), default="Detectado", index=True)
    valores_iniciales = mapped_column(JSONB, default=dict)  # {A, B, C, versiones}
    evidencias = mapped_column(JSONB, default=list)
    debate: Mapped[str | None] = mapped_column(Text, nullable=True)
    # consenso | sin_consenso | null
    dictamen: Mapped[str | None] = mapped_column(String(20), nullable=True)
    clase_definitiva: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    reemplazo = mapped_column(JSONB, nullable=True)
    # Ficha del protocolo 4.12.1: sensores, causa de descarte, fecha y firmas
    sensores = mapped_column(JSONB, default=list)
    causa_descarte: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fecha_sesion: Mapped[date | None] = mapped_column(Date, nullable=True)
    firmas = mapped_column(JSONB, default=dict)
    responsable_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    accion: Mapped[str] = mapped_column(String(100))
    entidad: Mapped[str] = mapped_column(String(100))
    entidad_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detalle = mapped_column(JSONB, default=dict)
    fecha: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Origen de la acción (Fase A0): auditoría con actor real, IP y agente.
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Reemplazo(Base):
    """Sustitucion de un punto principal por uno de reserva (4.5-4.9).

    La notacion R/A de la columna Notas implica autorizacion de la
    supervision; los puntos ad-hoc estan prohibidos: la reserva debe
    existir en la malla y pertenecer al mismo estrato (4.7)."""

    __tablename__ = "reemplazos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_origen: Mapped[int] = mapped_column(Integer, index=True)
    # Puede ser NULL en un reporte propuesto aun sin reserva asignada
    punto_reserva: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # N = nubosidad | B = bandeado | T = transicion oculta
    causa: Mapped[str | None] = mapped_column(String(1), nullable=True)
    # propuesto | autorizado | completado
    estado: Mapped[str] = mapped_column(String(20), default="propuesto", index=True)
    autorizada_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())


class ClaseMapa(Base):
    """Clase asignada por los mapas MADES (UTCUTS_FINAL_YYYY) por punto-anio.

    Filas de la matriz de confusion (5.1). Sin estos datos la Etapa 2
    no puede calcularse: esta prohibido usar una evaluadora como mapa."""

    __tablename__ = "clases_mapa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyectos.id"), index=True)
    punto_id: Mapped[int] = mapped_column(Integer, index=True)
    anio: Mapped[int] = mapped_column(Integer, index=True)
    clase: Mapped[int] = mapped_column(SmallInteger)
    fuente: Mapped[str] = mapped_column(String(100), default="UTCUTS")
    cargada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- Autenticación, sesiones y MFA (Fase A0) -----------------------------------
# Estas tablas se crean ya en A0, pero permanecen vacías hasta que la Fase A1
# implemente el login. Ninguna de ellas afecta al flujo actual de la aplicación.


class Sesion(Base):
    """Sesión revocable asociada a una cookie firmada.

    La cookie transporta el token en claro y aquí solo se guarda su SHA-256:
    una copia de la base no permite reutilizar sesiones. ``mfa_at`` registra
    cuándo se superó el segundo factor y ``last_seen_at`` alimenta el control
    de inactividad.
    """

    __tablename__ = "sesiones"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64))
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mfa_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class MfaTotp(Base):
    """Secreto TOTP cifrado (una fila por usuario).

    ``secret_enc`` se descifra con :class:`app.core.security.SecretBox` usando
    ``key_id``; ``last_used_step`` impide reutilizar un código ya aceptado.
    """

    __tablename__ = "mfa_totp"

    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(20), default="v1")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MfaEmailCode(Base):
    """Código de 6 dígitos enviado por correo, hasheado con HMAC y pepper."""

    __tablename__ = "mfa_email_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64))
    # mfa | reset | verificacion
    purpose: Mapped[str] = mapped_column(String(30), default="mfa")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)


class RecoveryCode(Base):
    """Código de respaldo de un solo uso (10 por usuario al enrolar)."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuthToken(Base):
    """Token de un solo uso para restablecer contraseña, activar o verificar.

    Se guarda el SHA-256 del token; el valor en claro viaja solo en el correo.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"))
    # reset | activacion | verificacion | mfa
    purpose: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # Intentos consumidos del token (Fase A3): el challenge de segundo factor
    # admite 5 y luego se invalida. En el resto de los propósitos queda en 0.
    mfa_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LoginAttempt(Base):
    """Intento de autenticación, exitoso o no.

    Alimenta el rate limit por cuenta y por IP y la auditoría de seguridad.
    No lleva FK a ``usuarios``: se registran identificadores inexistentes.
    """

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Identificador tal como se escribió (normalizado a minúsculas)
    identificador: Mapped[str] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    exito: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # password | mfa | recovery
    factor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EmailOutbox(Base):
    """Cola de correo saliente con reintentos y evidencia de envío."""

    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destinatario: Mapped[str] = mapped_column(String(320))
    asunto: Mapped[str] = mapped_column(String(300))
    cuerpo: Mapped[str] = mapped_column(Text)
    intentos: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    enviado_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ultimo_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- Índices -------------------------------------------------------------------
# Unicidad de credenciales sin CITEXT: se compara siempre en minúsculas y
# Postgres permite varios NULL, de modo que las filas semilla sin usuario ni
# correo siguen siendo válidas hasta el backfill.
Index("uq_usuarios_username_lower", func.lower(Usuario.username), unique=True)
Index("uq_usuarios_email_lower", func.lower(Usuario.email), unique=True)

Index("uq_sesiones_token_hash", Sesion.token_hash, unique=True)
Index("ix_sesiones_usuario_revocada", Sesion.usuario_id, Sesion.revoked_at)
Index("ix_sesiones_expira", Sesion.expires_at)

Index("ix_auth_tokens_token_hash", AuthToken.token_hash, unique=True)
Index("ix_auth_tokens_usuario_proposito", AuthToken.usuario_id, AuthToken.purpose)
Index("ix_auth_tokens_expira", AuthToken.expires_at)

Index("ix_login_attempts_identificador_fecha", LoginAttempt.identificador,
      LoginAttempt.created_at)
Index("ix_login_attempts_ip_fecha", LoginAttempt.ip, LoginAttempt.created_at)

Index("ix_mfa_email_codes_usuario_purpose", MfaEmailCode.usuario_id,
      MfaEmailCode.purpose, MfaEmailCode.consumed_at)
Index("ix_recovery_codes_usuario", RecoveryCode.usuario_id)
Index("ix_email_outbox_pendientes", EmailOutbox.enviado_en, EmailOutbox.created_at)
