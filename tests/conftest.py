"""Fixtures compartidas para la batería de pruebas de Evalua5."""
from io import BytesIO

import pytest
from openpyxl import Workbook


def _crear_libro(hojas):
    """Construye un libro .xlsx en memoria y devuelve su contenido en bytes.

    Cada hoja se especifica como un diccionario con las claves
    ``titulo``, ``encabezado`` y ``filas``.
    """
    libro = Workbook()
    libro.remove(libro.active)
    for hoja in hojas:
        ws = libro.create_sheet(title=hoja["titulo"])
        ws.append(hoja["encabezado"])
        for fila in hoja.get("filas", []):
            ws.append(fila)
    buffer = BytesIO()
    libro.save(buffer)
    libro.close()
    return buffer.getvalue()


@pytest.fixture
def crear_libro():
    """Factory que arma libros xlsx en memoria para el importador."""
    return _crear_libro
