# Lighthouse — Validador EET (AEP)

**Base:** http://localhost:8000  
**Chrome:** `C:\Program Files\Google\Chrome\Application\chrome.exe` (headless=new, --no-sandbox)  
**Lighthouse:** 13.6.0  
**Fecha de ejecución:** 2026-10-29T21:32:44.116Z

Categorías solicitadas: `accessibility`, `performance`, `best-practices`, `seo`.
Las corridas «móvil» usan la emulación por defecto de Lighthouse (Moto G Power, CPU 4x,
red 4G lenta); «escritorio» usa `--preset=desktop`.

## Puntajes

| Ruta | Forma | Rendimiento | Accesibilidad | Buenas prácticas | SEO |
|---|---|---|---|---|---|
| `/` | móvil | 100 | 100 | 100 | 100 |
| `/cruces` | móvil | 99 | 100 | 100 | 100 |
| `/qaqc/1` | móvil | 100 | 100 | 100 | 100 |
| `/` | escritorio | 100 | 100 | 100 | 100 |

## Métricas de laboratorio

| Ruta | Forma | FCP | LCP | TBT | CLS | Speed Index |
|---|---|---|---|---|---|---|
| `/` | móvil | 1.1 s | 1.1 s | 0 ms | 0 | 1.1 s |
| `/cruces` | móvil | 1.7 s | 1.7 s | 0 ms | 0 | 1.7 s |
| `/qaqc/1` | móvil | 1.1 s | 1.1 s | 0 ms | 0 | 1.1 s |
| `/` | escritorio | 0.3 s | 0.3 s | 0 ms | 0 | 0.3 s |

## Audits de accesibilidad no perfectos

Ninguno: las cuatro corridas obtienen **100** en `accessibility` (todos los audits
ponderados pasan).
## Artefactos

- `inicio-movil`: [JSON](inicio-movil.report.json) · [HTML](inicio-movil.report.html)
- `cruces-movil`: [JSON](cruces-movil.report.json) · [HTML](cruces-movil.report.html)
- `qaqc-1-movil`: [JSON](qaqc-1-movil.report.json) · [HTML](qaqc-1-movil.report.html)
- `inicio-escritorio`: [JSON](inicio-escritorio.report.json) · [HTML](inicio-escritorio.report.html)

## Confirmación con el CSS recompilado

Los cuatro recorridos se repitieron contra `uvicorn` en `http://127.0.0.1:8018`, que sirve
el CSS regenerado con `npm run css:build` (la imagen Docker de `:8000` no se reconstruyó).
Los puntajes son idénticos: **100** en accesibilidad en las cuatro corridas, **100** en
buenas prácticas y SEO, y rendimiento **100 / 99 / 100 / 100** (el 99 de `/cruces` móvil
proviene de FCP y LCP, no de accesibilidad).

Evidencia: [`uvicorn-8018/resumen.md`](uvicorn-8018/resumen.md).
