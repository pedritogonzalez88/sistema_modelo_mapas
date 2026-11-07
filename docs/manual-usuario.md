# Manual de usuario — Validador EET

## 1. Qué es

El **Validador EET** es la aplicación web que consolida y valida las entregas
semanales de la terna de evaluadores (A, B y C) frente al mapa de referencia
MADES, produce cruces y fichas QA/QC, gestiona reemplazos de puntos y exporta
el consolidado para el informe BTR2 de Paraguay.

URL local: `http://localhost:8000` (producción: `https://<dominio>`).

## 2. Acceso

1. Abra la URL en el navegador.
2. Ingrese **usuario** y **contraseña**.
3. Si su rol exige segundo factor (admin/supervisión), ingrese el código TOTP
   del autenticador o el código enviado por correo.
4. Si es su primer acceso, cambie la contraseña y enrolé el autenticador.

La sesión se cierra automáticamente tras inactividad. Use **Salir** para cerrarla.

## 3. Roles

| Rol | Alcance |
|---|---|
| Asistente (A/B/C) | Carga y consulta de sus propias entregas. |
| Carga | Carga de entregas, malla y mapa; lectura de análisis. |
| Supervisión | Todo lo anterior + dictamen QA/QC y autorización de reemplazos. |
| Consulta | Lectura de cruces, QA/QC, reemplazos, malla, mapa y exportación. |
| Administración | Gestión de usuarios, auditoría y políticas. No dictamina ni autoriza reemplazos (separación de deberes). |

## 4. Flujos principales

### 4.1 Entregas

- **Listado** (`/entregas`): entregas recibidas, estado y versión.
- **Carga** (`/entregas/upload`): suba el `.xlsx` del evaluador, indicando letra
  de terna, semana y tipo. El sistema valida formato y registra SHA-256.
- **Confirmación**: una entrega válida se confirma para recalcular cruces.
- **Rechazo**: se rechaza si tiene errores de validación.

### 4.2 Cruces de la terna

- `/cruces`: tabla consolidada por punto/año con clases A, B, C, resultado
  (mayoría, discrepancia, SC) y clase consolidada.
- Filtros por resultado y paginación.
- Detalle por punto con origen de cada interpretación.

### 4.3 QA/QC

- `/qaqc`: fichas detectadas automáticamente (discrepancia absoluta o SC).
- Supervisión inicia revisión, agrega evidencias/sensores y decide
  (consenso o reemplazo).
- Carga y consulta ven las fichas en modo lectura.

### 4.4 Reemplazos

- `/reemplazos`: propuesta de reemplazo de punto por otro.
- Supervisión autoriza o rechaza; el flujo queda auditado.

### 4.5 Malla oficial y mapa MADES

- `/malla`: carga/consulta de la malla oficial de puntos.
- `/mapa`: carga/consulta del mapa de referencia MADES.

### 4.6 Exportación

- `/exportar`: descarga del consolidado en XLSX (`/exportar/consolidado.xlsx`).

## 5. Administración

- `/auth/usuarios`: alta, edición, desactivación y políticas MFA de usuarios.
- `/auth/auditoria`: bitácora de acciones (logins, cambios, accesos denegados).
- Recuperación de contraseña por correo.
- Códigos de respaldo de un solo uso.

## 6. Errores comunes

- **403 Acceso restringido**: su rol no tiene permiso para esa acción.
- **Sesión expirada**: vuelva a iniciar sesión.
- **Archivo inválido**: revise el formato del `.xlsx` (hojas `Evaluador_A/B/C`,
  columnas `ID_EV`, `ID_PUNTO`, `A2008`, etc.).
- **MFA bloqueado**: use un código de respaldo o contacte a administración.

## 7. Soporte

Contacte al responsable de la aplicación o a TI/operaciones. Incluya el
`request_id` que aparece en la página de error para facilitar el diagnóstico.
