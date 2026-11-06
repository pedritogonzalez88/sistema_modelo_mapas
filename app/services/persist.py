"""Persistencia en PostgreSQL + PostGIS.

Reutiliza el dominio existente (cross_engine, qaqc) sin modificarlo:
convierte entre codigos enteros 1-9 de la BD y las etiquetas del dominio.
"""
from hashlib import sha256
from pathlib import Path
import re

from geoalchemy2 import WKTElement
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import utcnow
from app.domain.classes import SIN_CONSENSO, ClaseCobertura
from app.domain.cross_engine import TipoResultado, resolver_tabla7
from app.domain.qaqc import (
    EstadoQAQC,
    EventoQAQC,
    requiere_ficha_qaqc,
    transicion,
)
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
    ResultadoCruceHistorial,
    Usuario,
)
from app.seed_data import USUARIOS_SEMILLA, mfa_policy_para_rol
from app.services.analysis import analisis_seccion5
from app.services.importer import (
    CAUSAS_RESERVA,
    ResultadoImportacion,
    abrir_libro,
    importar_excel,
)

# La columna ``puntos_malla.geom`` está declarada Geometry(..., srid=4326).
# No usar ``Proyecto.srid`` para insertar: si difiere, PostGIS rechaza el WKT.
SRID_MALLA = 4326

_CLASES = list(ClaseCobertura)
_ESTADOS_CERRADOS = {"Resuelto por consenso", "Reemplazo completado"}
ESTADOS_REEMPLAZO = ("propuesto", "autorizado", "completado")
ERROR_MAPA_NO_CARGADO = "MAPA_NO_CARGADO"


class StoreError(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


class ErrorEntrega(ValueError):
    """Una entrega no puede confirmarse.

    ``codigo`` lo consume el router para redirigir a la vista previa con el
    flash correspondiente, sin perder el detalle del motivo.
    """

    def __init__(self, mensaje: str, codigo: str = "confirmacion_invalida"):
        super().__init__(mensaje)
        self.codigo = codigo


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
    # Un índice negativo devolvería silenciosamente otra clase (0 -> Otras
    # tierras) y un 99 rompería más adelante con IndexError. Se falla aquí.
    if (isinstance(codigo, bool) or not isinstance(codigo, int)
            or not 1 <= codigo <= len(_CLASES)):
        raise ValueError(
            f"Código de clase fuera de rango (1-{len(_CLASES)}): {codigo!r}."
        )
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
        # Cuentas nominales desde la fuente única (app/seed_data.py). Nacen sin
        # contraseña: se asigna por CLI (``seed-usuarios --con-password``) o por
        # la administración de usuarios.
        db.add_all([
            Usuario(
                nombre=datos["nombre"],
                username=datos["username"],
                email=datos["email"],
                rol=datos["rol"],
                letra_terna=datos["letra_terna"],
                auth_provider="local",
                mfa_policy=mfa_policy_para_rol(datos["rol"]),
                is_active=True,
                must_change_password=False,
            )
            for datos in USUARIOS_SEMILLA
        ])
        db.commit()
    db.add(Auditoria(actor="sistema", accion="seed", entidad="sistema",
                     detalle={"msg": "seed verificado"}))
    db.commit()


# --- Entregas -----------------------------------------------------------------
def crear_entrega_pendiente(db: Session, letra: str, semana: str, tipo: str,
                            contenido: bytes, actor: str = "cargador",
                            cargado_por_id: int | None = None):
    letra = (letra or "").strip().upper()
    if letra not in {"A", "B", "C"}:
        raise ValueError("La evaluadora debe ser A, B o C.")
    tipo = (tipo or "").strip().lower()
    if tipo not in {"avance", "acumulado", "correccion"}:
        raise ValueError("El tipo debe ser avance, acumulado o corrección.")
    semana = (semana or "").strip()
    if not semana or len(semana) > 20:
        raise ValueError("La semana es obligatoria (máximo 20 caracteres).")

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

    # Parsear antes de escribir: un archivo inválido no deja huérfanos en
    # uploads/ ni una fila de entrega a medias.
    parsed: ResultadoImportacion = importar_excel(contenido)
    letras = {clave for valores in parsed.datos.values() for clave in valores}
    ajenas = sorted(letras - {letra})
    if ajenas:
        raise ValueError(
            "El archivo contiene evaluadoras "
            + ", ".join(ajenas)
            + f"; esta entrega es de la evaluadora {letra}. "
            "Cargue una entrega por letra."
        )

    ruta = settings.upload_path / f"{digest}.xlsx"
    creada = False
    if not ruta.exists():
        ruta.write_bytes(contenido)
        creada = True

    entrega = Entrega(
        proyecto_id=proyecto.id, asistente_letra=letra, semana=semana, tipo=tipo,
        version=version, archivo_path=str(ruta), sha256=digest, estado="pendiente",
        cargada_por_id=cargado_por_id,
        detalle_validacion={
            "cruces": len(parsed.datos), "errores": len(parsed.errores),
            "nota": parsed.nota,
            "primeros_errores": parsed.errores[:50],
        },
    )
    try:
        db.add(entrega)
        db.flush()
        db.add(Auditoria(
            actor=actor, accion="entrega_recibida", entidad="entrega",
            entidad_id=str(entrega.id),
            detalle={"letra": letra, "semana": semana, "version": version,
                     "sha256": digest[:16], "errores": len(parsed.errores)},
        ))
        db.commit()
        db.refresh(entrega)
    except Exception:
        db.rollback()
        if creada:
            ruta.unlink(missing_ok=True)
        raise
    return entrega, parsed


def confirmar_entrega(db: Session, entrega_id: int, actor: str = "cargador") -> dict:
    entrega = db.get(Entrega, entrega_id)
    if entrega is None or entrega.estado != "pendiente":
        raise ValueError("La entrega no existe o ya fue procesada.")

    contenido = Path(entrega.archivo_path).read_bytes()
    parsed = importar_excel(contenido)
    # Un archivo con celdas inválidas o duplicados contradictorios no puede
    # confirmarse: la vista previa lo promete y, si se aceptara, se perderían
    # celdas en silencio. El operador debe corregir el original y volver a
    # subirlo (queda como nueva versión).
    if parsed.errores:
        raise ErrorEntrega(
            f"El archivo tiene {len(parsed.errores)} error(es) de validación; "
            "corríjalos y vuelva a cargarlo antes de confirmar.",
            codigo="validacion_pendiente",
        )
    letra = entrega.asistente_letra

    # Todo se escribe en una sola transacción: si falla el recálculo o un
    # movimiento de reserva, la entrega sigue pendiente y no quedan datos
    # parciales. El commit va al final.
    n_guardadas = 0
    n_ausencias = 0
    try:
        # 1. Interpretaciones. Una celda explícitamente vacía se persiste como
        # ausencia (clase NULL): así una corrección puede borrar el valor de una
        # entrega anterior en vez de que este siga ganando por ser el único.
        for (punto, anio), valores in parsed.datos.items():
            if letra not in valores:
                # La hoja no trae esta evaluadora para el punto/año: no opina.
                continue
            etiqueta = valores[letra]
            codigo, es_sc = clase_a_db(etiqueta)
            meta = parsed.puntos_meta.get(punto, {})
            db.add(Interpretacion(
                entrega_id=entrega.id, proyecto_id=entrega.proyecto_id,
                punto_id=punto, anio=anio, asistente_letra=letra,
                clase=codigo, es_sc=es_sc,
                x=meta.get("x"), y=meta.get("y"), nota=meta.get("nota"),
            ))
            if codigo is None and not es_sc:
                n_ausencias += 1
            else:
                n_guardadas += 1

        # 2. Malla oficial: registrar o contrastar coordenadas (nunca desplazar)
        observaciones = []
        filas_malla = []
        if parsed.puntos_meta:
            filas_malla = db.execute(
                select(PuntoMalla, func.ST_X(PuntoMalla.geom),
                       func.ST_Y(PuntoMalla.geom))
                .where(PuntoMalla.proyecto_id == entrega.proyecto_id,
                       PuntoMalla.punto_id.in_(parsed.puntos_meta))
            ).all()
        malla_por_punto = {
            malla.punto_id: (malla, actual_x, actual_y)
            for malla, actual_x, actual_y in filas_malla
        }
        for punto, meta in parsed.puntos_meta.items():
            x, y = meta.get("x"), meta.get("y")
            malla, actual_x, actual_y = malla_por_punto.get(
                punto, (None, None, None))
            if malla is None:
                geom = None
                if x is not None and y is not None:
                    geom = WKTElement(f"POINT({x} {y})", srid=SRID_MALLA)
                db.add(PuntoMalla(proyecto_id=entrega.proyecto_id,
                                  punto_id=punto, geom=geom,
                                  origen="importacion"))
            elif x is not None and y is not None:
                if malla.geom is None:
                    # B-18: completa una geometría que llegó nula.
                    malla.geom = WKTElement(f"POINT({x} {y})", srid=SRID_MALLA)
                elif (actual_x is None or actual_y is None
                      or abs(float(actual_x) - float(x)) > 1e-9
                      or abs(float(actual_y) - float(y)) > 1e-9):
                    observaciones.append(
                        f"Punto {punto}: coordenadas {x},{y} difieren de la "
                        f"malla ({actual_x}, {actual_y}).")

        entrega.estado = "aceptada"
        # Publica interpretaciones y estado antes de recalcular; sigue dentro
        # de la transacción, así que un fallo posterior revierte todo.
        db.flush()

        # 3. Recalcular cruces afectados (última versión aceptada por letra)
        claves = {(p, a) for (p, a) in parsed.datos}
        n_cruces = 0
        for punto, anio in claves:
            recalcular_cruce(db, entrega.proyecto_id, punto, anio,
                             actor=actor, entrega_id=entrega.id)
            n_cruces += 1

        # 4. Movimientos de reserva de la columna Notas (4.6 / Tabla 5)
        obs_reserva = _procesar_movimientos(
            db, entrega.proyecto_id, parsed.movimientos, actor)
        observaciones.extend(obs_reserva)
        entrega.detalle_validacion = {
            **(entrega.detalle_validacion or {}),
            "observaciones_malla": observaciones,
            "interpretaciones_guardadas": n_guardadas,
            "ausencias_registradas": n_ausencias,
        }
        db.add(Auditoria(actor=actor, accion="entrega_aceptada",
                         entidad="entrega", entidad_id=str(entrega.id),
                         detalle={"letra": letra, "semana": entrega.semana,
                                  "version": entrega.version,
                                  "guardadas": n_guardadas,
                                  "ausencias": n_ausencias}))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ErrorEntrega(
            "La entrega contiene datos duplicados que violan una restricción "
            "de integridad; revísela y vuelva a cargarla.",
            codigo="duplicado",
        ) from exc
    except Exception:
        db.rollback()
        raise

    return {"interpretaciones": n_guardadas, "ausencias": n_ausencias,
            "cruces": n_cruces, "observaciones": observaciones}


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


def parsear_malla(contenido: bytes, nombre: str) -> list[dict]:
    """Columnas: punto_id, estrato?, origen_anio?, peso?, x?, y?, es_reserva?."""
    import csv
    import io

    if nombre.lower().endswith(".csv"):
        try:
            texto = contenido.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("El CSV no está codificado en UTF-8.") from exc
        lector = csv.DictReader(io.StringIO(texto))
        filas = [{k.strip().lower(): (v.strip() if isinstance(v, str) else v)
                  for k, v in f.items()} for f in lector]
    else:
        libro = abrir_libro(contenido)
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
        # La versión se numera por letra + semana, así que no es comparable
        # entre semanas: la semana 05 v2 no debe ganarle a la 06 v1. Manda la
        # entrega aceptada más reciente y, a igual fecha, el id mayor.
        .order_by(Entrega.recibida_en.desc(), Entrega.id.desc()))


def recalcular_cruce(db: Session, proyecto_id: int, punto: int, anio: int,
                     actor: str = "sistema",
                     entrega_id: int | None = None) -> ResultadoCruce:
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

    ficha = db.scalar(select(FichaQAQC).where(
        FichaQAQC.proyecto_id == proyecto_id,
        FichaQAQC.punto_id == punto, FichaQAQC.anio == anio))
    ficha_cerrada = ficha is not None and ficha.estado in _ESTADOS_CERRADOS

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
    if not ficha_cerrada:
        # Solo el cálculo automático escribe el consolidado. Una ficha cerrada
        # contiene una decisión humana (consenso o reemplazo completado) que no
        # debe desaparecer porque después llegue otra entrega semanal.
        cruce.clase_consolidada = consol
        cruce.regla_version = settings.regla_version
    elif cruce.clase_consolidada is None and ficha.clase_definitiva is not None:
        # Caso defensivo: ficha cerrada sin consolidado previo (datos migrados).
        cruce.clase_consolidada = ficha.clase_definitiva
        cruce.regla_version = "qaqc-consenso"

    # Historial append-only: el estado vigente se puede reconstruir con la
    # entrega, la regla y el actor que lo produjeron.
    db.add(ResultadoCruceHistorial(
        proyecto_id=proyecto_id, punto_id=punto, anio=anio,
        entrega_id=entrega_id, a=a_db, b=b_db, c=c_db,
        resultado=nombre_res,
        clase_consolidada=cruce.clase_consolidada,
        regla_version=cruce.regla_version,
        actor=actor,
    ))
    db.flush()

    # Ficha QA/QC automática solo para discrepancia absoluta (3 clases
    # distintas) o SC, siempre con la terna completa. La mayoría 2/3 se
    # consolida sin ficha y la terna incompleta no es un desacuerdo (protocolo
    # 4.2 y 6.3); la regla vive en el dominio para no divergir de Store.
    if requiere_ficha_qaqc(valores.values()):
        if ficha is None:
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
            m.geom = WKTElement(f"POINT({x} {y})", srid=SRID_MALLA)
    db.add(Auditoria(actor=actor, accion="malla_actualizada", entidad="malla",
                     detalle={"creados": creados, "actualizados": actualizados}))
    db.commit()
    return {"creados": creados, "actualizados": actualizados}


def _parsear_mapa_xlsx(contenido: bytes) -> list[dict]:
    """Formato ancho: ID_PUNTO + A1990..A2024 con clases 1-9 (mapas MADES).

    Recorre todas las hojas y elige la primera con ID_PUNTO y columnas
    anuales; no depende de cuál sea la hoja activa ni de mayúsculas.
    """
    libro = abrir_libro(contenido)
    try:
        filas = []
        for hoja in libro.worksheets:
            candidatas = list(hoja.iter_rows(values_only=True))
            if not candidatas:
                continue
            encabezado = [("" if c is None else str(c).strip())
                          for c in candidatas[0]]
            tiene_punto = any(
                c.lower().replace("_", "").replace(" ", "") == "idpunto"
                for c in encabezado)
            tiene_anios = any(re.match(r"^a\d{4}$", c, re.IGNORECASE)
                              for c in encabezado)
            if tiene_punto and tiene_anios:
                filas = candidatas
                break
        if not filas:
            filas = list(libro.active.iter_rows(values_only=True))
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
        m = re.match(r"^A(\d{4})$", c, re.IGNORECASE)
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
                raise ValueError(
                    f"Clase inválida '{v}' (punto {punto}, año {anio})."
                ) from None
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
    claves = set()
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
        claves.add((r["punto_id"], r["anio"]))
    # B-17: un mapa corregido reemplaza al anterior; las combinaciones que ya
    # no vienen en el archivo no deben seguir contando en la matriz.
    if claves:
        db.execute(delete(ClaseMapa).where(
            ClaseMapa.proyecto_id == proyecto_id,
            tuple_(ClaseMapa.punto_id, ClaseMapa.anio).not_in(claves)))
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
    # B-19: una corrección crea otra interpretación para el mismo
    # punto/año/letra. Se cuenta solo la última aceptada de cada combinación.
    ultimas = (
        select(Interpretacion.punto_id, Interpretacion.anio,
               Interpretacion.asistente_letra,
               func.max(Interpretacion.id).label("ultimo"))
        .join(Entrega, Entrega.id == Interpretacion.entrega_id)
        .where(Interpretacion.proyecto_id == proyecto_id,
               Entrega.estado == "aceptada")
        .group_by(Interpretacion.punto_id, Interpretacion.anio,
                  Interpretacion.asistente_letra)
        .subquery()
    )
    total_eval = db.scalar(
        select(func.count()).select_from(ultimas)) or 0
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
                  page: int = 1, per_page: int = 50) -> tuple[list, int, int]:
    """Cruces de la terna paginados, con el id de ficha QA/QC si existe.

    Devuelve ``(filas, total, page)``; ``page`` se ajusta a la última página
    cuando el cliente pide una fuera de rango. Cada fila es
    ``(ResultadoCruce, id_ficha)`` y ``total`` cuenta los cruces que cumplen
    los filtros (sin paginar).

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
    paginas = max(1, (int(total) + per_page - 1) // per_page)
    page = min(page, paginas)

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
    return filas, total, page


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
        dictamen = str(datos["dictamen"]).strip().lower()
        if dictamen not in ("consenso", "sin_consenso"):
            raise ValueError("El dictamen debe ser 'consenso' o 'sin_consenso'.")
        ficha.dictamen = dictamen
    if datos.get("clase_definitiva") is not None:
        clase = datos["clase_definitiva"]
        if not isinstance(clase, int) or not 1 <= clase <= 9:
            raise ValueError("La clase definitiva debe ser un número del 1 al 9.")
        ficha.clase_definitiva = clase
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
    if evento is EventoQAQC.APROBAR_CONSENSO:
        clase = ficha.clase_definitiva
        if not isinstance(clase, int) or not 1 <= clase <= 9:
            raise ValueError("Para aprobar por consenso se exige clase definitiva 1-9.")
    ficha.estado = nuevo.value
    if nuevo.value in _ESTADOS_CERRADOS:
        ficha.cerrada_en = utcnow()
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
