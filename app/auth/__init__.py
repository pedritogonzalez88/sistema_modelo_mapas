"""Autenticación de la aplicación (Fase A1).

El paquete se mantiene ligero a propósito: los submódulos se importan de forma
explícita (``from app.auth import service``) para que no haya ciclos entre
``app.web`` (globals de Jinja), ``app.main`` (middlewares y routers) y este
paquete.

Mapa de responsabilidades:

- ``service``: sesiones, login local, bloqueo por intentos y cambio de
  contraseña.
- ``providers``: interfaz ``AuthProvider`` y ``LocalProvider`` (Argon2id).
  El proveedor LDAP llega en la Fase A5 y el TOTP en la A3.
- ``csrf``: token de formulario (cookie anónima o token de sesión) y validación
  de origen.
- ``dependencies``: ``get_current_user`` y ``require_roles`` para los routers.
- ``middleware``: ``AuthGateMiddleware`` y ``SecurityHeadersMiddleware``.
- ``audit``: registro de acciones con actor real, IP y agente.
- ``router``: endpoints ``/auth/*``.
"""

__all__ = ["audit", "csrf", "dependencies", "middleware", "providers", "router", "service"]
