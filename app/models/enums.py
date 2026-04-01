from enum import Enum


class NombreRol(str, Enum):
    CLIENTE = "CLIENTE"
    TECNICO = "TECNICO"
    OPERADOR = "OPERADOR"
    ADMINISTRADOR = "ADMINISTRADOR"


class PrioridadSolicitud(str, Enum):
    BAJA = "BAJA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"
    CRITICA = "CRITICA"


class EstadoSolicitudEnum(str, Enum):
    REGISTRADA = "REGISTRADA"
    ASIGNADA = "ASIGNADA"
    EN_CAMINO = "EN_CAMINO"
    EN_ATENCION = "EN_ATENCION"
    COMPLETADA = "COMPLETADA"
    CANCELADA = "CANCELADA"
