from app.schemas.auth import LoginRequest, RegisterRequest, ResetPasswordRequest, TokenResponse
from app.schemas.clientes import ClienteCreate, ClienteResponse, ClienteUpdate
from app.schemas.notificaciones import NotificacionResponse
from app.schemas.solicitudes import SolicitudAsignar, SolicitudCreate, SolicitudEstadoUpdate, SolicitudResponse
from app.schemas.tecnicos import (
    DisponibilidadTecnicoUpdate,
    TecnicoCreate,
    TecnicoResponse,
    TecnicoUpdate,
    UbicacionTecnicoUpdate,
)
from app.schemas.vehiculos import VehiculoCreate, VehiculoResponse, VehiculoUpdate

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
