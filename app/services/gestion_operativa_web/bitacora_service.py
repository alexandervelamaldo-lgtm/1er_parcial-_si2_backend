"""Servicio de la Bitácora — describe y persiste acciones de usuario.

Dos responsabilidades:

  1. `describe_action(metodo, ruta)` — función PURA que traduce una petición
     mutante a una etiqueta legible en español + (entidad, entidad_id). Es
     testeable sin DB.
  2. `persistir_evento(...)` — abre una sesión acotada al tenant y guarda
     una fila en `bitacora`. Best-effort: el llamador (middleware) la invoca
     dentro de un try/except para que un fallo de auditoría jamás tumbe la
     petición real.

Nunca persiste tokens ni secretos: solo método, ruta, acción y user_id.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("bitacora")


# ── Reglas de descripción ────────────────────────────────────────────────
# Cada regla: (método, patrón de ruta, etiqueta legible, entidad).
# El patrón usa un grupo `(?P<id>\d+)` opcional para extraer el id afectado.
# El orden importa: la primera que coincide gana, así que las rutas más
# específicas van antes que las genéricas.
_RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    ("POST", re.compile(r"^/solicitudes/?$"), "Creó una solicitud", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/estado"), "Cambió el estado de una solicitud", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/asignar"), "Asignó una solicitud a un taller", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/seleccionar-taller"), "Seleccionó un taller para la solicitud", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/respuesta-taller"), "El taller respondió a una propuesta", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/respuesta-cliente"), "El cliente respondió a una propuesta", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/responder-asignacion"), "Respondió a una asignación", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/revision-manual"), "Revisó manualmente una solicitud", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/cancelar"), "Canceló una solicitud", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/trabajo-finalizado"), "Finalizó el trabajo de una solicitud", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/(?P<id>\d+)/ruta"), "Actualizó la ruta de una solicitud", "solicitud"),
    ("POST", re.compile(r"^/solicitudes/(?P<id>\d+)/pago"), "Registró un pago", "pago"),
    ("POST", re.compile(r"^/solicitudes/(?P<id>\d+)/disputas"), "Abrió una disputa", "solicitud"),
    ("PUT", re.compile(r"^/solicitudes/disputas/(?P<id>\d+)/resolver"), "Resolvió una disputa", "disputa"),
    ("POST", re.compile(r"^/solicitudes/(?P<id>\d+)/evidencias"), "Agregó evidencia a una solicitud", "solicitud"),
    ("POST", re.compile(r"^/solicitudes/(?P<id>\d+)/audio/transcribir"), "Reprocesó el audio de una solicitud", "solicitud"),
    ("POST", re.compile(r"^/solicitudes/(?P<id>\d+)/imagenes/reprocesar"), "Reprocesó las imágenes de una solicitud", "solicitud"),
    ("POST", re.compile(r"^/clientes/?$"), "Registró un cliente", "cliente"),
    ("POST", re.compile(r"^/tecnicos/?$"), "Registró un técnico", "tecnico"),
    ("PUT", re.compile(r"^/tecnicos/(?P<id>\d+)"), "Actualizó un técnico", "tecnico"),
    ("POST", re.compile(r"^/talleres/?$"), "Registró un taller", "taller"),
    ("PUT", re.compile(r"^/talleres/(?P<id>\d+)"), "Actualizó un taller", "taller"),
    ("POST", re.compile(r"^/vehiculos/?$"), "Registró un vehículo", "vehiculo"),
    ("PUT", re.compile(r"^/vehiculos/(?P<id>\d+)"), "Actualizó un vehículo", "vehiculo"),
    ("POST", re.compile(r"^/cotizaciones"), "Creó una cotización", "cotizacion"),
    ("POST", re.compile(r"^/tenants"), "Creó una organización (tenant)", "tenant"),
    ("POST", re.compile(r"^/admin/tenants"), "Creó una organización (tenant)", "tenant"),
]

# Verbos genéricos para rutas sin regla específica.
_GENERIC_VERB = {
    "POST": "Creó un registro",
    "PUT": "Actualizó un registro",
    "PATCH": "Modificó un registro",
    "DELETE": "Eliminó un registro",
}

_FIRST_NUM = re.compile(r"/(\d+)")
_FIRST_SEG = re.compile(r"^/([a-zA-Z0-9_-]+)")


def describe_action(metodo: str, ruta: str) -> tuple[str, str | None, str | None]:
    """Traduce (método, ruta) → (acción legible, entidad, entidad_id).

    Función pura, sin efectos secundarios — fácil de testear.
    """
    metodo = (metodo or "").upper()
    ruta = ruta or ""
    for rule_method, pattern, label, entidad in _RULES:
        if rule_method != metodo:
            continue
        match = pattern.match(ruta)
        if match:
            entidad_id = None
            if "id" in match.groupdict():
                entidad_id = match.group("id")
            return label, entidad, entidad_id

    # Fallback genérico: verbo + primer segmento como entidad, primer número
    # como id. Mantiene la bitácora útil incluso para rutas no mapeadas.
    accion = _GENERIC_VERB.get(metodo, f"{metodo} {ruta}")
    seg_match = _FIRST_SEG.match(ruta)
    entidad = seg_match.group(1) if seg_match else None
    num_match = _FIRST_NUM.search(ruta)
    entidad_id = num_match.group(1) if num_match else None
    return accion, entidad, entidad_id


def _parse_user_id(raw: str | int | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


async def persistir_evento(
    *,
    tenant: str,
    user_id: str | int | None,
    metodo: str,
    ruta: str,
    status_code: int,
    ip: str | None = None,
) -> None:
    """Guarda una fila en `bitacora` dentro del schema/DB del tenant.

    Best-effort: si la tabla no existe todavía (tenant sin migrar) o la DB
    no responde, se traga el error con un warning. NUNCA propaga la
    excepción — la auditoría no debe afectar la petición del usuario.
    """
    # Imports locales para evitar ciclos en el arranque (database importa
    # dependencies.tenant; el middleware importa este servicio).
    from app.database import get_tenant_sessionmaker
    from app.models.bitacora import Bitacora

    accion, entidad, entidad_id = describe_action(metodo, ruta)
    try:
        sessionmaker = get_tenant_sessionmaker(tenant)
        async with sessionmaker() as session:
            session.add(
                Bitacora(
                    user_id=_parse_user_id(user_id),
                    accion=accion,
                    metodo=(metodo or "").upper()[:8],
                    ruta=ruta[:255],
                    status_code=int(status_code or 0),
                    entidad=entidad,
                    entidad_id=entidad_id,
                    ip=(ip or None) if not ip else ip[:64],
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort, jamás rompe la request
        logger.warning(
            "Bitácora: no se pudo persistir el evento tenant=%s metodo=%s ruta=%s (%s)",
            tenant, metodo, ruta, type(exc).__name__,
        )
