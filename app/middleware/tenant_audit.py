"""Middleware de auditoría para operaciones críticas con tenant.

Registra en el log estructurado: método, ruta, tenant_key y user_id para
cada solicitud mutante (POST / PUT / PATCH / DELETE).
No almacena tokens ni secretos.
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings
from app.dependencies.tenant import resolve_tenant_key
from app.utils.auth import decode_token

logger = logging.getLogger("tenant_audit")

_AUDIT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Rutas excluidas de la auditoría (health, login, docs)
_EXCLUDED_PREFIXES = ("/health", "/docs", "/openapi", "/auth/login", "/auth/register")
# Rutas mutantes que NO queremos en la bitácora de negocio porque son
# "chatter" de la UI/cliente (marcar notificación leída, registrar el token
# de push en cada arranque, suscripción WebPush). Siguen apareciendo en el
# log estructurado, pero no ensucian la bitácora que ve el administrador.
_BITACORA_SKIP_PREFIXES = ("/notificaciones",)


class TenantAuditMiddleware(BaseHTTPMiddleware):
    """Registra el tenant y usuario en operaciones mutantes."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in _AUDIT_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            return await call_next(request)

        tenant_key = resolve_tenant_key(request)
        user_id: str | None = None
        try:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                payload = decode_token(auth_header.split(" ", 1)[1].strip())
                user_id = str(payload.get("user_id") or payload.get("sub") or "")
        except Exception:
            pass

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        logger.info(
            "AUDIT tenant=%s user=%s method=%s path=%s status=%s elapsed_ms=%s",
            tenant_key,
            user_id or "anon",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )

        # ── Bitácora persistente (best-effort) ──────────────────────────────
        # Solo registramos acciones EXITOSAS (status < 400): una petición que
        # falló no cambió nada del sistema, así que no es una "acción" real.
        # Cualquier error de persistencia se traga dentro del servicio para no
        # afectar la respuesta que ya se le devuelve al usuario.
        if response.status_code < 400 and not any(
            path.startswith(prefix) for prefix in _BITACORA_SKIP_PREFIXES
        ):
            try:
                from app.services.gestion_operativa_web.bitacora_service import persistir_evento

                client_ip = request.client.host if request.client else None
                await persistir_evento(
                    tenant=tenant_key,
                    user_id=user_id,
                    metodo=request.method,
                    ruta=path,
                    status_code=response.status_code,
                    ip=client_ip,
                )
            except Exception:  # noqa: BLE001 — la auditoría nunca rompe la request
                pass

        return response
