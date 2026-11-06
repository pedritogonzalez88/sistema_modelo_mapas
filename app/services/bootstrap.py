"""Proyecto por defecto y semilla mínima de la base."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Auditoria, Proyecto, Usuario
from app.seed_data import USUARIOS_SEMILLA, mfa_policy_para_rol


def get_default_proyecto(db: Session) -> Proyecto:
    proyecto = db.scalars(select(Proyecto).order_by(Proyecto.id)).first()
    if proyecto is None:
        proyecto = Proyecto(nombre="BTR2 Paraguay", periodo="1990-2024", protocolo_version="EET-V4")
        db.add(proyecto)
        db.commit()
        db.refresh(proyecto)
    return proyecto


def seed(db: Session) -> None:
    get_default_proyecto(db)
    if db.scalar(select(func.count()).select_from(Usuario)) == 0:
        # Cuentas nominales desde la fuente única (app/seed_data.py). Nacen sin
        # contraseña: se asigna por CLI (``seed-usuarios --con-password``) o por
        # la administración de usuarios.
        db.add_all([
            Usuario(
                nombre=datos["nombre"],
                username=datos["username"],
                email=datos["email"],
                rol=datos["rol"],
                letra_terna=datos["letra_terna"],
                auth_provider="local",
                mfa_policy=mfa_policy_para_rol(datos["rol"]),
                is_active=True,
                must_change_password=False,
            )
            for datos in USUARIOS_SEMILLA
        ])
        db.commit()
    db.add(Auditoria(actor="sistema", accion="seed", entidad="sistema",
                     detalle={"msg": "seed verificado"}))
    db.commit()
