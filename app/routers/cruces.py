from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permiso
from app.core.database import get_db
from app.services import persist
from app.web import templates

router = APIRouter(prefix="/cruces",
                   dependencies=[Depends(require_permiso("cruces.ver"))])

# Resultados posibles de la terna (tabla 7 del protocolo). Se usa para validar
# el filtro y para construir los chips con su conteo.
RESULTADOS = ("unanimidad", "mayoria", "discrepancia", "incompleto")

# Tamaños de página ofrecidos en la tabla. Cualquier otro valor cae a 50 para
# no permitir consultas arbitrariamente grandes.
PER_PAGE_OPCIONES = (25, 50, 100)
PER_PAGE_DEFECTO = 50


def _entero(valor: str | None) -> int | None:
    """Normaliza un parámetro de filtro numérico recibido por query string.

    El formulario envía cadenas vacías ("?punto=&anio=") cuando un filtro está
    en "Todos": con `int | None` FastAPI respondería 422 y el usuario perdería
    la vista. Un valor no numérico se ignora (equivale a no filtrar) en lugar de
    romper la página.
    """
    if valor is None:
        return None
    limpio = valor.strip()
    if not limpio:
        return None
    try:
        return int(limpio)
    except ValueError:
        return None


def _return_to(request: Request) -> str:
    """Ruta interna actual (path + query) para volver desde el detalle.

    Se devuelve en forma relativa ("/cruces?resultado=..."): el detalle solo
    acepta destinos que empiecen con "/" y no con "//", de modo que una URL
    absoluta se descartaria y se perderia el filtro aplicado.
    """
    destino = request.url.path
    if request.url.query:
        destino = f"{destino}?{request.url.query}"
    return destino


@router.get("", response_class=HTMLResponse)
def listar(request: Request, resultado: str | None = None,
           punto: str | None = None, anio: str | None = None,
           page: int = Query(1, ge=1),
           per_page: int = Query(PER_PAGE_DEFECTO),
           db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)

    if per_page not in PER_PAGE_OPCIONES:
        per_page = PER_PAGE_DEFECTO
    # Un resultado fuera del vocabulario se ignora (equivale a "todos") en lugar
    # de devolver una tabla vacía por un parámetro mal escrito.
    filtro_resultado = resultado if resultado in RESULTADOS else None
    filtro_punto = _entero(punto)
    filtro_anio = _entero(anio)

    filas, total = persist.listar_cruces(db, proyecto.id, filtro_resultado,
                                         filtro_punto, filtro_anio, page=page,
                                         per_page=per_page)
    paginas = max(1, ceil(total / per_page))
    # Los clientes que piden JSON (Accept: application/json) reciben la misma
    # página de cruces serializada; el HTML sigue siendo la respuesta por
    # defecto. Así el handler de errores no es el único que conserva un
    # contrato JSON para integraciones de solo lectura.
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({
            "total": total,
            "page": page,
            "pages": paginas,
            "per_page": per_page,
            "cruces": [
                {"punto": cruce.punto_id, "anio": cruce.anio,
                 "a": cruce.a, "b": cruce.b, "c": cruce.c,
                 "resultado": cruce.resultado,
                 "clase_consolidada": cruce.clase_consolidada,
                 "ficha_id": ficha_id}
                for cruce, ficha_id in filas
            ],
        })
    return templates.TemplateResponse(request, "cruces.html", {
        "proyecto": proyecto,
        "filas": filas,
        "total": total,
        "page": page,
        "pages": paginas,
        "per_page": per_page,
        "per_page_opciones": PER_PAGE_OPCIONES,
        "conteos": persist.conteo_resultados(db, proyecto.id),
        "resultados": RESULTADOS,
        "f_resultado": filtro_resultado or "",
        "f_punto": filtro_punto or "",
        "f_anio": filtro_anio or "",
        "return_to": _return_to(request),
    })


@router.get("/{punto}/{anio}", response_class=HTMLResponse)
def detalle(request: Request, punto: int, anio: int, db: Session = Depends(get_db)):
    proyecto = persist.get_default_proyecto(db)
    datos = persist.detalle_cruce(db, proyecto.id, punto, anio)
    if datos is None:
        raise HTTPException(status_code=404, detail="Cruce inexistente.")
    # Solo se aceptan rutas internas: descarta destinos absolutos
    # (https://evil.com) y protocol-relative (//evil.com), que serian una
    # redireccion abierta al pulsar "Volver a cruces".
    return_to = request.query_params.get("return_to") or "/cruces"
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = "/cruces"
    return templates.TemplateResponse(request, "cruce_detalle.html",
                                      {"proyecto": proyecto, "punto": punto,
                                       "anio": anio, "return_to": return_to,
                                       **datos})
