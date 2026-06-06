"""
Centralized real-time broadcast hub.

All modules that need to push events to connected WebSocket clients
import the singleton `hub` from here. This keeps tracking_ws.py thin
and avoids circular imports between routers.

Message types emitted by the platform:
  - init              → initial technician positions (sent on connect)
  - location_update   → technician moved
  - solicitud_update  → solicitud state/assignment changed
  - kpi_refresh       → signal to clients that KPI data is stale
  - ping / pong       → keep-alive
"""
from __future__ import annotations

import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class TenantHub:
    """
    In-process broadcast hub scoped by tenant key.

    Thread-safety note: all WebSocket operations in FastAPI run in the
    same event loop, so no lock is needed for the connection sets.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._user_connections: dict[str, dict[int, set[WebSocket]]] = {}
        self._socket_users: dict[WebSocket, tuple[str, int]] = {}

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self, tenant: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(tenant, set()).add(websocket)
        logger.debug("WS connect  tenant=%s  total=%d", tenant, self.count(tenant))

    def disconnect(self, tenant: str, websocket: WebSocket) -> None:
        conns = self._connections.get(tenant)
        if conns:
            conns.discard(websocket)
            if not conns:
                self._connections.pop(tenant, None)
        binding = self._socket_users.pop(websocket, None)
        if binding:
            bound_tenant, user_id = binding
            tenant_users = self._user_connections.get(bound_tenant)
            if tenant_users:
                user_conns = tenant_users.get(user_id)
                if user_conns:
                    user_conns.discard(websocket)
                    if not user_conns:
                        tenant_users.pop(user_id, None)
                if not tenant_users:
                    self._user_connections.pop(bound_tenant, None)
        logger.debug("WS disconnect  tenant=%s  total=%d", tenant, self.count(tenant))

    def count(self, tenant: str) -> int:
        return len(self._connections.get(tenant, set()))

    def bind_user(self, tenant: str, websocket: WebSocket, user_id: int) -> None:
        self._socket_users[websocket] = (tenant, user_id)
        self._user_connections.setdefault(tenant, {}).setdefault(user_id, set()).add(websocket)

    def user_count(self, tenant: str, user_id: int) -> int:
        return len(self._user_connections.get(tenant, {}).get(user_id, set()))

    # ── Broadcasting ──────────────────────────────────────────────────────────

    async def broadcast(self, tenant: str, message: dict) -> None:
        """Send *message* to every connected client in the given tenant."""
        conns = list(self._connections.get(tenant, set()))
        if not conns:
            return
        payload = json.dumps(message, ensure_ascii=False, default=str)
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(tenant, ws)

    async def broadcast_to_users(self, tenant: str, user_ids: list[int], message: dict) -> None:
        """Send *message* only to bound WebSocket clients for the given users."""
        tenant_users = self._user_connections.get(tenant, {})
        sockets: set[WebSocket] = set()
        for user_id in set(user_ids):
            sockets.update(tenant_users.get(user_id, set()))
        if not sockets:
            return
        payload = json.dumps(message, ensure_ascii=False, default=str)
        dead: list[WebSocket] = []
        for ws in list(sockets):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(tenant, ws)

    async def broadcast_solicitud_update(
        self,
        tenant: str,
        *,
        solicitud_id: int,
        estado: str,
        taller_id: int | None = None,
        tecnico_id: int | None = None,
        updated_at: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """Convenience wrapper for solicitud state-change events."""
        msg: dict = {
            "type": "solicitud_update",
            "solicitud_id": solicitud_id,
            "estado": estado,
            "taller_id": taller_id,
            "tecnico_id": tecnico_id,
            "updated_at": updated_at,
        }
        if extra:
            msg.update(extra)
        await self.broadcast(tenant, msg)

    async def broadcast_kpi_refresh(self, tenant: str) -> None:
        """Signal connected dashboard clients to re-fetch KPI data."""
        await self.broadcast(tenant, {"type": "kpi_refresh"})

    async def broadcast_notification_event(
        self,
        tenant: str,
        *,
        user_ids: list[int],
        titulo: str,
        mensaje: str,
        tipo: str,
        deep_link: str | None = None,
        diagnostico_categoria: str | None = None,
    ) -> None:
        await self.broadcast_to_users(
            tenant,
            user_ids,
            {
                "type": "notification_event",
                "titulo": titulo,
                "mensaje": mensaje,
                "notification_type": tipo,
                "url": deep_link,
                "diagnostico_categoria": diagnostico_categoria,
            },
        )


# Module-level singleton — import this instance everywhere
hub = TenantHub()
