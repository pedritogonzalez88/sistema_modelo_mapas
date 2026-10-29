from app.domain.classes import ClaseCobertura
from app.domain.cross_engine import TipoResultado
from app.services.importer import (
    importar_excel,
    ERROR_EVALUADORA_DESCONOCIDA,
    ERROR_ID_PUNTO_INVALIDO,
    ERROR_ETIQUETA_INVALIDA,
)
from app.services.store import Store


def test_importar_evaluadoras_tradicional(crear_libro):
    hojas = [
        {
            "titulo": "Evaluadora_A",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991", "X", "Y", "Notas"],
            "filas": [["A", 101, 1, 2, -57.5, -25.3, None]],
        },
        {
            "titulo": "Evaluadora_B",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991", "X", "Y", "Notas"],
            "filas": [["B", 101, 1, 5, -57.5, -25.3, None]],
        },
        {
            "titulo": "Evaluadora_C",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991", "X", "Y", "Notas"],
            "filas": [["C", 101, 1, 2, -57.5, -25.3, None]],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0
    assert (101, 1990) in res.datos
    assert res.datos[(101, 1990)] == {
        "A": ClaseCobertura.BOSQUE_NATURAL,
        "B": ClaseCobertura.BOSQUE_NATURAL,
        "C": ClaseCobertura.BOSQUE_NATURAL,
    }


def test_importar_evaluadores_masculino(crear_libro):
    hojas = [
        {
            "titulo": "Evaluador_A",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991", "X", "Y", "Notas"],
            "filas": [["Evaluador_A", 101, 1, 2, -57.5, -25.3, None]],
        },
        {
            "titulo": "Evaluador_B",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991", "X", "Y", "Notas"],
            "filas": [["Evaluador_B", 101, 1, 5, -57.5, -25.3, None]],
        },
        {
            "titulo": "Evaluador_C",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991", "X", "Y", "Notas"],
            "filas": [["Evaluador_C", 101, 1, 2, -57.5, -25.3, None]],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0
    assert (101, 1990) in res.datos
    assert res.datos[(101, 1990)] == {
        "A": ClaseCobertura.BOSQUE_NATURAL,
        "B": ClaseCobertura.BOSQUE_NATURAL,
        "C": ClaseCobertura.BOSQUE_NATURAL,
    }


def test_importar_todos_en_una_sola_hoja(crear_libro):
    hojas = [
        {
            "titulo": "Evaluadores",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991", "X", "Y", "Notas"],
            "filas": [
                ["Evaluador_A", 101, 1, 2, -57.5, -25.3, None],
                ["Evaluador_B", 101, 1, 5, -57.5, -25.3, None],
                ["Evaluador_C", 101, 1, 2, -57.5, -25.3, None],
            ],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0
    assert (101, 1990) in res.datos
    assert res.datos[(101, 1990)] == {
        "A": ClaseCobertura.BOSQUE_NATURAL,
        "B": ClaseCobertura.BOSQUE_NATURAL,
        "C": ClaseCobertura.BOSQUE_NATURAL,
    }


def test_importar_asistentes_y_letras_directas(crear_libro):
    hojas = [
        {
            "titulo": "Asistente A",
            "encabezado": ["ASISTENTE", "ID_PUNTO", "A1990", "X", "Y"],
            "filas": [["Asistente A", 200, "Bosque natural", -56.0, -24.0]],
        },
        {
            "titulo": "Asistente B",
            "encabezado": ["ASISTENTE", "ID_PUNTO", "A1990", "X", "Y"],
            "filas": [["Asistente B", 200, "Pastizales", -56.0, -24.0]],
        },
        {
            "titulo": "Asistente C",
            "encabezado": ["ASISTENTE", "ID_PUNTO", "A1990", "X", "Y"],
            "filas": [["Asistente C", 200, "Bosque natural", -56.0, -24.0]],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0
    assert res.datos[(200, 1990)] == {
        "A": ClaseCobertura.BOSQUE_NATURAL,
        "B": ClaseCobertura.PASTIZALES,
        "C": ClaseCobertura.BOSQUE_NATURAL,
    }


def test_importar_hojas_con_letras_directas_a_b_c(crear_libro):
    hojas = [
        {
            "titulo": "A",
            "encabezado": ["ID_EV", "PUNTO", "1990"],
            "filas": [["A", 300, 1]],
        },
        {
            "titulo": "B",
            "encabezado": ["ID_EV", "PUNTO", "1990"],
            "filas": [["B", 300, 1]],
        },
        {
            "titulo": "C",
            "encabezado": ["ID_EV", "PUNTO", "1990"],
            "filas": [["C", 300, 1]],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0
    assert res.datos[(300, 1990)] == {
        "A": ClaseCobertura.BOSQUE_NATURAL,
        "B": ClaseCobertura.BOSQUE_NATURAL,
        "C": ClaseCobertura.BOSQUE_NATURAL,
    }


def test_importar_valores_float_y_encabezados_flexibles(crear_libro):
    hojas = [
        {
            "titulo": "Evaluador A",
            "encabezado": ["ID_EVALUADOR", "PUNTO", "1990", "1991", "X", "Y", "Notas"],
            "filas": [["A", 101, 1.0, 2.0, -57.5, -25.3, None]],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0
    assert (101, 1990) in res.datos
    assert res.datos[(101, 1990)] == {
        "A": ClaseCobertura.BOSQUE_NATURAL,
    }


def test_importar_hoja_evaluador_sin_columna_id_ev_asume_hoja(crear_libro):
    hojas = [
        {
            "titulo": "Evaluador_B",
            "encabezado": ["ID_PUNTO", "A1990", "X", "Y"],
            "filas": [[101, 5, -57.5, -25.3]],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0
    assert res.datos[(101, 1990)] == {
        "B": ClaseCobertura.PASTIZALES,
    }


def test_importar_y_ejecutar_cruce_con_store(crear_libro):
    hojas = [
        {
            "titulo": "Evaluador_A",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991"],
            "filas": [
                ["A", 1, 1, 1],  # 1990: 1, 1991: 1
            ],
        },
        {
            "titulo": "Evaluador_B",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991"],
            "filas": [
                ["B", 1, 1, 5],  # 1990: 1, 1991: 5
            ],
        },
        {
            "titulo": "Evaluador_C",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990", "A1991"],
            "filas": [
                ["C", 1, 1, 1],  # 1990: 1, 1991: 1
            ],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 0

    store = Store()
    total = store.cargar(res.datos)
    assert total == 6  # 2 años x 3 evaluadores = 6 evaluaciones

    cruce_1990 = store.ejecutar_cruce(1, 1990)
    assert cruce_1990.consenso == ClaseCobertura.BOSQUE_NATURAL
    assert cruce_1990.resultado == TipoResultado.VALIDACION_AUTOMATICA

    cruce_1991 = store.ejecutar_cruce(1, 1991)
    assert cruce_1991.consenso == ClaseCobertura.BOSQUE_NATURAL
    assert cruce_1991.resultado == TipoResultado.REQUIERE_REVISION
    assert cruce_1991.minorias == [ClaseCobertura.PASTIZALES]


def test_deteccion_errores_importacion(crear_libro):
    hojas = [
        {
            "titulo": "Evaluador_A",
            "encabezado": ["ID_EV", "ID_PUNTO", "A1990"],
            "filas": [
                ["A", "PUNTO_INVALIDO_SIN_NUMERO", 1],
                ["A", 102, "Clase Inexistente 99"],
                ["B", 103, 1],  # Discrepancia con hoja Evaluador_A
            ],
        },
    ]
    contenido = crear_libro(hojas)
    res = importar_excel(contenido)
    assert len(res.errores) == 3
    tipos_error = [e["tipo"] for e in res.errores]
    assert ERROR_ID_PUNTO_INVALIDO in tipos_error
    assert ERROR_ETIQUETA_INVALIDA in tipos_error
    assert ERROR_EVALUADORA_DESCONOCIDA in tipos_error
