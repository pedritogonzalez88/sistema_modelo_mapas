"""Malla oficial, coordenadas y clases del mapa MADES."""
import re

from geoalchemy2 import WKTElement
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import Session

from app.models import Auditoria, ClaseMapa, PuntoMalla
from app.services.common import SRID_MALLA
from app.services.importer import abrir_libro


def _malla_de(db: Session, proyecto_id: int, punto: int) -> PuntoMalla | None:
    return db.scalar(select(PuntoMalla).where(
        PuntoMalla.proyecto_id == proyecto_id, PuntoMalla.punto_id == punto))



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
