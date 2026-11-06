"""Recepción, confirmación y rechazo de entregas semanales."""
from hashlib import sha256
from pathlib import Path

from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Auditoria, Entrega, Interpretacion, PuntoMalla
from app.services.bootstrap import get_default_proyecto
from app.services.common import (
    SRID_MALLA,
    ErrorEntrega,
    clase_a_db,
)
from app.services.cruces import recalcular_cruce
from app.services.importer import ResultadoImportacion, importar_excel
from app.services.reemplazos import _procesar_movimientos


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
        observaciones: list[str] = []
        filas_malla: list = []
        if parsed.puntos_meta:
            filas_malla = list(db.execute(
                select(PuntoMalla, func.ST_X(PuntoMalla.geom),
                       func.ST_Y(PuntoMalla.geom))
                .where(PuntoMalla.proyecto_id == entrega.proyecto_id,
                       PuntoMalla.punto_id.in_(parsed.puntos_meta))
            ).all())
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



def rechazar_entrega(db: Session, entrega_id: int, motivo: str, actor: str = "cargador") -> None:
    entrega = db.get(Entrega, entrega_id)
    if entrega is None or entrega.estado != "pendiente":
        raise ValueError("La entrega no existe o ya fue procesada.")
    entrega.estado = "rechazada"
    entrega.detalle_validacion = {**(entrega.detalle_validacion or {}), "motivo_rechazo": motivo}
    db.add(Auditoria(actor=actor, accion="entrega_rechazada", entidad="entrega",
                     entidad_id=str(entrega.id), detalle={"motivo": motivo}))
    db.commit()
