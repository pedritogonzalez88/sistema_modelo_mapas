# Auditoría axe de las pantallas de autenticación

**Base:** http://127.0.0.1:8033 · **Generado:** 30/10/2026, 9:20:30 a. m.

## Resultado por pantalla y ancho

| Pantalla | Ruta | Ancho | HTTP | critical | serious | moderate | minor | Reglas | Nodos |
|---|---|---|---|---|---|---|---|---|---|
| Inicio de sesión (anónimo) | `/auth/login` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Recuperar acceso (anónimo) | `/auth/recuperar` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Enlace de restablecimiento inválido | `/auth/restablecer?token=invalido` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Seguridad (autenticador sin activar) | `/auth/seguridad` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Seguridad (códigos de respaldo recién generados) | `/auth/seguridad` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Seguridad (segundo factor y sesiones) | `/auth/seguridad` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Usuarios (administración) | `/auth/usuarios` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Auditoría (eventos y CSV) | `/auth/auditoria` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Verificación en dos pasos (challenge pendiente) | `/auth/mfa` | 1440 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Inicio de sesión (anónimo) | `/auth/login` | 390 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Recuperar acceso (anónimo) | `/auth/recuperar` | 390 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Enlace de restablecimiento inválido | `/auth/restablecer?token=invalido` | 390 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Seguridad (segundo factor y sesiones) | `/auth/seguridad` | 390 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Usuarios (administración) | `/auth/usuarios` | 390 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Auditoría (eventos y CSV) | `/auth/auditoria` | 390 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| Verificación en dos pasos (challenge pendiente) | `/auth/mfa` | 390 | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Total** | | | | **0** | **0** | **0** | **0** | **0** | **0** |

## Violaciones por regla

Sin violaciones en ninguna pantalla ni ancho.

## Reglas incompletas (requieren criterio humano)

- `color-contrast`: 1 nodos en seguridad@390

## Teclado

| Pantalla | Foco inicial | Primer Tab | Skip link alcanzable | Paradas | Trampa | Ocultos | Enter dispara envío |
|---|---|---|---|---|---|---|---|
| login-1440 | input #campo-login | input #campo-password (visible, anillo) | sí (visible) | 7 | no | 0 | sí |
| usuarios-1440 | body | a (visible, anillo) | sí (visible) | 21 | no | 0 | sí |
| mfa-1440 | input #campo-codigo | summary (visible, anillo) | sí (visible) | 6 | no | 0 | sí |
| login-390 | input #campo-login | input #campo-password (visible, anillo) | sí (visible) | 7 | no | 0 | sí |
| usuarios-390 | body | a (visible, anillo) | sí (visible) | 18 | no | 0 | sí |
| mfa-390 | input #campo-codigo | summary (visible, anillo) | sí (visible) | 6 | no | 0 | sí |
