from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import PrioridadSolicitud
from app.schemas.gestion_solicitudes.disputas import DisputaResponse
from app.schemas.gestion_solicitudes.evidencias import EvidenciaResponse
from app.schemas.gestion_solicitudes.historial_eventos import HistorialEventoResponse
from app.schemas.pagos_facturacion.pagos import PagoResponse
from app.schemas.gestion_solicitudes.estados_solicitud import EstadoSolicitudResponse
from app.schemas.gestion_operativa_web.talleres import TallerResponse
from app.schemas.gestion_solicitudes.tipos_incidente import TipoIncidenteResponse


class SolicitudCreate(BaseModel):
    cliente_id: int
    vehiculo_id: int
    taller_id: int | None = None
    tipo_incidente_id: int
    latitud_incidente: float = Field(ge=-90, le=90)
    longitud_incidente: float = Field(ge=-180, le=180)
    latitud_cliente: float = Field(ge=-90, le=90)
    longitud_cliente: float = Field(ge=-180, le=180)
    descripcion: str = Field(min_length=10)
    danos_descripcion: str | None = Field(default=None, min_length=5, max_length=2000)
    fecha_incidente: datetime | None = None
    ubicacion_texto: str | None = Field(default=None, min_length=3, max_length=255)
    categoria_dano: str | None = Field(default=None, min_length=2, max_length=80)
    foto_url: str | None = None
    es_carretera: bool = False
    condicion_vehiculo: str = Field(default="Operativo con limitaciones", min_length=3)
    # The risk level is now AI-determined server-side. Clients can omit it (default None);
    # if a value is provided it is treated as a hint and clamped to 1..5.
    nivel_riesgo: int | None = Field(default=None, ge=1, le=5)
    # Enviado cuando el cliente preselecciona taller desde el mapa
    presupuesto_aceptado: float | None = Field(default=None, gt=0)


class SolicitudAsignar(BaseModel):
    tecnico_id: int | None = None
    taller_id: int | None = None


class SolicitudSeleccionTallerRequest(BaseModel):
    taller_id: int
    origen_lat: float = Field(ge=-90, le=90)
    origen_lon: float = Field(ge=-180, le=180)
    presupuesto_aceptado: float | None = Field(default=None, gt=0)


class SolicitudActualizarRutaRequest(BaseModel):
    origen_lat: float = Field(ge=-90, le=90)
    origen_lon: float = Field(ge=-180, le=180)


class SolicitudEstadoUpdate(BaseModel):
    estado_id: int
    estado_nombre: str | None = None
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
    # Confirmación GPS del técnico parado en el sitio. Es OPCIONAL porque un
    # taller SIN técnico cierra el trabajo de forma remota (flujo "taller sin
    # técnico") y no aporta una ubicación en terreno. Cuando vienen, el backend
    # valida que coincidan con el punto del servicio.
    latitud_confirmacion: float | None = Field(default=None, ge=-90, le=90)
    longitud_confirmacion: float | None = Field(default=None, ge=-180, le=180)


class ServicioDemandaResponse(BaseModel):
    id: int
    estado: str
    solicitud_id: int
    taller_id: int | None = None
    tecnico_id: int | None = None
    latitud_cliente: float
    longitud_cliente: float
    latitud_servicio: float
    longitud_servicio: float
    direccion_servicio: str | None = None
    radio_busqueda_km: float
    cobertura_tecnico_km: float | None = None
    distancia_asignacion_km: float | None = None
    eta_estimado_min: int | None = None
    score_matching: float | None = None
    match_especialidad: bool = False
    detalle_matching: str | None = None
    confirmacion_ubicacion_ok: bool | None = None
    distancia_confirmacion_m: float | None = None
    confirmacion_ubicacion_en: datetime | None = None

    model_config = {"from_attributes": True}


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
    en_turno: bool = True
    radio_cobertura_km: float | None = None
    match_especialidad: bool = False
    score: float | None = None
    detalle_match: str | None = None
    distancia_km: float | None = None
    eta_min: int | None = None


class SolicitudCandidatosResponse(BaseModel):
    solicitud_id: int
    hay_cobertura: bool
    mensaje: str | None = None
    talleres: list[TallerResponse]
    tecnicos: list[TecnicoCandidatoResponse]
    servicio_unico: ServicioDemandaResponse | None = None


# ── Talleres con presupuesto (flujo cliente↔taller-directo) ─────────────


class TallerPresupuestoBreakdown(BaseModel):
    """Desglose del presupuesto que el cliente verá en cada bottom-sheet
    del mapa móvil. Todos los montos en la moneda del taller (default BOB)."""
    monto_base:      float          # precio sin descuentos
    descuento_pct:   float | None   # 15.0 si aplica marca_asociada, 0–100 si descuentos_marca
    monto_final:     float          # = monto_base * (1 - descuento_pct/100)
    moneda:          str = "BOB"
    rango_min:       float          # límite inferior probable
    rango_max:       float          # límite superior probable
    tiempo_horas:    float | None = None
    motivo_descuento: str | None = None  # "Marca asociada del taller" / "Descuento especial Toyota" etc.
    # Cross-check con la estimación IA (cuando hay solicitud.costo_estimado).
    # `diverge_ia_pct` es la diferencia relativa absoluta entre el precio del
    # taller y la estimación IA, en porcentaje. `requiere_revision` se marca
    # cuando esa diferencia supera el 80% — el cliente puede aceptar igual,
    # pero la UI lo destaca como "presupuesto inusual".
    estimacion_ia_min:  float | None = None
    estimacion_ia_max:  float | None = None
    estimacion_ia_prob: float | None = None
    diverge_ia_pct:     float | None = None
    requiere_revision:  bool = False


class TallerConPresupuestoResponse(BaseModel):
    taller_id:                int
    nombre:                   str
    direccion:                str | None = None
    lat:                      float
    lng:                      float
    distancia_km:             float
    eta_min:                  int | None = None     # null si Mapbox falló
    rating_promedio:          float
    capacidad:                int
    disponible:               bool
    match_especializacion:    bool                  # categoría del incidente ∈ servicios del taller
    marca_asociada_descuento: bool                  # 15% por marca asociada aplicable
    presupuesto:              TallerPresupuestoBreakdown
    score:                    float                 # 0–1, mayor = mejor recomendación
    motivo:                   str                   # "Cerca + especializado + descuento marca"


class TalleresConPresupuestoResponse(BaseModel):
    solicitud_id:   int
    radio_km:       float
    total:          int
    talleres:       list[TallerConPresupuestoResponse]
    cached_at:      str               # ISO timestamp para que el cliente sepa qué tan fresco está
    mensaje:        str | None = None # mensaje si no hay talleres o si la consulta tiene observaciones


class SolicitudSeguimientoResponse(BaseModel):
    solicitud_id: int
    estado: str
    route_color: str | None = None
    servicio_id: int | None = None
    servicio_estado: str | None = None
    taller_nombre: str | None = None
    taller_id: int | None = None
    latitud_taller: float | None = None
    longitud_taller: float | None = None
    tecnico_id: int | None = None
    tecnico_nombre: str | None = None
    latitud_cliente: float | None = None
    longitud_cliente: float | None = None
    latitud_servicio: float | None = None
    longitud_servicio: float | None = None
    direccion_servicio: str | None = None
    latitud_actual: float | None = None
    longitud_actual: float | None = None
    distancia_km: float | None = None
    eta_min: int | None = None
    # Rango de incertidumbre del ETA en minutos — la UI debe mostrar
    # "12-18 min" cuando upper-lower > 5 min y "15 min" cuando es cerrado.
    # Si no viene, los clientes legacy siguen usando solo `eta_min`.
    eta_min_lower: int | None = None
    eta_min_upper: int | None = None
    # Geometría GeoJSON (LineString) de la ruta vial taller→incidente cuando el
    # seguimiento se origina en el taller (flujo "taller sin técnico"). La UI la
    # dibuja directamente y anima el muñeco sobre ella, así sigue calles reales
    # en vez de una recta. Si no aplica, la UI cae a `solicitud.ruta_osrm`.
    ruta_seguimiento: dict | None = None
    ubicacion_actualizada_en: datetime | None = None
    ubicacion_desactualizada: bool = False
    tracking_activo: bool = False
    sin_senal: bool = False
    requiere_compartir_ubicacion: bool = False
    cliente_aprobada: bool | None = None
    propuesta_expira_en: datetime | None = None
    propuesta_expirada: bool = False
    match_especialidad: bool = False
    confirmacion_ubicacion_ok: bool | None = None
    distancia_confirmacion_m: float | None = None
    confirmacion_ubicacion_en: datetime | None = None
    mensaje: str | None = None


class SolicitudResponse(BaseModel):
    id: int
    tenant_key: str | None = None
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
    visual_tags: list[str] = []
    visual_summary: str | None = None
    visual_factor: float | None = None
    visual_confidence: float | None = None
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
    fecha_incidente: datetime | None = None
    danos_descripcion: str | None = None
    ubicacion_texto: str | None = None
    categoria_dano: str | None = None
    presupuesto_aceptado: float | None = None
    ruta_osrm: dict | None = None
    ruta_distancia_km: float | None = None
    ruta_eta_min: int | None = None
    servicio_demanda: ServicioDemandaResponse | None = None
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
