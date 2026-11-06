"""Reemplazos y reservas: propuesta, autorización y cierre."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Auditoria, PuntoMalla, Reemplazo
from app.services.importer import CAUSAS_RESERVA
from app.services.malla import _malla_de

def _procesar_movimientos(db: Session, proyecto_id: int, movimientos: dict,
                          actor: str) -> list:
    """Registra los codigos R/A de Notas como eventos de reemplazo (4.5-4.9).

    Dos pasadas (primero R, luego A) con flush intermedio para que la
    activacion encuentre el expediente creado por el reemplazo.
    """
    observaciones = []
    items_r = [(p, m) for p, m in movimientos.items() if m["accion"] == "R"]
    items_a = [(p, m) for p, m in movimientos.items() if m["accion"] == "A"]
    for punto, mov in items_r:
        _registrar_reemplazo_r(db, proyecto_id, punto, mov, actor, observaciones)
    db.flush()
    for punto, mov in items_a:
        _registrar_activacion_a(db, proyecto_id, punto, mov, actor, observaciones)
    db.flush()
    return observaciones


def _registrar_reemplazo_r(db, proyecto_id, punto, mov, actor, observaciones):
    reserva = mov["ref"]
    m_res = _malla_de(db, proyecto_id, reserva)
    if m_res is None:
        observaciones.append(
            f"Punto {punto}: la reserva {reserva} no está en la malla; "
            "no se registra el reemplazo. Cargue primero la malla oficial.")
        return
    if not m_res.es_reserva:
        observaciones.append(
            f"Punto {punto}: el punto {reserva} no figura como reserva "
            "en la malla (ad-hoc prohibido por 4.7); no se registra.")
        return
    m_ori = _malla_de(db, proyecto_id, punto)
    if (m_ori is not None and m_res is not None and m_ori.estrato
            and m_res.estrato and m_ori.estrato != m_res.estrato):
        observaciones.append(
            f"Punto {punto}: la reserva {reserva} es de otro estrato "
            f"({m_res.estrato} vs {m_ori.estrato}).")
    existe = db.scalar(select(Reemplazo).where(
        Reemplazo.proyecto_id == proyecto_id,
        Reemplazo.punto_origen == punto, Reemplazo.punto_reserva == reserva))
    if existe is None:
        db.add(Reemplazo(proyecto_id=proyecto_id, punto_origen=punto,
                         punto_reserva=reserva, causa=mov["causa"],
                         estado="autorizado"))
        db.add(Auditoria(actor=actor, accion="reemplazo_autorizado",
                         entidad="reemplazo",
                         detalle={"origen": punto, "reserva": reserva,
                                  "causa": CAUSAS_RESERVA.get(mov["causa"])}))


def _registrar_activacion_a(db, proyecto_id, punto, mov, actor, observaciones):
    origen = mov["ref"]
    ficha = db.scalar(select(Reemplazo).where(
        Reemplazo.proyecto_id == proyecto_id,
        Reemplazo.punto_reserva == punto))
    if ficha is None:
        db.add(Reemplazo(proyecto_id=proyecto_id, punto_origen=origen,
                         punto_reserva=punto, estado="completado"))
        observaciones.append(
            f"Reserva {punto}: activada sin codigo R previo "
            f"(origen declarado {origen}).")
    elif ficha.estado != "completado":
        ficha.estado = "completado"
        db.add(Auditoria(actor=actor, accion="reemplazo_completado",
                         entidad="reemplazo", entidad_id=str(ficha.id),
                         detalle={"origen": ficha.punto_origen,
                                  "reserva": punto}))


def proponer_reserva(db: Session, proyecto_id: int, punto_origen: int,
                     actor: str = "supervision") -> Reemplazo:
    """Asigna el primer punto de reserva disponible del mismo estrato (4.7)."""
    origen = _malla_de(db, proyecto_id, punto_origen)
    usadas = select(Reemplazo.punto_reserva).where(
        Reemplazo.proyecto_id == proyecto_id,
        Reemplazo.punto_reserva.is_not(None),
        Reemplazo.estado.in_(("propuesto", "autorizado", "completado")),
        Reemplazo.punto_origen != punto_origen)
    q = select(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto_id, PuntoMalla.es_reserva.is_(True),
        PuntoMalla.activo.is_(True), PuntoMalla.punto_id.not_in(usadas))
    if origen is not None and origen.estrato:
        q = q.where(PuntoMalla.estrato == origen.estrato)
    candidata = db.scalar(q.order_by(PuntoMalla.punto_id))
    if candidata is None:
        raise ValueError("No hay reservas disponibles del mismo estrato.")
    rep = db.scalar(select(Reemplazo).where(
        Reemplazo.proyecto_id == proyecto_id,
        Reemplazo.punto_origen == punto_origen,
        Reemplazo.estado == "propuesto"))
    if rep is None:
        rep = Reemplazo(proyecto_id=proyecto_id, punto_origen=punto_origen,
                        estado="propuesto")
        db.add(rep)
    rep.punto_reserva = candidata.punto_id
    db.add(Auditoria(actor=actor, accion="reserva_propuesta", entidad="reemplazo",
                     detalle={"origen": punto_origen,
                              "reserva": candidata.punto_id,
                              "estrato": candidata.estrato}))
    db.commit()
    db.refresh(rep)
    return rep


def transicionar_reemplazo(db: Session, reemplazo_id: int, nuevo_estado: str,
                           actor: str = "supervision") -> Reemplazo:
    permitidas = {
        "propuesto": {"autorizado"},
        "autorizado": {"completado"},
        "completado": set(),
    }
    rep = db.get(Reemplazo, reemplazo_id)
    if rep is None:
        raise ValueError("Reemplazo inexistente.")
    if rep.estado not in permitidas:
        raise ValueError(f"Estado actual inválido: '{rep.estado}'.")
    if nuevo_estado not in permitidas[rep.estado]:
        raise ValueError(f"No se puede pasar de '{rep.estado}' a '{nuevo_estado}'.")
    if nuevo_estado in ("autorizado", "completado") and not rep.punto_reserva:
        raise ValueError("Asigne primero un punto de reserva.")
    rep.estado = nuevo_estado
    db.add(Auditoria(actor=actor, accion="reemplazo_transicion", entidad="reemplazo",
                     entidad_id=str(rep.id), detalle={"a": nuevo_estado}))
    db.commit()
    db.refresh(rep)
    return rep


def listar_reemplazos(db: Session, proyecto_id: int):
    return db.scalars(select(Reemplazo).where(
        Reemplazo.proyecto_id == proyecto_id).order_by(Reemplazo.id)).all()


def crear_reemplazo_propuesto(db: Session, proyecto_id: int, punto_origen: int,
                              causa: str | None, actor: str = "supervision") -> Reemplazo:
    if causa and causa not in ("N", "B", "T"):
        raise ValueError("Causa inválida (N, B o T).")
    rep = Reemplazo(proyecto_id=proyecto_id, punto_origen=punto_origen,
                    causa=causa, estado="propuesto")
    db.add(rep)
    db.add(Auditoria(actor=actor, accion="reemplazo_propuesto", entidad="reemplazo",
                     detalle={"origen": punto_origen, "causa": causa}))
    db.commit()
    db.refresh(rep)
    return rep
