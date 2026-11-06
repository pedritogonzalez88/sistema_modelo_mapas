"""Utilidades de exportación segura a Excel/CSV.

Excel evalúa como fórmula cualquier celda de texto que empiece por ``=``,
``+``, ``-`` o ``@`` (y también las que comienzan con tabulación o retorno de
carro). Un valor proveniente de la base o de un archivo cargado podría ejecutar
una fórmula al abrir el export. Se antepone un apóstrofo para forzar texto.
"""
from __future__ import annotations

_PELIGROSOS = {"=", "+", "-", "@", "\t", "\r", "\n"}


def celda_segura(valor):
    """Devuelve el valor listo para una celda de Excel sin inyección de fórmulas.

    Los tipos no textuales (números, fechas, ``None``) se conservan para que
    Excel los trate como datos. Solo se prefija el texto peligroso.
    """
    if valor is None:
        return ""
    if not isinstance(valor, str):
        return valor
    if valor[:1] in _PELIGROSOS or valor.lstrip()[:1] in _PELIGROSOS:
        return "'" + valor
    return valor
