"""
Real-time WebSocket endpoint for technician location tracking and
solicitud state change events.

Both technicians (sending location_update) and clients/operators
(receiving updates) connect here.  The broadcast hub is a shared
singleton in app.services.realtime_hub so other routers (e.g.
solicitudes) can also push events to connected clients.
"""
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.database import get_db
from app.dependencies.tenant import resolve_tenant_key
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.services.realtime_hub import hub  # shared singleton
from app.utils.auth import decode_token


router = APIRouter(tags=["Tracking"])


async def _resolve_user_from_ws(websocket: WebSocket, db: AsyncSession) -> tuple[User, set[str]] | None:
    token = (websocket.query_params.get("access_token") or "").strip()
    if not token:
        auth = websocket.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

    try:
        payload = decode_token(token)
    except JWTError:
        await websocket.close(code=4401)
        return None

    email = payload.get("sub")
    if not isinstance(email, str) or not email.strip():
        await websocket.close(code=4401)
        return None

    token_tenant = payload.get("tenant")
    request_tenant = getattr(websocket.state, "tenant_key", None)
    if (
        isinstance(token_tenant, str)
        and isinstance(request_tenant, str)
        and token_tenant.strip()
        and request_tenant.strip()
        and token_tenant.strip() != request_tenant.strip()
    ):
        await websocket.close(code=4401)
        return None

    user = await db.scalar(select(User).where(User.email == email))
    if not user or not user.is_active:
        await websocket.close(code=4401)
        return None

    roles_raw = payload.get("roles", [])
    roles = {str(r).strip().upper() for r in roles_raw} if isinstance(roles_raw, list) else set()
    return user, roles


@router.websocket("/realtime/tracking")
async def tracking_ws(websocket: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    tenant = resolve_tenant_key(websocket)
    websocket.state.tenant_key = tenant
    await hub.connect(tenant, websocket)

    try:
        resolved = await _resolve_user_from_ws(websocket, db)
        if not resolved:
            return
        user, roles = resolved
        hub.bind_user(tenant, websocket, user.id)

        # Send current technician positions on connect
        initial = await db.execute(
            select(Tecnico).where(
                Tecnico.latitud_actual.is_not(None),
                Tecnico.longitud_actual.is_not(None),
            )
        )
        tecnicos = initial.scalars().all()
        await websocket.send_text(
            json.dumps(
                {
                    "type": "init",
                    "tecnicos": [
                        {
                            "id": t.id,
                            "nombre": t.nombre,
                            "lat": t.latitud_actual,
                            "lng": t.longitud_actual,
                            "updated_at": t.ubicacion_actualizada_en.isoformat()
                            if t.ubicacion_actualizada_en
                            else None,
                            "disponible": t.disponibilidad,
                        }
                        for t in tecnicos
                    ],
                },
                ensure_ascii=False,
            )
        )

        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue

            msg_type = message.get("type")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if msg_type == "location_update":
                if "TECNICO" not in roles:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "No autorizado"}))
                    continue
                lat = message.get("lat")
                lng = message.get("lng")
                if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Coordenadas inválidas"}))
                    continue
                if abs(float(lat)) > 90 or abs(float(lng)) > 180:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Coordenadas fuera de rango"}))
                    continue

                tecnico = await db.scalar(select(Tecnico).where(Tecnico.user_id == user.id))
                if not tecnico:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Técnico no encontrado"}))
                    continue

                tecnico.latitud_actual = float(lat)
                tecnico.longitud_actual = float(lng)
                tecnico.ubicacion_actualizada_en = func.now()
                await db.commit()
                await db.refresh(tecnico)

                updated_dt = tecnico.ubicacion_actualizada_en
                await hub.broadcast(
                    tenant,
                    {
                        "type": "location_update",
                        "tecnico_id": tecnico.id,
                        "lat": tecnico.latitud_actual,
                        "lng": tecnico.longitud_actual,
                        "updated_at": updated_dt.isoformat() if updated_dt is not None else None,
                        "disponible": tecnico.disponibilidad,
                    },
                )

    except WebSocketDisconnect:
        return
    finally:
        hub.disconnect(tenant, websocket)
