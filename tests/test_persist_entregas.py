"""Regresiones de persistencia de entregas y cruces.

Estas pruebas no necesitan PostgreSQL: crean en SQLite solo las tablas que
intervienen en ``confirmar_entrega`` y ``recalcular_cruce`` (sin la columna
``geometry`` de ``puntos_malla``). Así la lógica de versiones, ausencias y
fichas QA/QC queda cubierta en cualquier equipo, sin Docker.

Las pruebas que sí requieren PostGIS (coordenadas, mallas) siguen en la suite
``integration``.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.domain.classes import ClaseCobertura
from app.domain.qaqc import EstadoQAQC
from app.models import (
    Auditoria,
    ClaseMapa,
    Entrega,
    FichaQAQC,
    Interpretacion,
    Proyecto,
    Reemplazo,
    ResultadoCruce,
    ResultadoCruceHistorial,
)
from app.services import persist
from app.services.importer import ResultadoImportacion

_TABLAS = (
    "proyectos",
    "usuarios",
    "entregas",
    "interpretaciones",
    "resultados_cruce",
    "resultados_cruce_historial",
    "fichas_qaqc",
    "reemplazos",
    "clases_mapa",
    "auditoria",
)


@pytest.fixture
def db():
    """Sesión SQLite en memoria con el subconjunto de tablas necesario."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[nombre] for nombre in _TABLAS])
    with Session(engine) as sesion:
        sesion.add(Proyecto(nombre="Proyecto de prueba", periodo="1990-2024",
                            protocolo_version="EET-V4"))
        sesion.commit()
        yield sesion


def _proyecto(db: Session) -> Proyecto:
    return db.scalar(select(Proyecto))


def _entrega(db: Session, *, letra: str = "A", semana: str = "01",
             version: int = 1, recibida_en: datetime | None = None,
             estado: str = "aceptada", archivo: str = "x.xlsx") -> Entrega:
    entrega = Entrega(
        proyecto_id=_proyecto(db).id, asistente_letra=letra, semana=semana,
        tipo="avance", version=version, archivo_path=archivo,
        sha256="0" * 64, estado=estado, detalle_validacion={},
        recibida_en=recibida_en,
    )
    db.add(entrega)
    db.commit()
    db.refresh(entrega)
    return entrega


def _interpretacion(db: Session, entrega: Entrega, *, punto: int = 1,
                    anio: int = 2020, clase: int | None = None,
                    letra: str | None = None, es_sc: bool = False) -> None:
    db.add(Interpretacion(
        entrega_id=entrega.id, proyecto_id=entrega.proyecto_id,
        punto_id=punto, anio=anio,
        asistente_letra=letra or entrega.asistente_letra,
        clase=None if es_sc else clase, es_sc=es_sc,
    ))
    db.commit()


# --- C-1: no confirmar archivos con errores -----------------------------------
def test_confirmar_rechaza_entrega_con_errores(db, tmp_path, monkeypatch):
    archivo = tmp_path / "entrega.xlsx"
    archivo.write_bytes(b"contenido")
    entrega = _entrega(db, estado="pendiente", archivo=str(archivo))
    parsed = ResultadoImportacion(
        datos={(1, 2020): {"A": ClaseCobertura.BOSQUE_NATURAL}},
        errores=[{"tipo": "ETIQUETA_INVALIDA", "mensaje": "Clase 99"}],
    )
    monkeypatch.setattr(persist, "importar_excel", lambda _contenido: parsed)

    with pytest.raises(persist.ErrorEntrega, match="error"):
        persist.confirmar_entrega(db, entrega.id)

    db.refresh(entrega)
    assert entrega.estado == "pendiente"
    assert db.scalar(select(func.count()).select_from(Interpretacion)) == 0


# --- C-2: una celda vacía es una ausencia explícita ---------------------------
def test_confirmar_persiste_ausencia_y_borra_valor_anterior(db, tmp_path,
                                                            monkeypatch):
    archivo = tmp_path / "entrega.xlsx"
    archivo.write_bytes(b"contenido")

    # Primera entrega: la evaluadora A informa clase 1.
    primera = _entrega(db, estado="pendiente", archivo=str(archivo),
                       semana="05", version=1)
    monkeypatch.setattr(persist, "importar_excel", lambda _contenido: ResultadoImportacion(
        datos={(1, 2020): {"A": ClaseCobertura.BOSQUE_NATURAL}},
    ))
    persist.confirmar_entrega(db, primera.id)
    assert persist._ultima_interpretacion(db, _proyecto(db).id, 1, 2020, "A").clase == 1

    # Corrección: la celda A queda vacía. Debe ganar la ausencia, no el valor
    # de la entrega anterior.
    correccion = _entrega(db, estado="pendiente", archivo=str(archivo),
                          semana="05", version=2)
    monkeypatch.setattr(persist, "importar_excel", lambda _contenido: ResultadoImportacion(
        datos={(1, 2020): {"A": None}},
    ))
    stats = persist.confirmar_entrega(db, correccion.id)

    ultima = persist._ultima_interpretacion(db, _proyecto(db).id, 1, 2020, "A")
    assert ultima is not None and ultima.clase is None
    assert stats["ausencias"] == 1
    cruce = db.scalar(select(ResultadoCruce).where(
        ResultadoCruce.punto_id == 1, ResultadoCruce.anio == 2020))
    assert cruce is not None and cruce.a is None


# --- A-1: manda la entrega más reciente, no la versión de otra semana ---------
def test_ultima_interpretacion_no_compara_versiones_de_semanas_distintas(db):
    # Semana 05 corregida dos veces (v2), recibida antes.
    vieja = _entrega(db, semana="05", version=2,
                     recibida_en=datetime(2026, 1, 10, 9, 0))
    _interpretacion(db, vieja, clase=1)
    # Semana 06 recién entregada (v1), recibida después.
    nueva = _entrega(db, semana="06", version=1,
                     recibida_en=datetime(2026, 1, 20, 9, 0))
    _interpretacion(db, nueva, clase=6)

    ultima = persist._ultima_interpretacion(db, _proyecto(db).id, 1, 2020, "A")
    assert ultima is not None
    assert ultima.clase == 6
    assert ultima.entrega_id == nueva.id


# --- A-2: recalcular no pisa una ficha cerrada por consenso -------------------
def test_recalcular_preserva_consenso_cerrado(db):
    a = _entrega(db, letra="A", recibida_en=datetime(2026, 1, 1))
    b = _entrega(db, letra="B", recibida_en=datetime(2026, 1, 1))
    c = _entrega(db, letra="C", recibida_en=datetime(2026, 1, 1))
    _interpretacion(db, a, clase=1)
    _interpretacion(db, b, clase=3)
    _interpretacion(db, c, clase=5)

    persist.recalcular_cruce(db, _proyecto(db).id, 1, 2020)
    db.commit()
    ficha = db.scalar(select(FichaQAQC))
    assert ficha is not None
    ficha.estado = EstadoQAQC.RESUELTO_POR_CONSENSO.value
    ficha.clase_definitiva = 7
    cruce = db.scalar(select(ResultadoCruce))
    cruce.clase_consolidada = 7
    cruce.regla_version = "qaqc-consenso"
    db.commit()

    # Llega una entrega posterior que cambia el valor de A.
    nueva = _entrega(db, letra="A", semana="07", version=1,
                     recibida_en=datetime(2026, 2, 1))
    _interpretacion(db, nueva, clase=2)
    persist.recalcular_cruce(db, _proyecto(db).id, 1, 2020)
    db.commit()

    actualizado = db.scalar(select(ResultadoCruce))
    assert actualizado.clase_consolidada == 7
    assert actualizado.regla_version == "qaqc-consenso"
    assert actualizado.a == 2  # los valores crudos sí se actualizan


# --- A-3: la ficha se abre solo para discrepancia absoluta o SC --------------
@pytest.mark.parametrize("valores", [
    {"A": 1, "B": 1, "C": 2},   # mayoría 2/3: se consolida sin ficha
    {"A": 1},                   # terna incompleta: no es un desacuerdo
])
def test_recalcular_no_crea_ficha_para_mayoria_ni_incompleto(db, valores):
    for letra, clase in valores.items():
        entrega = _entrega(db, letra=letra, recibida_en=datetime(2026, 1, 1))
        _interpretacion(db, entrega, clase=clase, letra=letra)

    cruce = persist.recalcular_cruce(db, _proyecto(db).id, 1, 2020)
    db.commit()

    assert cruce.resultado in ("mayoria", "incompleto")
    assert db.scalar(select(func.count()).select_from(FichaQAQC)) == 0


@pytest.mark.parametrize("valores,sc", [
    ({"A": 1, "B": 5, "C": 6}, False),   # tres clases distintas
    ({"A": 1, "B": 1, "C": None}, True),  # SC en terna completa
])
def test_recalcular_crea_ficha_para_discrepancia_o_sc(db, valores, sc):
    for letra, clase in valores.items():
        entrega = _entrega(db, letra=letra, recibida_en=datetime(2026, 1, 1))
        _interpretacion(db, entrega, clase=clase, letra=letra,
                        es_sc=(sc and letra == "C"))

    persist.recalcular_cruce(db, _proyecto(db).id, 1, 2020)
    db.commit()

    assert db.scalar(select(func.count()).select_from(FichaQAQC)) == 1


# --- Fase 1: historial de cruces ---------------------------------------------
def test_recalcular_registra_historial_append_only(db):
    for letra, clase in (("A", 1), ("B", 5), ("C", 6)):
        entrega = _entrega(db, letra=letra, recibida_en=datetime(2026, 1, 1))
        _interpretacion(db, entrega, clase=clase, letra=letra)

    persist.recalcular_cruce(db, _proyecto(db).id, 1, 2020)
    db.commit()

    fila = db.scalar(select(ResultadoCruceHistorial))
    assert fila is not None
    assert fila.punto_id == 1
    assert fila.anio == 2020
    assert fila.resultado == "discrepancia"
    assert fila.regla_version == "tabla7-v1"
    assert fila.actor == "sistema"


# --- A-4: la clase definitiva solo admite 1..9 --------------------------------
@pytest.mark.parametrize("codigo", [0, 10, 99, -1, "1", True])
def test_clase_a_dominio_rechaza_codigos_fuera_de_rango(codigo):
    with pytest.raises(ValueError):
        persist.clase_a_dominio(codigo)


def test_actualizar_ficha_rechaza_clase_fuera_de_rango(db):
    ficha = FichaQAQC(proyecto_id=_proyecto(db).id, punto_id=1, anio=2020,
                      estado=EstadoQAQC.DETECTADO.value)
    db.add(ficha)
    db.commit()

    for invalida in (0, 10, 99, -1):
        with pytest.raises(ValueError, match="1 al 9"):
            persist.actualizar_ficha(db, ficha.id, {"clase_definitiva": invalida})

    db.refresh(ficha)
    assert ficha.clase_definitiva is None

    persist.actualizar_ficha(db, ficha.id, {"clase_definitiva": 9})
    db.refresh(ficha)
    assert ficha.clase_definitiva == 9


def test_transicionar_ficha_rechaza_clase_invalida(db):
    ficha = FichaQAQC(
        proyecto_id=_proyecto(db).id, punto_id=1, anio=2020,
        estado=EstadoQAQC.PENDIENTE_APROBACION.value,
    )
    db.add(ficha)
    db.commit()

    with pytest.raises(ValueError, match="1-9"):
        persist.transicionar_ficha(db, ficha.id, "Aprobar consenso")


def test_la_bd_rechaza_clase_definitiva_cero(db):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.add(FichaQAQC(
            proyecto_id=_proyecto(db).id, punto_id=2, anio=2020,
            estado=EstadoQAQC.DETECTADO.value, clase_definitiva=0,
        ))
        db.commit()
    db.rollback()


def test_confirmar_entrega_revierte_si_falla_el_recalculo(db, tmp_path, monkeypatch):
    archivo = tmp_path / 'entrega.xlsx'
    archivo.write_bytes(b'contenido')
    entrega = _entrega(db, estado='pendiente', archivo=str(archivo))
    parsed = ResultadoImportacion(datos={(1, 2020): {'A': ClaseCobertura.BOSQUE_NATURAL}})
    monkeypatch.setattr(persist, 'importar_excel', lambda _contenido: parsed)

    def falla(*args, **kwargs):
        raise RuntimeError('fallo simulado')

    monkeypatch.setattr(persist, 'recalcular_cruce', falla)

    with pytest.raises(RuntimeError):
        persist.confirmar_entrega(db, entrega.id)

    db.expire_all()
    assert db.get(Entrega, entrega.id).estado == 'pendiente'
    assert db.scalar(select(func.count()).select_from(Interpretacion)) == 0


def test_crear_entrega_invalida_no_deja_archivo_huerfano(db, tmp_path, monkeypatch):
    monkeypatch.setattr(persist.settings, 'upload_dir', str(tmp_path))

    with pytest.raises(ValueError, match='xlsx'):
        persist.crear_entrega_pendiente(db, 'A', '2026-W01', 'avance', b'no es un xlsx')

    assert list(tmp_path.glob('*.xlsx')) == []


def test_crear_entrega_valida_letra_tipo_y_una_sola_evaluadora(db, monkeypatch):
    monkeypatch.setattr(persist, "importar_excel", lambda _contenido: ResultadoImportacion(
        datos={(1, 2020): {"A": ClaseCobertura.BOSQUE_NATURAL,
                           "B": ClaseCobertura.PASTIZALES}},
    ))

    with pytest.raises(ValueError, match="A, B o C"):
        persist.crear_entrega_pendiente(db, "D", "2026-W01", "avance", b"x")
    with pytest.raises(ValueError, match="tipo"):
        persist.crear_entrega_pendiente(db, "A", "2026-W01", "otro", b"x")
    with pytest.raises(ValueError, match="evaluadoras"):
        persist.crear_entrega_pendiente(db, "A", "2026-W01", "avance", b"x")


def test_cargar_mapa_reemplaza_clases_obsoletas(db):
    proyecto = _proyecto(db)
    persist.cargar_mapa(
        db, proyecto.id, b"punto,anio,clase\n1,2024,1\n2,2024,2\n", "mapa.csv")
    persist.cargar_mapa(
        db, proyecto.id, b"punto,anio,clase\n1,2024,3\n", "mapa.csv")

    filas = db.scalars(select(ClaseMapa).where(
        ClaseMapa.proyecto_id == proyecto.id)).all()
    assert [(f.punto_id, f.anio, f.clase) for f in filas] == [(1, 2024, 3)]


def test_transicionar_reemplazo_no_salta_la_autorizacion(db):
    proyecto = _proyecto(db)
    rep = Reemplazo(proyecto_id=proyecto.id, punto_origen=1,
                    punto_reserva=2, estado="propuesto")
    db.add(rep)
    db.commit()
    db.refresh(rep)

    with pytest.raises(ValueError, match="No se puede pasar"):
        persist.transicionar_reemplazo(db, rep.id, "completado")

    persist.transicionar_reemplazo(db, rep.id, "autorizado")
    persist.transicionar_reemplazo(db, rep.id, "completado")


def test_crear_entrega_audita_actor_y_cargador(db, tmp_path, monkeypatch):
    monkeypatch.setattr(persist.settings, 'upload_dir', str(tmp_path))
    monkeypatch.setattr(persist, 'importar_excel', lambda _contenido: ResultadoImportacion(
        datos={(1, 2020): {'A': ClaseCobertura.BOSQUE_NATURAL}},
    ))

    entrega, _ = persist.crear_entrega_pendiente(
        db, 'A', '2026-W01', 'avance', b'contenido',
        actor='usuario:42', cargado_por_id=42)

    fila = db.scalar(select(Auditoria).where(
        Auditoria.accion == 'entrega_recibida'))
    assert fila is not None
    assert fila.actor == 'usuario:42'
    assert entrega.cargada_por_id == 42
