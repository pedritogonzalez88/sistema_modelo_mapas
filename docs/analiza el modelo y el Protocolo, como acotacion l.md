<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# analiza el modelo y el Protocolo, como acotacion la AEP es la encargada de hacer las ETT, se tendran 3 asistentes que haran foto interpretación van a mandar un archivo excel de manera semanal y una persona debera cargar y guaradar en una base de datos, se debe hacer el cruce de las 3 foto interpretaciones y muestre los resultados de las mismas a traves de un dashboard de las fotointerpretaciones, tambien una seccion para carga de las FICHA DE CONTROL DE CALIDAD (QA/QC): RESOLUCIÓN DE DISCREPANCIAS ABSOLUTAS

El modelo de Excel y el protocolo permiten definir un sistema de gestión de fotointerpretaciones para la AEP con cuatro funciones centrales: recepción semanal de archivos, almacenamiento versionado, cruce automático de la terna y resolución documentada de discrepancias mediante fichas QA/QC.

La decisión principal es preservar la **trazabilidad**: el sistema debe guardar por separado lo que interpretó cada asistente, lo que calculó el cruce y lo que finalmente resolvió la supervisión. La clase consolidada nunca debe sobrescribir las interpretaciones originales.

A continuación distingo los requisitos establecidos en los documentos de las decisiones de implementación propuestas.

## 1. Responsabilidad institucional y organización operativa

Tu aclaración coincide con el objetivo general del protocolo: la AEP ejecuta la Evaluación de Exactitud Temática de los mapas desarrollados por el MADES, como entidad colaborativa independiente. Para la documentación del sistema utilizaría la sigla EET, que es la empleada en el protocolo; conviene unificarla con “ETT” antes de redactar los requerimientos definitivos.[^1_1]

El flujo operativo quedaría organizado así:


| Actor | Responsabilidad | Restricción propuesta |
| :-- | :-- | :-- |
| Asistente A | Fotointerpretar y entregar su Excel semanal. | No consultar las interpretaciones de B y C antes del cierre independiente. |
| Asistente B | Fotointerpretar y entregar su Excel semanal. | No consultar las interpretaciones de A y C antes del cierre independiente. |
| Asistente C | Fotointerpretar y entregar su Excel semanal. | No consultar las interpretaciones de A y B antes del cierre independiente. |
| Responsable de carga | Recibir, validar e importar los tres archivos. | No decidir clases ni modificar silenciosamente los archivos recibidos. |
| Supervisión AEP | Revisar discrepancias, registrar dictámenes y autorizar reemplazos. | Toda decisión debe quedar asociada a evidencias y responsables. |
| Consulta institucional | Consultar resultados autorizados y exportaciones. | Acceso según alcance y etapa de publicación. |

La independencia de la terna no es un detalle de interfaz: el protocolo exige evaluaciones ciegas, separadas e independientes. Por tanto, el dashboard comparativo debe reservarse a los roles autorizados y no exponerse a los asistentes durante la interpretación inicial.[^1_1]

## 2. Análisis del modelo de Excel

### 2.1. Estructura existente

La planilla contiene tres hojas: `Evaluadora_A`, `Evaluadora_B` y `Evaluadora_C`. Cada una utiliza:

- `ID_EV`.
- `ID_PUNTO`.
- Las columnas anuales `A1990` a `A2024`.
- `X`, `Y`.
- `Notas`.[^1_2]

Esto representa una interpretación por asistente y punto, con 35 valores anuales. Es compatible con la estructura descrita en el protocolo.[^1_2][^1_1]

La extracción disponible muestra principalmente una plantilla sin clasificaciones cargadas; por ello, este análisis evalúa la estructura y el proceso, no resultados reales de concordancia.

### 2.2. Qué conservar y qué agregar

Propongo conservar las columnas actuales para no cambiar el trabajo de los asistentes. Los datos administrativos adicionales pueden capturarse en la pantalla de importación:


| Dato adicional | Utilidad |
| :-- | :-- |
| Proyecto y versión del protocolo | Identificar el marco metodológico aplicado. |
| Asistente responsable | Vincular `A`, `B` o `C` con una persona concreta. |
| Semana o período de entrega | Identificar la recepción semanal. |
| Tipo de entrega | Diferenciar avance incremental, acumulado o corrección. |
| Versión de entrega | Controlar rectificaciones. |
| Fecha de recepción y usuario cargador | Auditar quién incorporó el archivo. |
| Archivo original y huella digital | Conservar evidencia y detectar duplicados. |
| Versión de plantilla | Validar el formato esperado. |

Hay una decisión que debe quedar explícita: si cada semana se entregan solamente puntos nuevos o una planilla acumulada. El sistema puede admitir ambos formatos, pero no debe inferirlo automáticamente.

### 2.3. Validaciones de importación

Antes de guardar una entrega como aceptada, propongo verificar:

1. Encabezados y columnas anuales esperadas.
2. Correspondencia entre hoja, `ID_EV` y asistente seleccionado.
3. Existencia del `ID_PUNTO` en la malla oficial.
4. Ausencia de puntos duplicados dentro de la entrega.
5. Clasificaciones enteras entre 1 y 9.
6. Valores vacíos tratados como ausencia de interpretación, nunca como cero.
7. Coordenadas contrastadas contra la malla y su sistema de referencia.
8. Cambios respecto de entregas anteriores.
9. Notas de reemplazo coherentes con autorizaciones registradas.

El protocolo utiliza códigos de cobertura del 1 al 9 y dispone valores nulos para los puntos reemplazados; ambos criterios deben preservarse.[^1_1]

La importación debería mostrar una vista previa con filas válidas, errores y diferencias antes de confirmar. Un archivo rechazado puede conservarse como evidencia de recepción, pero sus datos no deben entrar al conjunto aceptado.

## 3. Flujo semanal propuesto

```text
Fotointerpretación independiente A / B / C
                    │
                    ▼
             Entregas semanales
                    │
                    ▼
        Responsable de carga centralizada
                    │
                    ▼
      Validación y comparación de versiones
           ├── Errores → devolución
           └── Aceptado
                    │
                    ▼
     Archivo original + registros versionados
                    │
                    ▼
           Cruce por punto y año
         ├── Incompleto
         ├── Unanimidad
         ├── Mayoría 2 de 3
         └── Discrepancia absoluta → ficha QA/QC
                    │
                    ▼
       Consolidación y dashboard autorizado
```

La semana identifica la entrega; no debe ser la llave del cruce. Si A entrega un punto una semana y C lo entrega después, ambos registros deben encontrarse mediante:

```text
Proyecto + versión de malla + ID_PUNTO + año
```

Cada interpretación individual añade a esa llave el asistente y la versión de entrega.

## 4. Reglas para cruzar las tres fotointerpretaciones

El protocolo establece unanimidad, mayoría de dos evaluadores, discrepancia absoluta resuelta mediante debate y discrepancia irresoluble con código `SC`.[^1_1]

La implementación debe aplicar estas reglas por punto y año:


| A | B | C | Resultado | Clase consolidada |
| --: | --: | --: | :-- | :-- |
| 1 | 1 | 1 | Unanimidad | 1 |
| 1 | 1 | 5 | Mayoría | 1 |
| 1 | 5 | 1 | Mayoría | 1 |
| 5 | 1 | 1 | Mayoría | 1 |
| 1 | 5 | 6 | Discrepancia absoluta | Pendiente de QA/QC |
| 1 | 1 | Vacío | Terna incompleta | Pendiente |
| Vacío | Vacío | Vacío | Sin interpretación | Pendiente |

Los valores son ejemplos ilustrativos, no resultados de la planilla.

### Precauciones de implementación

- Dos valores iguales y un tercero ausente no constituyen una mayoría válida de una terna completa.
- La clasificación minoritaria deja de determinar el consenso, pero permanece almacenada.
- Una discrepancia absoluta no debe resolverse seleccionando arbitrariamente una moda.
- `SC` debe almacenarse como estado de resolución, no como código de cobertura.
- Una nueva versión de interpretación debe recalcular los cruces afectados y revisar la vigencia de las fichas asociadas.

También deben distinguirse dos niveles:

- Estado del punto-año: por ejemplo, discrepancia en 2007.
- Estado del punto completo: por ejemplo, punto reemplazado por decisión formal.

Esta separación importa porque el desacuerdo se detecta para una fecha, mientras que el protocolo contempla anular la posición geográfica y activar una reserva interpretada durante toda la serie.[^1_1]

## 5. Sección de fichas QA/QC

### 5.1. Creación y carga

La sección debe permitir:

1. Crear automáticamente un caso cuando aparezcan tres clases distintas.
2. Completar digitalmente la ficha de resolución.
3. Adjuntar una ficha externa firmada.
4. Incorporar imágenes y documentos de evidencia.
5. Registrar revisión, aprobación y cierre.

No propongo limitar el módulo a subir un PDF: el dictamen debe quedar en campos estructurados para actualizar el consenso y alimentar el dashboard.

### 5.2. Campos de la ficha

El formato del protocolo incluye identificación del punto, clasificaciones iniciales, insumos analizados, resolución final y firmas.[^1_1]


| Bloque | Campos propuestos |
| :-- | :-- |
| Identificación | Número de ficha, proyecto, punto, coordenadas, sistema de referencia, año o años afectados y fecha de sesión. |
| Interpretaciones iniciales | Valores A, B y C, responsables y versiones de origen. |
| Evidencias | Landsat, Sentinel-2, Google Earth, fechas de imágenes, índices, capturas y otros datos complementarios. |
| Debate técnico | Observaciones, criterios examinados y fundamento de la decisión. |
| Dictamen | Consenso técnico alcanzado o sin consenso. |
| Clase definitiva | Código 1–9 cuando se alcanza consenso. |
| Reemplazo | Causa, punto de reserva, estrato de origen y autorización. |
| Conformidad | Participantes, supervisión, fechas y ficha firmada. |

El año afectado debe ser un campo obligatorio en el sistema: sin él, no se identifica inequívocamente qué cruce se está resolviendo.

### 5.3. Estados del expediente

```text
DETECTADO
   → EN_REVISIÓN
   → PENDIENTE_DE_APROBACIÓN
       ├── RESUELTO_POR_CONSENSO
       └── SIN_CONSENSO
              → REEMPLAZO_AUTORIZADO
              → RESERVA_EN_INTERPRETACIÓN
              → REEMPLAZO_COMPLETADO
```

Para cerrar por consenso exigiría clase definitiva, sustento técnico y conformidades registradas. Para cerrar un reemplazo exigiría autorización y referencia al punto de reserva.

El protocolo prohíbe generar reservas ad hoc y exige que el punto activado sea sometido al mismo proceso de interpretación ciega por la terna. Por tanto, autorizar una reserva no equivale a tener un dato de referencia listo.[^1_1]

## 6. Diseño del dashboard

### 6.1. Seguimiento de entregas

- Entregas recibidas por semana y asistente.
- Entregas aceptadas, rechazadas y corregidas.
- Puntos y observaciones anuales incorporadas.
- Pendientes de cada asistente.
- Fecha de última recepción.

Conviene mostrar puntos y observaciones punto-año por separado: completar un punto para toda la serie representa 35 observaciones por asistente.

### 6.2. Concordancia de la terna

- Ternas completas.
- Unanimidades.
- Mayorías.
- Discrepancias absolutas.
- Casos incompletos.
- Concordancia por año.
- Comparaciones A–B, A–C y B–C.
- Clases que concentran más desacuerdos.

Las proporciones deben indicar su denominador. Por ejemplo:

$$
\text{Unanimidad}=
\frac{\text{puntos-año con A=B=C}}
{\text{puntos-año con terna completa}}\times100
$$

No se deben incluir registros incompletos como desacuerdos.

### 6.3. Tabla comparativa central

| Punto | Año | A | B | C | Resultado | Clase final | Ficha QA/QC |
| :-- | --: | --: | --: | --: | :-- | --: | :-- |
| 120 | 1990 | 1 | 1 | 1 | Unanimidad | 1 | — |
| 120 | 1991 | 1 | 5 | 1 | Mayoría | 1 | — |
| 120 | 1992 | 1 | 5 | 6 | Discrepancia | — | Pendiente |
| 121 | 1990 | 6 | — | 6 | Incompleto | — | — |

Esta tabla es ilustrativa. Cada fila debe permitir abrir el historial, las entregas de origen y la resolución correspondiente.

### 6.4. Seguimiento QA/QC y mapa

- Fichas pendientes, en revisión y cerradas.
- Tiempo transcurrido desde la detección.
- Casos resueltos por consenso.
- Casos sin consenso.
- Reservas autorizadas y todavía pendientes de interpretación.
- Mapa de puntos filtrado por estado.
- Trayectoria anual del punto y alertas de transición.

Las restricciones temporales del protocolo deben generar alertas de revisión, no cambios automáticos de clase.[^1_1]

## 7. Modelo de base de datos propuesto

Propongo PostgreSQL con PostGIS y un modelo relacional normalizado. Excel quedaría como formato de intercambio, no como estructura interna de almacenamiento.


| Entidad | Contenido |
| :-- | :-- |
| `proyectos` | Alcance, período, protocolo y configuración. |
| `usuarios` y `asignaciones_terna` | Personas, roles y correspondencia A/B/C. |
| `clases_cobertura` | Códigos 1–9 y agrupación en categorías IPCC. |
| `mallas` y `puntos_muestreo` | Identificadores, geometría y versión de malla. |
| `metadatos_muestreo` | Origen, estrato, reserva, orden y parámetros del diseño. |
| `entregas` | Archivo, asistente, semana, versión y resultado de validación. |
| `interpretaciones` | Punto, año, asistente, clase y entrega de origen. |
| `ejecuciones_cruce` | Fecha, versión de reglas y conjunto procesado. |
| `resultados_cruce` | Estado, clase propuesta e interpretaciones utilizadas. |
| `fichas_qaqc` y `resoluciones_qaqc` | Expediente y dictamen. |
| `evidencias_qaqc` | Adjuntos y metadatos. |
| `reemplazos` | Punto original, reserva, causa y autorización. |
| `auditoria` | Acciones, responsables y cambios. |

En vez de guardar 35 columnas anuales en la tabla de interpretaciones, propongo:

```text
proyecto | punto | año | asistente | clase | entrega | versión
```

Las exportaciones pueden reconstruir después las columnas `A1990`–`A2024`.

Las coordenadas deben pertenecer a la malla oficial. Una entrega semanal con coordenadas diferentes debe generar una observación o rechazo; no debe desplazar automáticamente el punto.

## 8. Aspectos del protocolo que requieren aclaración

### 8.1. Concordancia no equivale a exactitud cartográfica

El cruce A/B/C construye la referencia consolidada. La EET compara posteriormente esa referencia con la clase del mapa MADES.

El protocolo requiere matrices de confusión anuales, matrices ponderadas, estimadores de exactitud y superficies ajustadas con incertidumbre. La planilla de fotointerpretación, por sí sola, no contiene todos los insumos necesarios para esos productos.[^1_2][^1_1]

Por ello, separaría:

- Dashboard operativo de fotointerpretaciones y QA/QC.
- Dashboard estadístico de EET, habilitado cuando se integren mapas y metadatos de muestreo.


### 8.2. Metadatos del diseño muestral ausentes en la planilla

El Excel no incluye `ORIGEN`, estrato ni condición de reserva, mientras que el protocolo requiere información del diseño para el análisis ponderado y muestra el campo `ORIGEN` en la estructura de consolidación.[^1_2][^1_1]

Estos atributos deben incorporarse desde la malla y los archivos del diseño muestral, sin mostrarlos a los asistentes cuando puedan revelar la clase del mapa.

Además, debe documentarse cómo se conservarán las probabilidades y ponderaciones al unificar muestras procedentes de cuatro años. El sistema no debería fabricar esos parámetros a partir de los conteos observados.

### 8.3. Regla de “forzar acierto” en planicies de aluvión

El apartado 4.12 propone forzar el estado a “Acierto de Clasificación” para determinadas transiciones agua–bosque cuando se verifica su ubicación en una planicie de aluvión. Esto entra en tensión con el apartado 4.10, que indica que las restricciones deben utilizarse para detectar y revisar errores, no para corregir automáticamente.[^1_1]

Propongo no implementar esa regla como corrección automática hasta recibir una definición metodológica formal. En el sistema puede registrarse como alerta y evidencia contextual para revisión.

### 8.4. Correspondencia entre evaluadores y supervisión

La ficha menciona “Evaluadora A-Supervisora AEP”, mientras que tu organización contempla tres asistentes y una persona encargada de cargar los archivos.[^1_1]

Los permisos deben definirse por función, no por la letra A. Aunque una persona acumule funciones, la aplicación debe distinguir carga, interpretación, supervisión y aprobación.

## 9. Alcance de implementación recomendado

### Primera etapa: sistema operativo

1. Configuración del proyecto, terna y malla oficial.
2. Importación semanal con validación y versionado.
3. Almacenamiento de archivos originales.
4. Cruce automático por punto-año.
5. Dashboard de avances y concordancia.
6. Fichas QA/QC digitales y adjuntos.
7. Autorización y seguimiento de reservas.
8. Exportación consolidada y auditoría.

### Segunda etapa: análisis EET

1. Incorporación de clases de los mapas anuales.
2. Validación de metadatos y ponderaciones del diseño.
3. Matrices de confusión y matrices de error ponderadas.
4. Exactitud global, del usuario y del productor.
5. Kappa, según el alcance del protocolo.
6. Superficies ajustadas, errores estándar e intervalos de confianza.

Como arquitectura inicial propongo un monolito modular con Laravel, PostgreSQL/PostGIS, procesos en cola para importaciones y almacenamiento privado de documentos. Python puede incorporarse para el procesamiento estadístico y geoespacial, sin fragmentar prematuramente el sistema en microservicios.

El criterio de aceptación esencial será poder reconstruir cualquier clase final: identificar las tres interpretaciones que la originaron, las versiones de sus archivos, la regla de consolidación aplicada y, cuando corresponda, la ficha QA/QC y la autorización de reemplazo.

<div align="center">⁂</div>

