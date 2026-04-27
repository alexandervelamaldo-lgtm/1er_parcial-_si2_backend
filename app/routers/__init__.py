from . import inteligencia_automatizacion, pagos_facturacion
from .autenticacion_acceso import auth
from .gestion_operativa_web import clientes, notificaciones, talleres, tecnicos
from .gestion_solicitudes import solicitudes, vehiculos
from .seguimiento_cliente_web import mapa

__all__ = [
    "auth",
    "inteligencia_automatizacion",
    "clientes",
    "mapa",
    "notificaciones",
    "pagos_facturacion",
    "solicitudes",
    "talleres",
    "tecnicos",
    "vehiculos",
]
