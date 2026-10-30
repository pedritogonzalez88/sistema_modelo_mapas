"""Datos semilla nominales (cierre de autenticación).

Pruebas unitarias: no abren base de datos ni tocan la red. Verifican que la
fuente única describe exactamente a las cinco personas que operan el sistema y
que la política de segundo factor se deriva del rol.
"""
import pytest

from app.seed_data import (
    MFA_POLICY_POR_DEFECTO,
    ROLES_CON_TOTP,
    USUARIOS_SEMILLA,
    mfa_policy_para_rol,
)

CAMPOS = {"nombre", "username", "email", "rol", "letra_terna"}
ROLES_ESPERADOS = {"asistente": 3, "cargador": 1, "supervision": 1}


def test_hay_cinco_cuentas_nominales_con_todos_los_campos():
    assert len(USUARIOS_SEMILLA) == 5
    for datos in USUARIOS_SEMILLA:
        assert set(datos) == CAMPOS, datos
        assert datos["nombre"].strip()
        assert datos["username"].strip()
        assert datos["email"].strip()
        assert datos["rol"].strip()


def test_los_usuarios_y_correos_son_unicos_sin_distinguir_mayusculas():
    usuarios = [datos["username"].casefold() for datos in USUARIOS_SEMILLA]
    correos = [datos["email"].casefold() for datos in USUARIOS_SEMILLA]
    assert len(set(usuarios)) == len(USUARIOS_SEMILLA)
    assert len(set(correos)) == len(USUARIOS_SEMILLA)


def test_el_correo_sigue_la_convencion_institucional():
    for datos in USUARIOS_SEMILLA:
        assert datos["email"] == f"{datos['username']}@aep.gov.py"


def test_cada_persona_tiene_nombre_y_apellido():
    for datos in USUARIOS_SEMILLA:
        partes = datos["nombre"].split()
        assert len(partes) >= 2, datos["nombre"]


def test_la_terna_tiene_tres_asistentes_con_letras_a_b_c():
    asistentes = [datos for datos in USUARIOS_SEMILLA
                  if datos["rol"] == "asistente"]
    assert len(asistentes) == 3
    assert sorted(datos["letra_terna"] for datos in asistentes) == ["A", "B", "C"]


def test_los_roles_coinciden_con_la_operacion_del_sistema():
    conteo = {}
    for datos in USUARIOS_SEMILLA:
        conteo[datos["rol"]] = conteo.get(datos["rol"], 0) + 1
    assert conteo == ROLES_ESPERADOS


def test_solo_los_asistentes_llevan_letra_de_terna():
    for datos in USUARIOS_SEMILLA:
        if datos["rol"] == "asistente":
            assert datos["letra_terna"] in {"A", "B", "C"}
        else:
            assert datos["letra_terna"] is None, datos["username"]


@pytest.mark.parametrize("rol", ["admin", "supervision"])
def test_los_roles_criticos_exigen_totp(rol):
    assert rol in ROLES_CON_TOTP
    assert mfa_policy_para_rol(rol) == "totp"


@pytest.mark.parametrize("rol", ["cargador", "asistente", "consulta"])
def test_el_resto_de_roles_admite_cualquier_factor(rol):
    assert mfa_policy_para_rol(rol) == "any"


@pytest.mark.parametrize("rol", [None, "", "  ", "desconocido"])
def test_un_rol_desconocido_nunca_degrada_el_segundo_factor(rol):
    assert mfa_policy_para_rol(rol) == MFA_POLICY_POR_DEFECTO
    assert MFA_POLICY_POR_DEFECTO == "any"


def test_la_politica_no_distingue_mayusculas_ni_espacios():
    assert mfa_policy_para_rol(" Supervisión ".replace("ó", "o")) == "totp"
    assert mfa_policy_para_rol("ADMIN") == "totp"


def test_la_cli_expone_el_comando_seed_usuarios():
    from app import cli

    parser = cli._parser()
    args = parser.parse_args(["seed-usuarios"])
    assert args.comando == "seed-usuarios"
    assert args.func is cli._cmd_seed_usuarios
    assert args.con_password is False

    con_password = parser.parse_args(["seed-usuarios", "--con-password"])
    assert con_password.con_password is True
