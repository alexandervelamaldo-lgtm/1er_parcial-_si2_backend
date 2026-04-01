from app.models.clientes import Cliente
from app.models.estados_solicitud import EstadoSolicitud
from app.models.historial_eventos import HistorialEvento
from app.models.notificaciones import Notificacion
from app.models.operadores import Operador
from app.models.roles import Role
from app.models.solicitudes import Solicitud
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.tipos_incidente import TipoIncidente
from app.models.users import User
from app.models.vehiculos import Vehiculo

__all__ = [
    "Cliente",
    "EstadoSolicitud",
    "HistorialEvento",
    "Notificacion",
    "Operador",
    "Role",
    "Solicitud",
    "Taller",
    "Tecnico",
    "TipoIncidente",
    "User",
    "Vehiculo",
]
