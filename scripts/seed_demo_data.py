"""Datos demo idempotentes para el entorno local de pruebas.

Cubre dos necesidades distintas:

1. Filas de negocio que asumen `test_web_routes.py` y `test_a11y_static.py`
   (cruce 81/2008, ficha 1, entrega 1 con un .xlsx válido y más de 50 filas
   para paginar).
2. Cuentas demo opcionales para el smoke de autenticación. Solo se crean si el
   entorno define `EET_DEMO_ADMIN_PASSWORD` y `EET_DEMO_CONSULTA_PASSWORD`.

Uso (desde la raíz del proyecto, con la base del compose levantada):

    EET_DATABASE_URL=postgresql+psycopg://eet:eet@localhost:5432/eet \
    EET_DEMO_ADMIN_PASSWORD=... EET_DEMO_CONSULTA_PASSWORD=... \
    .venv/bin/python scripts/seed_demo_data.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Permite ejecutar `python scripts/seed_demo_data.py` sin instalar el proyecto.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook
from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.security import hash_password, utcnow
from app.models import Entrega, FichaQAQC, Proyecto, ResultadoCruce, Usuario

# Ruta relativa: el contenedor la resuelve como /app/uploads/... y las pruebas
# ejecutadas en WSL como <repo>/uploads/...
XLSX = Path("uploads") / "seed-entrega.xlsx"

CUENTAS_DEMO = (
    ("demo.admin", "demo.admin@aep.gov.py", "Administrador Demo", "admin",
     "EET_DEMO_ADMIN_PASSWORD"),
    ("demo.consulta", "demo.consulta@aep.gov.py", "Consulta Demo", "consulta",
     "EET_DEMO_CONSULTA_PASSWORD"),
)


def _crear_libro() -> None:
    XLSX.parent.mkdir(parents=True, exist_ok=True)
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Evaluador_A"
    hoja.append(["ID_EV", "ID_PUNTO", "A2008"])
    hoja.append(["A", 81, 1])
    libro.save(XLSX)
    libro.close()


def _sembrar_negocio(db) -> int:
    proyecto = db.scalar(select(Proyecto).order_by(Proyecto.id))
    if proyecto is None:
        raise SystemExit(
            "No hay proyecto. Arranque la app una vez (docker compose up) "
            "para que el lifespan cree el proyecto y las cuentas semilla.")
    pid = proyecto.id

    entrega = db.get(Entrega, 1)
    if entrega is None:
        entrega = Entrega(
            id=1, proyecto_id=pid, asistente_letra="A", semana="2026-W01",
            tipo="avance", version=1, archivo_path=str(XLSX),
            sha256="f" * 64, estado="pendiente", detalle_validacion={},
        )
        db.add(entrega)
    else:
        entrega.archivo_path = str(XLSX)
        entrega.estado = "pendiente"
    db.flush()

    ficha = db.get(FichaQAQC, 1)
    if ficha is None:
        ficha = FichaQAQC(
            id=1, proyecto_id=pid, punto_id=81, anio=2008,
            estado="Detectado", valores_iniciales={"A": 1, "B": 2, "C": 3},
            evidencias=[], sensores=[], firmas={},
        )
        db.add(ficha)
    else:
        ficha.estado = "Detectado"
        ficha.punto_id = 81
        ficha.anio = 2008
    db.flush()

    existentes = {
        (fila.punto_id, fila.anio)
        for fila in db.scalars(select(ResultadoCruce)).all()
    }
    creados = 0
    for n in range(60):
        punto = 81 if n == 0 else 1000 + n
        anio = 2008 if n == 0 else 2000 + (n % 20)
        if (punto, anio) in existentes:
            continue
        db.add(ResultadoCruce(
            proyecto_id=pid, punto_id=punto, anio=anio,
            a=1, b=1 if n % 3 else 2, c=1 if n % 5 else 3,
            resultado="mayoria" if n % 2 else "discrepancia",
            clase_consolidada=1 if n % 2 else None,
            regla_version="tabla7-v1",
        ))
        creados += 1

    fichas_creadas = 0
    for numero in range(2, 57):
        if db.get(FichaQAQC, numero) is None:
            db.add(FichaQAQC(
                id=numero, proyecto_id=pid, punto_id=2000 + numero, anio=2000,
                estado="Detectado",
                valores_iniciales={"A": 1, "B": 2, "C": 3},
                evidencias=[], sensores=[], firmas={},
            ))
            fichas_creadas += 1

    db.commit()
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text(
            "SELECT setval('entregas_id_seq', "
            "GREATEST((SELECT COALESCE(MAX(id), 1) FROM entregas), 1))"))
        db.execute(text(
            "SELECT setval('fichas_qaqc_id_seq', "
            "GREATEST((SELECT COALESCE(MAX(id), 1) FROM fichas_qaqc), 1))"))
        db.commit()
    return creados, fichas_creadas


def _sembrar_cuentas(db) -> list[str]:
    creadas: list[str] = []
    for username, email, nombre, rol, variable in CUENTAS_DEMO:
        password = (os.getenv(variable) or "").strip()
        if not password:
            continue
        usuario = db.scalar(select(Usuario).where(Usuario.username == username))
        if usuario is None:
            usuario = Usuario(username=username)
            db.add(usuario)
        usuario.nombre = nombre
        usuario.email = email
        usuario.rol = rol
        usuario.letra_terna = None
        usuario.auth_provider = "local"
        usuario.password_hash = hash_password(password)
        usuario.password_changed_at = utcnow()
        usuario.must_change_password = False
        usuario.is_active = True
        # Solo demo local: los roles críticos exigen TOTP en producción.
        usuario.mfa_policy = "disabled"
        usuario.mfa_enrolled_at = None
        usuario.role_source = "manual"
        usuario.failed_attempts = 0
        usuario.locked_until = None
        creadas.append(username)
    db.commit()
    return creadas


def main() -> None:
    _crear_libro()
    with SessionLocal() as db:
        cruces, fichas = _sembrar_negocio(db)
        cuentas = _sembrar_cuentas(db)
    print(f"Datos demo: {cruces} cruces nuevos, {fichas} fichas nuevas, "
          f"entrega 1, archivo {XLSX}.")
    if cuentas:
        print("Cuentas demo: " + ", ".join(cuentas))
    else:
        print("Cuentas demo: omitidas (faltan EET_DEMO_*_PASSWORD).")


if __name__ == "__main__":
    main()
