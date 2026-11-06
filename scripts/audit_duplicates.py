"""Audita duplicados que impiden aplicar restricciones únicas.

Uso:
    EET_DATABASE_URL=postgresql+psycopg://eet:eet@localhost:5432/eet \
        python scripts/audit_duplicates.py

Sale con código 1 si encuentra duplicados. En ese caso: backup, consolidación
acordada y ventana de mantenimiento antes de aplicar la migración 0004.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.database import SessionLocal

RANGOS: tuple[tuple[str, str], ...] = (
    (
        "interpretaciones.clase",
        "SELECT count(*) FROM interpretaciones "
        "WHERE clase IS NOT NULL AND (clase < 1 OR clase > 9)",
    ),
    (
        "resultados_cruce.a/b/c",
        "SELECT count(*) FROM resultados_cruce WHERE "
        "(a IS NOT NULL AND (a < 1 OR a > 9)) OR "
        "(b IS NOT NULL AND (b < 1 OR b > 9)) OR "
        "(c IS NOT NULL AND (c < 1 OR c > 9))",
    ),
    (
        "resultados_cruce.clase_consolidada",
        "SELECT count(*) FROM resultados_cruce "
        "WHERE clase_consolidada IS NOT NULL "
        "AND (clase_consolidada < 1 OR clase_consolidada > 9)",
    ),
    (
        "fichas_qaqc.clase_definitiva",
        "SELECT count(*) FROM fichas_qaqc "
        "WHERE clase_definitiva IS NOT NULL "
        "AND (clase_definitiva < 1 OR clase_definitiva > 9)",
    ),
    (
        "clases_mapa.clase",
        "SELECT count(*) FROM clases_mapa WHERE clase < 1 OR clase > 9",
    ),
)

CHECKS: tuple[tuple[str, str], ...] = (
    (
        "interpretaciones",
        "SELECT entrega_id, punto_id, anio, asistente_letra, count(*) AS n "
        "FROM interpretaciones "
        "GROUP BY entrega_id, punto_id, anio, asistente_letra "
        "HAVING count(*) > 1",
    ),
    (
        "resultados_cruce",
        "SELECT proyecto_id, punto_id, anio, count(*) AS n "
        "FROM resultados_cruce GROUP BY proyecto_id, punto_id, anio "
        "HAVING count(*) > 1",
    ),
    (
        "puntos_malla",
        "SELECT proyecto_id, punto_id, count(*) AS n "
        "FROM puntos_malla GROUP BY proyecto_id, punto_id HAVING count(*) > 1",
    ),
    (
        "clases_mapa",
        "SELECT proyecto_id, punto_id, anio, count(*) AS n "
        "FROM clases_mapa GROUP BY proyecto_id, punto_id, anio "
        "HAVING count(*) > 1",
    ),
    (
        "fichas_qaqc",
        "SELECT proyecto_id, punto_id, anio, count(*) AS n "
        "FROM fichas_qaqc GROUP BY proyecto_id, punto_id, anio "
        "HAVING count(*) > 1",
    ),
    (
        "reemplazos (con reserva)",
        "SELECT proyecto_id, punto_origen, punto_reserva, count(*) AS n "
        "FROM reemplazos WHERE punto_reserva IS NOT NULL "
        "GROUP BY proyecto_id, punto_origen, punto_reserva HAVING count(*) > 1",
    ),
    (
        "entregas",
        "SELECT proyecto_id, asistente_letra, semana, version, count(*) AS n "
        "FROM entregas "
        "GROUP BY proyecto_id, asistente_letra, semana, version "
        "HAVING count(*) > 1",
    ),
    (
        "usuarios.username",
        "SELECT lower(username) AS username, count(*) AS n FROM usuarios "
        "WHERE username IS NOT NULL GROUP BY lower(username) HAVING count(*) > 1",
    ),
    (
        "usuarios.email",
        "SELECT lower(email) AS email, count(*) AS n FROM usuarios "
        "WHERE email IS NOT NULL GROUP BY lower(email) HAVING count(*) > 1",
    ),
    (
        "proyectos.nombre",
        "SELECT nombre, count(*) AS n FROM proyectos "
        "GROUP BY nombre HAVING count(*) > 1",
    ),
)


def main() -> int:
    total = 0
    with SessionLocal() as db:
        print(f"{'tabla':<26} {'grupos':>7} {'filas extra':>11}")
        print("-" * 48)
        for nombre, sql in CHECKS:
            filas = db.execute(text(sql)).all()
            grupos = len(filas)
            extra = sum(int(fila[-1]) - 1 for fila in filas) if filas else 0
            total += extra
            print(f"{nombre:<26} {grupos:>7} {extra:>11}")
            for fila in filas[:5]:
                print(f"    muestra: {tuple(fila[:-1])} ({fila[-1]} filas)")

        print("-" * 48)
        print(f"{'rango inválido':<26} {'filas':>7}")
        print("-" * 48)
        for nombre, sql in RANGOS:
            cantidad = int(db.scalar(text(sql)) or 0)
            total += cantidad
            print(f"{nombre:<26} {cantidad:>7}")

    print("-" * 48)
    if total:
        print(f"RESULTADO: {total} filas a corregir. Haga backup y consolide "
              "antes de aplicar la migración 0004.")
        return 1
    print("RESULTADO: sin duplicados ni valores fuera de rango. "
          "Las restricciones pueden aplicarse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
