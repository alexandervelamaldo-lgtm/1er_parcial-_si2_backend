from app.schemas.autenticacion_acceso.auth import LoginRequest, RegisterRequest, ResetPasswordRequest, TokenResponse
from app.schemas.gestion_operativa_web.clientes import ClienteCreate, ClienteResponse, ClienteUpdate
from app.schemas.gestion_operativa_web.notificaciones import NotificacionResponse
from app.schemas.gestion_solicitudes.solicitudes import SolicitudAsignar, SolicitudCreate, SolicitudEstadoUpdate, SolicitudResponse
from app.schemas.gestion_operativa_web.tecnicos import (
    DisponibilidadTecnicoUpdate,
    TecnicoCreate,
    TecnicoResponse,
    TecnicoUpdate,
    UbicacionTecnicoUpdate,
)
from app.schemas.gestion_solicitudes.vehiculos import VehiculoCreate, VehiculoResponse, VehiculoUpdate

__all__ = [
    "LoginRequest",
    "RegisterRequest",
    "ResetPasswordRequest",
    "TokenResponse",
    "ClienteCreate",
    "ClienteResponse",
    "ClienteUpdate",
    "NotificacionResponse",
    "SolicitudAsignar",
    "SolicitudCreate",
    "SolicitudEstadoUpdate",
    "SolicitudResponse",
    "DisponibilidadTecnicoUpdate",
    "TecnicoCreate",
    "TecnicoResponse",
    "TecnicoUpdate",
    "UbicacionTecnicoUpdate",
    "VehiculoCreate",
    "VehiculoResponse",
    "VehiculoUpdate",
]
