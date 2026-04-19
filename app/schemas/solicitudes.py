from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import PrioridadSolicitud
from app.schemas.disputas import DisputaResponse
from app.schemas.evidencias import EvidenciaResponse
from app.schemas.historial_eventos import HistorialEventoResponse
from app.schemas.pagos import PagoResponse
from app.schemas.estados_solicitud import EstadoSolicitudResponse
from app.schemas.talleres import TallerResponse
from app.schemas.tipos_incidente import TipoIncidenteResponse


class SolicitudCreate(BaseModel):
    cliente_id: int
    vehiculo_id: int
    taller_id: int | None = None
    tipo_incidente_id: int
    latitud_incidente: float
    longitud_incidente: float
    descripcion: str = Field(min_length=10)
    foto_url: str | None = None
    es_carretera: bool = False
    condicion_vehiculo: str = Field(default="Operativo con limitaciones", min_length=3)
    nivel_riesgo: int = Field(default=1, ge=1, le=5)


class SolicitudAsignar(BaseModel):
    tecnico_id: int | None = None
    taller_id: int | None = None


class SolicitudEstadoUpdate(BaseModel):
    estado_id: int
    observacion: str = Field(min_length=3)


class SolicitudCancelarRequest(BaseModel):
    observacion: str = Field(min_length=3, max_length=500)


class SolicitudResponderAsignacionRequest(BaseModel):
    aceptada: bool
    observacion: str = Field(min_length=3, max_length=500)


class SolicitudRespuestaClienteRequest(BaseModel):
    aprobada: bool
    observacion: str = Field(min_length=3, max_length=500)


class SolicitudRevisionManualRequest(BaseModel):
    confianza: float = Field(ge=0, le=1)
    prioridad: PrioridadSolicitud
    resumen_ia: str = Field(min_length=5, max_length=1000)
    motivo_prioridad: str = Field(min_length=5, max_length=1000)


class SolicitudTrabajoFinalizadoRequest(BaseModel):
    costo_final: float = Field(gt=0)
    observacion: str = Field(min_length=5, max_length=1000)


class EstadoSolicitudOptionResponse(BaseModel):
    id: int
    nombre: str

    model_config = {"from_attributes": True}


class TecnicoCandidatoResponse(BaseModel):
    id: int
    nombre: str
    telefono: str
    especialidad: str
    disponibilidad: bool
    distancia_km: float | None = None
    eta_min: int | None = None


class SolicitudCandidatosResponse(BaseModel):
    solicitud_id: int
    hay_cobertura: bool
    mensaje: str | None = None
    talleres: list[TallerResponse]
    tecnicos: list[TecnicoCandidatoResponse]


class SolicitudSeguimientoResponse(BaseModel):
    solicitud_id: int
    estado: str
    taller_nombre: str | None = None
    tecnico_id: int | None = None
    tecnico_nombre: str | None = None
    latitud_actual: float | None = None
    longitud_actual: float | None = None
    distancia_km: float | None = None
    eta_min: int | None = None
    ubicacion_actualizada_en: datetime | None = None
    ubicacion_desactualizada: bool = False
    tracking_activo: bool = False
    sin_senal: bool = False
    requiere_compartir_ubicacion: bool = False
    cliente_aprobada: bool | None = None
    propuesta_expira_en: datetime | None = None
    propuesta_expirada: bool = False
    mensaje: str | None = None


class SolicitudResponse(BaseModel):
    id: int
    cliente_id: int
    vehiculo_id: int
    tecnico_id: int | None = None
    taller_id: int | None = None
    tipo_incidente_id: int
    estado_id: int
    latitud_incidente: float
    longitud_incidente: float
    descripcion: str
    foto_url: str | None = None
    es_carretera: bool = False
    condicion_vehiculo: str
    nivel_riesgo: int
    clasificacion_confianza: float | None = None
    requiere_revision_manual: bool = False
    motivo_prioridad: str | None = None
    resumen_ia: str | None = None
    etiquetas_ia: str | None = None
    transcripcion_audio: str | None = None
    transcripcion_audio_estado: str | None = None
    transcripcion_audio_error: str | None = None
    transcripcion_audio_actualizada_en: datetime | None = None
    proveedor_ia: str | None = None
    costo_estimado: float | None = None
    costo_estimado_min: float | None = None
    costo_estimado_max: float | None = None
    costo_estimacion_confianza: float | None = None
    costo_estimacion_nota: str | None = None
    costo_final: float | None = None
    moneda_costo: str = "BOB"
    trabajo_terminado: bool = False
    trabajo_terminado_en: datetime | None = None
    trabajo_terminado_observacion: str | None = None
    cliente_aprobada: bool | None = None
    cliente_aprobacion_observacion: str | None = None
    cliente_aprobacion_fecha: datetime | None = None
    propuesta_expira_en: datetime | None = None
    prioridad: PrioridadSolicitud
    fecha_solicitud: datetime
    fecha_asignacion: datetime | None = None
    fecha_atencion: datetime | None = None
    fecha_cierre: datetime | None = None
    estado: EstadoSolicitudResponse | None = None
    tipo_incidente: TipoIncidenteResponse | None = None

    model_config = {"from_attributes": True}


class SolicitudDetalleResponse(SolicitudResponse):
    historial: list[HistorialEventoResponse] = []
    evidencias: list[EvidenciaResponse] = []
    pagos: list[PagoResponse] = []
    disputas: list[DisputaResponse] = []


class TrabajoRealizadoItemResponse(BaseModel):
    solicitud_id: int
    fecha_cierre: datetime
    cliente: str
    taller: str
    tecnico: str
    tipo_incidente: str
    costo_estimado: float | None = None
    costo_final: float
    monto_total: float
    monto_comision: float
    monto_taller: float
    metodo_pago: str
    estado_pago: str


class TrabajoRealizadoResumenResponse(BaseModel):
    cantidad_trabajos: int
    total_facturado: float
    total_comision: float
    total_taller: float
    promedio_por_trabajo: float


class TrabajoRealizadoListResponse(BaseModel):
    items: list[TrabajoRealizadoItemResponse]
    resumen: TrabajoRealizadoResumenResponse
