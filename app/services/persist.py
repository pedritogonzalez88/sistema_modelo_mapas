"""Persistencia en PostgreSQL + PostGIS.

Reutiliza el dominio existente (cross_engine, qaqc) sin modificarlo:
convierte entre codigos enteros 1-9 de la BD y las etiquetas del dominio.
"""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re

from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.classes import SIN_CONSENSO, ClaseCobertura
from app.domain.cross_engine import TipoResultado, resolver_tabla7
from app.domain.qaqc import EstadoQAQC, EventoQAQC, transicion
from app.models import (
    Auditoria,
    ClaseMapa,
    Entrega,
    FichaQAQC,
    Interpretacion,
    Proyecto,
    PuntoMalla,
    Reemplazo,
    ResultadoCruce,
    Usuario,
)
from app.services.analysis import analisis_seccion5
from app.services.importer import CAUSAS_RESERVA, ResultadoImportacion, importar_excel

_CLASES = list(ClaseCobertura)
_ESTADOS_CERRADOS = {"Resuelto por consenso", "Reemplazo completado"}
ESTADOS_REEMPLAZO = ("propuesto", "autorizado", "completado")
ERROR_MAPA_NO_CARGADO = "MAPA_NO_CARGADO"


class StoreError(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


def _para_json(obj):
    """Convierte fechas a ISO para columnas JSONB (auditoria, detalles)."""
    from datetime import date, datetime

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _para_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_para_json(v) for v in obj]
    return obj


# --- Conversión dominio <-> BD -------------------------------------------------
def clase_a_db(valor) -> tuple[int | None, bool]:
    """Devuelve (codigo 1-9 o None, es_sc)."""
    if valor is None:
        return None, False
    if valor == SIN_CONSENSO:
        return None, True
    if isinstance(valor, ClaseCobertura):
        return _CLASES.index(valor) + 1, False
    return None, False


def clase_a_dominio(codigo: int | None, es_sc: bool = False):
    if es_sc:
        return SIN_CONSENSO
    if codigo is None:
        return None
    return _CLASES[codigo - 1]


# --- Proyecto / seed ----------------------------------------------------------
def get_default_proyecto(db: Session) -> Proyecto:
    proyecto = db.scalars(select(Proyecto).order_by(Proyecto.id)).first()
    if proyecto is None:
        proyecto = Proyecto(nombre="BTR2 Paraguay", periodo="1990-2024", protocolo_version="EET-V4")
        db.add(proyecto)
        db.commit()
        db.refresh(proyecto)
    return proyecto


def seed(db: Session) -> None:
    get_default_proyecto(db)
    if db.scalar(select(func.count()).select_from(Usuario)) == 0:
        db.add_all([
            Usuario(nombre="Asistente A", rol="asistente", letra_terna="A"),
            Usuario(nombre="Asistente B", rol="asistente", letra_terna="B"),
            Usuario(nombre="Asistente C", rol="asistente", letra_terna="C"),
            Usuario(nombre="Responsable de carga", rol="cargador"),
            Usuario(nombre="Supervisión AEP", rol="supervision"),
        ])
        db.commit()
    db.add(Auditoria(actor="sistema", accion="seed", entidad="sistema",
                     detalle={"msg": "seed verificado"}))
    db.commit()


# --- Entregas -----------------------------------------------------------------
def crear_entrega_pendiente(db: Session, letra: str, semana: str, tipo: str,
                            contenido: bytes, actor: str = "cargador"):
    digest = sha256(contenido).hexdigest()
    duplicada = db.scalar(select(Entrega).where(
        Entrega.sha256 == digest, Entrega.asistente_letra == letra))
    if duplicada is not None:
        raise ValueError(
            f"Archivo duplicado: ya ingresó como entrega #{duplicada.id} del asistente {letra}.")

    proyecto = get_default_proyecto(db)
    ultima = db.scalar(select(func.max(Entrega.version)).where(
        Entrega.proyecto_id == proyecto.id,
        Entrega.asistente_letra == letra, Entrega.semana == semana))
    version = (ultima or 0) + 1

    ruta = settings.upload_path / f"{digest}.xlsx"
    if not ruta.exists():
        ruta.write_bytes(contenido)

    parsed: ResultadoImportacion = importar_excel(contenido)
    entrega = Entrega(
        proyecto_id=proyecto.id, asistente_letra=letra, semana=semana, tipo=tipo,
        version=version, archivo_path=str(ruta), sha256=digest, estado="pendiente",
        detalle_validacion={
            "cruces": len(parsed.datos), "errores": len(parsed.errores),
            "nota": parsed.nota,
            "primeros_errores": parsed.errores[:50],
        },
    )
    db.add(entrega)
    db.commit()
    db.refresh(entrega)
    return entrega, parsed


def confirmar_entrega(db: Session, entrega_id: int, actor: str = "cargador") -> dict:
    entrega = db.get(Entrega, entrega_id)
    if entrega is None or entrega.estado != "pendiente":
        raise ValueError("La entrega no existe o ya fue procesada.")

    contenido = Path(entrega.archivo_path).read_bytes()
    parsed = importar_excel(contenido)
    letra = entrega.asistente_letra
    srid = db.get(Proyecto, entrega.proyecto_id).srid

    # 1. Interpretaciones (vacío = ausencia: no se guarda fila)
    n_guardadas = 0
    for (punto, anio), valores in parsed.datos.items():
        etiqueta = valores.get(letra)
        codigo, es_sc = clase_a_db(etiqueta)
        if codigo is None and not es_sc:
            continue
        meta = parsed.puntos_meta.get(punto, {})
        db.add(Interpretacion(
            entrega_id=entrega.id, proyecto_id=entrega.proyecto_id,
            punto_id=punto, anio=anio, asistente_letra=letra,
            clase=codigo, es_sc=es_sc,
            x=meta.get("x"), y=meta.get("y"), nota=meta.get("nota"),
        ))
        n_guardadas += 1

    # 2. Malla oficial: registrar o contrastar coordenadas (nunca desplazar)
    observaciones = []
    for punto, meta in parsed.puntos_meta.items():
        x, y = meta.get("x"), meta.get("y")
        malla = db.scalar(select(PuntoMalla).where(
            PuntoMalla.proyecto_id == entrega.proyecto_id, PuntoMalla.punto_id == punto))
        if malla is None:
            geom = None
            if x is not None and y is not None:
                geom = WKTElement(f"POINT({x} {y})", srid=srid)
            db.add(PuntoMalla(proyecto_id=entrega.proyecto_id, punto_id=punto,
                              geom=geom, origen="importacion"))
        elif x is not None and y is not None and malla.geom is not None:
            actual = db.scalar(func.ST_AsText(malla.geom))
            if actual != f"POINT({x} {y})":
                observaciones.append(
                    f"Punto {punto}: coordenadas {x},{y} difieren de la malla ({actual}).")

    entrega.estado = "aceptada"
    entrega.detalle_validacion = {**(entrega.detalle_validacion or {}),
                                  "observaciones_malla": observaciones,
                                  "interpretaciones_guardadas": n_guardadas}
    db.add(Auditoria(actor=actor, accion="entrega_aceptada", entidad="entrega",
                     entidad_id=str(entrega.id),
                     detalle={"letra": letra, "semana": entrega.semana,
                              "version": entrega.version, "guardadas": n_guardadas}))
    db.commit()

    # 3. Recalcular cruces afectados (ultima version aceptada por letra)
    claves = {(p, a) for (p, a) in parsed.datos}
    n_cruces = 0
    for punto, anio in claves:
        recalcular_cruce(db, entrega.proyecto_id, punto, anio)
        n_cruces += 1

    # 4. Movimientos de reserva de la columna Notas (4.6 / Tabla 5)
    obs_reserva = _procesar_movimientos(db, entrega.proyecto_id, parsed.movimientos,
                                        actor)
    observaciones.extend(obs_reserva)
    db.commit()
    return {"interpretaciones": n_guardadas, "cruces": n_cruces,
            "observaciones": observaciones}


def _malla_de(db: Session, proyecto_id: int, punto: int) -> PuntoMalla | None:
    return db.scalar(select(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto_id, PuntoMalla.punto_id == punto))


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
        m_res = PuntoMalla(proyecto_id=proyecto_id, punto_id=reserva,
                           origen="reserva-declarada", es_reserva=True)
        db.add(m_res)
        observaciones.append(
            f"Punto {punto}: la reserva {reserva} no estaba en la malla; "
            "se registro como reserva declarada (verificar disenio).")
    elif not m_res.es_reserva:
        observaciones.append(
            f"Punto {punto}: el punto {reserva} no figura como reserva "
            "en la malla (ad-hoc prohibido por 4.7).")
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
        Reemplazo.estado.in_(("autorizado", "completado")))
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
    orden = {"propuesto": 0, "autorizado": 1, "completado": 2}
    rep = db.get(Reemplazo, reemplazo_id)
    if rep is None:
        raise ValueError("Reemplazo inexistente.")
    if nuevo_estado not in orden or orden[nuevo_estado] <= orden[rep.estado]:
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


def parsear_malla(contenido: bytes, nombre: str) -> list[dict]:
    """Columnas: punto_id, estrato?, origen_anio?, peso?, x?, y?, es_reserva?."""
    import csv
    import io

    if nombre.lower().endswith(".csv"):
        texto = contenido.decode("utf-8-sig")
        lector = csv.DictReader(io.StringIO(texto))
        filas = [{k.strip().lower(): (v.strip() if isinstance(v, str) else v)
                  for k, v in f.items()} for f in lector]
    else:
        from openpyxl import load_workbook

        libro = load_workbook(BytesIO(contenido), data_only=True, read_only=True)
        try:
            ws = libro.active
            crudas = list(ws.iter_rows(values_only=True))
        finally:
            libro.close()
        if not crudas:
            raise ValueError("Archivo vacío.")
        enc = [("" if c is None else str(c).strip().lower()) for c in crudas[0]]
        filas = [dict(zip(enc, r)) for r in crudas[1:] if r is not None]

    def num(v):
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def entero(v):
        n = num(v)
        return None if n is None else int(n)

    def bit(v):
        if v in (None, ""):
            return None
        return str(v).strip().lower() in ("1", "true", "si", "sí", "x")

    salida = []
    for f in filas:
        pid = entero(f.get("punto_id") if "punto_id" in f else f.get("id_punto"))
        if pid is None:
            continue
        salida.append({"punto_id": pid, "estrato": f.get("estrato") or None,
                       "origen_anio": entero(f.get("origen_anio") or f.get("origen")),
                       "peso": num(f.get("peso")), "x": num(f.get("x")),
                       "y": num(f.get("y")), "es_reserva": bit(f.get("es_reserva"))})
    if not salida:
        raise ValueError("Sin filas válidas (se exige punto_id).")
    return salida


def rechazar_entrega(db: Session, entrega_id: int, motivo: str, actor: str = "cargador") -> None:
    entrega = db.get(Entrega, entrega_id)
    if entrega is None or entrega.estado != "pendiente":
        raise ValueError("La entrega no existe o ya fue procesada.")
    entrega.estado = "rechazada"
    entrega.detalle_validacion = {**(entrega.detalle_validacion or {}), "motivo_rechazo": motivo}
    db.add(Auditoria(actor=actor, accion="entrega_rechazada", entidad="entrega",
                     entidad_id=str(entrega.id), detalle={"motivo": motivo}))
    db.commit()


def _ultima_interpretacion(db: Session, proyecto_id: int, punto: int, anio: int, letra: str):
    return db.scalar(
        select(Interpretacion)
        .join(Entrega, Entrega.id == Interpretacion.entrega_id)
        .where(Interpretacion.proyecto_id == proyecto_id,
               Interpretacion.punto_id == punto, Interpretacion.anio == anio,
               Interpretacion.asistente_letra == letra,
               Entrega.estado == "aceptada")
        .order_by(Entrega.version.desc(), Entrega.id.desc()))


def recalcular_cruce(db: Session, proyecto_id: int, punto: int, anio: int) -> ResultadoCruce:
    interps = {letra: _ultima_interpretacion(db, proyecto_id, punto, anio, letra)
               for letra in ("A", "B", "C")}
    valores = {letra: (clase_a_dominio(i.clase, i.es_sc) if i else None)
               for letra, i in interps.items()}
    res = resolver_tabla7(valores["A"], valores["B"], valores["C"])
    consol, _ = clase_a_db(res.consenso) if isinstance(res.consenso, ClaseCobertura) else (None, False)

    if res.resultado is TipoResultado.VALIDACION_AUTOMATICA:
        nombre_res = "unanimidad"
    elif res.resultado is TipoResultado.REQUIERE_REVISION:
        nombre_res = "mayoria"
    elif res.resultado is TipoResultado.CONSENSO_TECNICO:
        nombre_res = "discrepancia"
    else:
        nombre_res = "incompleto" if SIN_CONSENSO not in valores.values() else "discrepancia"

    cruce = db.scalar(select(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == proyecto_id,
        ResultadoCruce.punto_id == punto, ResultadoCruce.anio == anio))
    if cruce is None:
        cruce = ResultadoCruce(proyecto_id=proyecto_id, punto_id=punto, anio=anio)
        db.add(cruce)
    a_db, _ = clase_a_db(valores["A"])
    b_db, _ = clase_a_db(valores["B"])
    c_db, _ = clase_a_db(valores["C"])
    cruce.a, cruce.b, cruce.c = a_db, b_db, c_db
    cruce.resultado = nombre_res
    cruce.clase_consolidada = consol
    cruce.regla_version = settings.regla_version
    db.flush()

    # Ficha QA/QC automática: terna completa con 3 clases distintas o SC presente
    presentes = [v for v in valores.values() if v is not None]
    distintas = {v for v in presentes}
    if len(presentes) == 3 and (len(distintas) == 3 or SIN_CONSENSO in distintas):
        existe = db.scalar(select(FichaQAQC).where(
            FichaQAQC.proyecto_id == proyecto_id,
            FichaQAQC.punto_id == punto, FichaQAQC.anio == anio))
        if existe is None:
            db.add(FichaQAQC(
                proyecto_id=proyecto_id, punto_id=punto, anio=anio,
                estado=EstadoQAQC.DETECTADO.value,
                valores_iniciales={k: (v.value if isinstance(v, ClaseCobertura) else v)
                                   for k, v in valores.items()},
            ))
            db.flush()
    return cruce


# --- Malla oficial y clases del mapa --------------------------------------------
def cargar_malla(db: Session, proyecto_id: int, filas: list[dict],
                 actor: str = "cargador") -> dict:
    """Alta/actualizacion de metadatos muestrales por punto.

    Cada fila: punto_id, estrato?, origen_anio? (1990|2007|2017|2024),
    peso?, x?, y?, es_reserva?. Los puntos ad-hoc estan prohibidos (4.7):
    toda reserva debe ingresar por esta via antes de usarse.
    """
    proyecto = db.get(Proyecto, proyecto_id)
    srid = proyecto.srid if proyecto else settings.srid
    creados = actualizados = 0
    for f in filas:
        punto = int(f["punto_id"])
        m = _malla_de(db, proyecto_id, punto)
        if m is None:
            m = PuntoMalla(proyecto_id=proyecto_id, punto_id=punto,
                           origen="malla-oficial")
            db.add(m)
            creados += 1
        else:
            actualizados += 1
        if f.get("estrato"):
            m.estrato = str(f["estrato"])
        if f.get("origen_anio"):
            m.origen_anio = int(f["origen_anio"])
        if f.get("peso") is not None:
            m.peso = float(f["peso"])
        if f.get("es_reserva") is not None:
            m.es_reserva = bool(f["es_reserva"])
        x, y = f.get("x"), f.get("y")
        if x is not None and y is not None:
            m.geom = WKTElement(f"POINT({x} {y})", srid=srid)
    db.add(Auditoria(actor=actor, accion="malla_actualizada", entidad="malla",
                     detalle={"creados": creados, "actualizados": actualizados}))
    db.commit()
    return {"creados": creados, "actualizados": actualizados}


def _parsear_mapa_xlsx(contenido: bytes) -> list[dict]:
    """Formato ancho: ID_PUNTO + A1990..A2024 con clases 1-9 (mapas MADES)."""
    from openpyxl import load_workbook

    libro = load_workbook(BytesIO(contenido), data_only=True, read_only=True)
    try:
        ws = libro.active
        filas = list(ws.iter_rows(values_only=True))
    finally:
        libro.close()
    if not filas:
        raise ValueError("Archivo vacío.")
    enc = [("" if c is None else str(c).strip()) for c in filas[0]]
    idx_punto = next((i for i, c in enumerate(enc)
                      if c.lower().replace("_", "").replace(" ", "") == "idpunto"), None)
    if idx_punto is None:
        raise ValueError("Falta la columna ID_PUNTO.")
    anios = {}
    for i, c in enumerate(enc):
        m = re.match(r"^A(\d{4})$", c)
        if m:
            anios[i] = int(m.group(1))
    if not anios:
        raise ValueError("No hay columnas anuales A1990..A2024.")
    registros = []
    for fila in filas[1:]:
        if fila is None or idx_punto >= len(fila):
            continue
        try:
            punto = int(str(fila[idx_punto]).split(".")[0])
        except (TypeError, ValueError):
            continue
        for i, anio in anios.items():
            v = fila[i] if i < len(fila) else None
            if v in (None, ""):
                continue
            try:
                clase = int(float(v))
            except (TypeError, ValueError):
                raise ValueError(f"Clase inválida '{v}' (punto {punto}, año {anio}).")
            if not 1 <= clase <= 9:
                raise ValueError(f"Clase {clase} fuera de 1-9 (punto {punto}, año {anio}).")
            registros.append({"punto_id": punto, "anio": anio, "clase": clase})
    return registros


def _parsear_mapa_csv(contenido: bytes) -> list[dict]:
    """Formato largo: punto,anio,clase por fila."""
    import csv
    import io

    texto = contenido.decode("utf-8-sig")
    lector = csv.DictReader(io.StringIO(texto))
    faltan = {"punto", "anio", "clase"} - {k.strip().lower() for k in (lector.fieldnames or [])}
    if faltan:
        raise ValueError(f"CSV sin columnas {sorted(faltan)} (punto,anio,clase).")
    registros = []
    for fila in lector:
        bajo = {k.strip().lower(): v for k, v in fila.items()}
        registros.append({"punto_id": int(bajo["punto"]), "anio": int(bajo["anio"]),
                          "clase": int(bajo["clase"])})
    for r in registros:
        if not 1 <= r["clase"] <= 9:
            raise ValueError(f"Clase {r['clase']} fuera de 1-9.")
    return registros


def cargar_mapa(db: Session, proyecto_id: int, contenido: bytes, nombre: str,
                actor: str = "cargador") -> dict:
    """Ingesta de clases de los mapas MADES (filas de la matriz, 5.1)."""
    if nombre.lower().endswith(".csv"):
        registros = _parsear_mapa_csv(contenido)
        fuente = "UTCUTS-CSV"
    else:
        registros = _parsear_mapa_xlsx(contenido)
        fuente = "UTCUTS-XLSX"
    n = 0
    for r in registros:
        ex = db.scalar(select(ClaseMapa).where(
            ClaseMapa.proyecto_id == proyecto_id, ClaseMapa.punto_id == r["punto_id"],
            ClaseMapa.anio == r["anio"]))
        if ex is None:
            db.add(ClaseMapa(proyecto_id=proyecto_id, punto_id=r["punto_id"],
                             anio=r["anio"], clase=r["clase"], fuente=fuente))
            n += 1
        elif ex.clase != r["clase"]:
            ex.clase = r["clase"]
            ex.fuente = fuente
            n += 1
    db.add(Auditoria(actor=actor, accion="mapa_cargado", entidad="mapa",
                     detalle={"registros": n, "fuente": fuente}))
    db.commit()
    return {"registros": n, "fuente": fuente}


def estado_mapa(db: Session, proyecto_id: int) -> dict:
    n = db.scalar(select(func.count()).select_from(ClaseMapa).where(
        ClaseMapa.proyecto_id == proyecto_id)) or 0
    puntos = db.scalar(select(func.count(func.distinct(ClaseMapa.punto_id))).where(
        ClaseMapa.proyecto_id == proyecto_id)) or 0
    return {"registros": n, "puntos": puntos}


def _muestra_con_mapa(db: Session, proyecto_id: int) -> list:
    """Pares (mapa, consenso) solo con clase del mapa cargada (5.1).

    Las filas de la matriz son las clases MADES y las columnas la
    referencia de la terna; usar una evaluadora como mapa esta prohibido.
    """
    filas = db.execute(
        select(ClaseMapa.clase, ResultadoCruce.clase_consolidada)
        .join(ResultadoCruce, (ResultadoCruce.proyecto_id == ClaseMapa.proyecto_id)
              & (ResultadoCruce.punto_id == ClaseMapa.punto_id)
              & (ResultadoCruce.anio == ClaseMapa.anio))
        .where(ClaseMapa.proyecto_id == proyecto_id,
               ResultadoCruce.clase_consolidada.is_not(None))).all()
    return [(_CLASES[mapa - 1], _CLASES[cons - 1]) for mapa, cons in filas]


def matriz_consenso(db: Session, proyecto_id: int) -> dict:
    muestra = _muestra_con_mapa(db, proyecto_id)
    if not muestra:
        raise StoreError(ERROR_MAPA_NO_CARGADO,
                         "Sin clases del mapa MADES: cargue el mapa antes de la Etapa 2.")
    k = len(_CLASES)
    matriz = [[0] * k for _ in range(k)]
    for mapa, consenso in muestra:
        matriz[_CLASES.index(mapa)][_CLASES.index(consenso)] += 1
    return {"clases": [c.value for c in _CLASES], "matriz": matriz,
            "total_registros": len(muestra)}


def seccion5(db: Session, proyecto_id: int, area_total_ha: float, pesos: list):
    muestra = _muestra_con_mapa(db, proyecto_id)
    if not muestra:
        raise StoreError(ERROR_MAPA_NO_CARGADO,
                         "Sin clases del mapa MADES: cargue el mapa antes de la Etapa 2.")
    return analisis_seccion5(muestra, area_total_ha, pesos)


# --- Consultas ----------------------------------------------------------------
def resumen_dashboard(db: Session, proyecto_id: int) -> dict:
    filas = db.execute(select(ResultadoCruce.resultado, func.count())
                       .where(ResultadoCruce.proyecto_id == proyecto_id)
                       .group_by(ResultadoCruce.resultado)).all()
    conteo = {r: n for r, n in filas}
    completas = (conteo.get("unanimidad", 0) + conteo.get("mayoria", 0)
                 + conteo.get("discrepancia", 0))
    total_eval = db.scalar(
        select(func.count()).select_from(Interpretacion)
        .join(Entrega, Entrega.id == Interpretacion.entrega_id).where(
            Interpretacion.proyecto_id == proyecto_id, Entrega.estado == "aceptada")) or 0
    total_puntos = db.scalar(select(func.count(func.distinct(ResultadoCruce.punto_id))).where(
        ResultadoCruce.proyecto_id == proyecto_id)) or 0
    pendientes = db.scalar(select(func.count()).select_from(FichaQAQC).where(
        FichaQAQC.proyecto_id == proyecto_id,
        FichaQAQC.estado.notin_(_ESTADOS_CERRADOS))) or 0
    entregas = db.scalars(select(Entrega).where(
        Entrega.proyecto_id == proyecto_id).order_by(Entrega.recibida_en.desc()).limit(20)).all()
    mapa = estado_mapa(db, proyecto_id)
    entregas_pendientes = db.scalar(
        select(func.count()).select_from(Entrega).where(
            Entrega.proyecto_id == proyecto_id,
            Entrega.estado == "pendiente")) or 0
    tiene_malla = (db.scalar(
        select(func.count()).select_from(PuntoMalla).where(
            PuntoMalla.proyecto_id == proyecto_id)) or 0) > 0
    ultima_mapa = db.scalar(
        select(func.max(ClaseMapa.cargada_en)).where(
            ClaseMapa.proyecto_id == proyecto_id))
    return {
        "total_puntos": total_puntos, "total_evaluaciones": total_eval,
        "unanimidad": conteo.get("unanimidad", 0), "mayoria": conteo.get("mayoria", 0),
        "discrepancia": conteo.get("discrepancia", 0),
        "incompleto": conteo.get("incompleto", 0),
        "pct_unanimidad": round(conteo.get("unanimidad", 0) / completas * 100, 1) if completas else 0.0,
        "pct_mayoria": round(conteo.get("mayoria", 0) / completas * 100, 1) if completas else 0.0,
        "pct_discrepancia": round(conteo.get("discrepancia", 0) / completas * 100, 1) if completas else 0.0,
        "pendientes_qaqc": pendientes,
        "entregas_pendientes": entregas_pendientes,
        "tiene_malla": tiene_malla,
        "ultima_mapa": ultima_mapa,
        "mapa_registros": mapa["registros"], "mapa_puntos": mapa["puntos"],
        "entregas": [{"id": e.id, "letra": e.asistente_letra, "semana": e.semana,
                      "tipo": e.tipo, "version": e.version, "estado": e.estado} for e in entregas],
    }


def listar_cruces(db: Session, proyecto_id: int, resultado: str | None = None,
                  punto: int | None = None, anio: int | None = None,
                  page: int = 1, per_page: int = 50) -> tuple[list, int]:
    """Cruces de la terna paginados, con el id de ficha QA/QC si existe.

    Devuelve ``(filas, total)``, donde cada fila es ``(ResultadoCruce, id_ficha)``
    y ``total`` cuenta los cruces que cumplen los filtros (sin paginar).

    El id de la ficha se resuelve con una subconsulta escalar correlacionada en
    lugar de un ``outerjoin``: un cruce tiene como maximo una ficha, pero si en
    el futuro apareciera un duplicado (mismo punto-anio) el join multiplicaria
    filas y descuadraria el total frente a las filas de la pagina. Comprobado en
    la base del contenedor ``eet-mapa-aep`` antes de implementar::

        SELECT punto_id, anio, count(*) FROM fichas_qaqc
        GROUP BY 1, 2 HAVING count(*) > 1;   -- 0 filas

    La subconsulta deja el resultado estable sin depender de esa comprobacion.
    """
    page = max(int(page or 1), 1)
    per_page = max(int(per_page or 50), 1)

    cond = [ResultadoCruce.proyecto_id == proyecto_id]
    if resultado:
        cond.append(ResultadoCruce.resultado == resultado)
    if punto:
        cond.append(ResultadoCruce.punto_id == punto)
    if anio:
        cond.append(ResultadoCruce.anio == anio)

    total = db.scalar(
        select(func.count()).select_from(ResultadoCruce).where(*cond)) or 0

    ficha_id = (
        select(FichaQAQC.id)
        .where(FichaQAQC.proyecto_id == ResultadoCruce.proyecto_id,
               FichaQAQC.punto_id == ResultadoCruce.punto_id,
               FichaQAQC.anio == ResultadoCruce.anio)
        .order_by(FichaQAQC.id)
        .limit(1)
        .correlate(ResultadoCruce)
        .scalar_subquery()
    )

    filas = db.execute(
        select(ResultadoCruce, ficha_id.label("ficha_id"))
        .where(*cond)
        .order_by(ResultadoCruce.punto_id, ResultadoCruce.anio)
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return filas, total


def conteo_resultados(db: Session, proyecto_id: int) -> dict[str, int]:
    """Conteo de cruces por resultado, con las cuatro claves siempre presentes.

    Alimenta los chips de filtro de la vista de cruces: si un resultado no
    aparece en la base (por ejemplo ``incompleto``), el chip muestra 0 en lugar
    de desaparecer.
    """
    conteo = dict(db.execute(
        select(ResultadoCruce.resultado, func.count())
        .where(ResultadoCruce.proyecto_id == proyecto_id)
        .group_by(ResultadoCruce.resultado)
    ).all())
    return {clave: conteo.get(clave, 0)
            for clave in ("unanimidad", "mayoria", "discrepancia", "incompleto")}


def detalle_cruce(db: Session, proyecto_id: int, punto: int, anio: int) -> dict | None:
    cruce = db.scalar(select(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == proyecto_id,
        ResultadoCruce.punto_id == punto, ResultadoCruce.anio == anio))
    if cruce is None:
        return None
    origen = []
    for letra in ("A", "B", "C"):
        i = _ultima_interpretacion(db, proyecto_id, punto, anio, letra)
        if i is None:
            origen.append({"letra": letra, "clase": None, "entrega": None})
        else:
            e = db.get(Entrega, i.entrega_id)
            origen.append({"letra": letra,
                           "clase": (i.clase if not i.es_sc else "SC"),
                           "entrega": {"id": e.id, "semana": e.semana,
                                       "version": e.version, "sha": e.sha256[:10]}})
    ficha = db.scalar(select(FichaQAQC).where(
        FichaQAQC.proyecto_id == proyecto_id,
        FichaQAQC.punto_id == punto, FichaQAQC.anio == anio))
    return {"cruce": cruce, "origen": origen, "ficha": ficha}


# --- QA/QC --------------------------------------------------------------------
def actualizar_ficha(db: Session, ficha_id: int, datos: dict, actor: str = "supervision"):
    ficha = db.get(FichaQAQC, ficha_id)
    if ficha is None:
        raise ValueError("Ficha inexistente.")
    if ficha.estado in _ESTADOS_CERRADOS:
        raise ValueError("La ficha está cerrada.")
    if datos.get("evidencias") is not None:
        ficha.evidencias = datos["evidencias"]
    if datos.get("sensores") is not None:
        ficha.sensores = datos["sensores"]
    if datos.get("debate") is not None:
        ficha.debate = datos["debate"]
    if datos.get("dictamen") is not None:
        ficha.dictamen = datos["dictamen"]
    if datos.get("clase_definitiva") is not None:
        ficha.clase_definitiva = datos["clase_definitiva"]
    if datos.get("reemplazo") is not None:
        ficha.reemplazo = datos["reemplazo"]
    if datos.get("causa_descarte") is not None:
        ficha.causa_descarte = datos["causa_descarte"] or None
    if datos.get("fecha_sesion") is not None:
        ficha.fecha_sesion = datos["fecha_sesion"]
    if datos.get("firmas") is not None:
        ficha.firmas = datos["firmas"]
    db.add(Auditoria(actor=actor, accion="ficha_actualizada", entidad="ficha",
                     entidad_id=str(ficha.id),
                     detalle=_para_json({k: v for k, v in datos.items()})))
    db.commit()
    db.refresh(ficha)
    return ficha


def transicionar_ficha(db: Session, ficha_id: int, evento_nombre: str, actor: str = "supervision"):
    from datetime import datetime

    ficha = db.get(FichaQAQC, ficha_id)
    if ficha is None:
        raise ValueError("Ficha inexistente.")
    try:
        evento = EventoQAQC(evento_nombre)
    except ValueError:
        raise ValueError(f"Evento '{evento_nombre}' no válido.") from None
    estado_actual = EstadoQAQC(ficha.estado)
    nuevo = transicion(estado_actual, evento)
    if nuevo is None:
        raise ValueError(f"No se permite '{evento.value}' desde '{estado_actual.value}'.")
    if evento is EventoQAQC.APROBAR_CONSENSO and not ficha.clase_definitiva:
        raise ValueError("Para aprobar por consenso se exige clase definitiva 1-9.")
    ficha.estado = nuevo.value
    if nuevo.value in _ESTADOS_CERRADOS:
        ficha.cerrada_en = datetime.utcnow()
    # El dictamen actualiza el consenso (trazable: no toca interpretaciones)
    cruce = db.scalar(select(ResultadoCruce).where(
        ResultadoCruce.proyecto_id == ficha.proyecto_id,
        ResultadoCruce.punto_id == ficha.punto_id, ResultadoCruce.anio == ficha.anio))
    if cruce is not None and evento is EventoQAQC.APROBAR_CONSENSO:
        cruce.clase_consolidada = ficha.clase_definitiva
        cruce.regla_version = "qaqc-consenso"
    if cruce is not None and evento is EventoQAQC.DECLARAR_SIN_CONSENSO:
        cruce.clase_consolidada = None
        cruce.regla_version = "qaqc-sin-consenso"
    db.add(Auditoria(actor=actor, accion="ficha_transicion", entidad="ficha",
                     entidad_id=str(ficha.id),
                     detalle={"de": estado_actual.value, "a": nuevo.value,
                              "evento": evento.value}))
    db.commit()
    db.refresh(ficha)
    return ficha
