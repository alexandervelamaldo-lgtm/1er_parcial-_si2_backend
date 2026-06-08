import json
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, get_tenant_sessionmaker
from app.dependencies.auth import get_current_user, get_role_names
from app.models.device_tokens import UserDeviceToken
from app.models.notificaciones import Notificacion
from app.models.notification_preferences import UserNotificationPreferences
from app.models.users import User
from app.models.web_push_subscriptions import WebPushSubscription
from app.schemas.gestion_operativa_web.notificaciones import (
    DeviceTokenRegisterRequest,
    NotificationPreferencesResponse,
    NotificationPreferencesUpdateRequest,
    NotificacionResponse,
    WebPushPublicKeyResponse,
    WebPushSubscriptionRegisterRequest,
)


router = APIRouter(prefix="/notificaciones", tags=["Notificaciones"])
settings = get_settings()


# #region debug-point B:web-push-server-report
def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict) -> None:
    _p = ".dbg/web-push-missing.env"
    _u = None
    _s = "web-push-missing"
    try:
        with open(_p, encoding="utf-8") as f:
            c = f.read()
        _u = next((line.split("=", 1)[1] for line in c.splitlines() if line.startswith("DEBUG_SERVER_URL=")), None)
        _s = next((line.split("=", 1)[1] for line in c.splitlines() if line.startswith("DEBUG_SESSION_ID=")), _s)
    except Exception:
        pass
    if not _u:
        return
    try:
        payload = {
            "sessionId": _s,
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": f"[DEBUG] {msg}",
            "data": data,
            "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        urllib.request.urlopen(
            urllib.request.Request(
                _u,
                data=json.dumps(payload, default=str).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=0.3,
        ).read()
    except Exception:
        pass
# #endregion


async def _list_client_notifications_across_tenants(
    *,
    db: AsyncSession,
    current_user: User,
) -> list[NotificacionResponse]:
    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    items: list[NotificacionResponse] = []
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            session = db
            result = await session.execute(
                select(Notificacion)
                .join(User, User.id == Notificacion.usuario_id)
                .where(User.email == current_user.email)
                .order_by(desc(Notificacion.fecha_creacion))
            )
        else:
            tenant_sessionmaker = get_tenant_sessionmaker(tenant)
            async with tenant_sessionmaker() as session:
                session.info["tenant_key"] = tenant
                result = await session.execute(
                    select(Notificacion)
                    .join(User, User.id == Notificacion.usuario_id)
                    .where(User.email == current_user.email)
                    .order_by(desc(Notificacion.fecha_creacion))
                )
        items.extend(NotificacionResponse.model_validate(row) for row in result.scalars().all())
    items.sort(key=lambda item: item.fecha_creacion, reverse=True)
    return items


async def _sync_client_device_token_across_tenants(
    *,
    db: AsyncSession,
    current_user: User,
    token: str,
    plataforma: str,
) -> None:
    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            session = db
            owns_session = False
        else:
            session = get_tenant_sessionmaker(tenant)()
            session.info["tenant_key"] = tenant
            owns_session = True
        try:
            user = await session.scalar(select(User).where(User.email == current_user.email))
            if user is None:
                continue
            existing = await session.scalar(
                select(UserDeviceToken).where(
                    UserDeviceToken.user_id == user.id,
                    UserDeviceToken.token == token,
                )
            )
            if existing:
                existing.plataforma = plataforma
            else:
                session.add(
                    UserDeviceToken(
                        user_id=user.id,
                        token=token,
                        plataforma=plataforma,
                    )
                )
            await session.commit()
        finally:
            if owns_session:
                await session.close()


async def _delete_client_device_token_across_tenants(
    *,
    db: AsyncSession,
    current_user: User,
    token: str | None,
) -> None:
    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            session = db
            owns_session = False
        else:
            session = get_tenant_sessionmaker(tenant)()
            session.info["tenant_key"] = tenant
            owns_session = True
        try:
            user = await session.scalar(select(User).where(User.email == current_user.email))
            if user is None:
                continue
            query = select(UserDeviceToken).where(UserDeviceToken.user_id == user.id)
            if token:
                query = query.where(UserDeviceToken.token == token)
            result = await session.execute(query)
            for row in result.scalars().all():
                await session.delete(row)
            await session.commit()
        finally:
            if owns_session:
                await session.close()


async def _sync_client_web_push_across_tenants(
    *,
    db: AsyncSession,
    current_user: User,
    endpoint: str,
    p256dh: str,
    auth: str,
    expiration_time: str | None,
    user_agent: str | None,
) -> None:
    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            session = db
            owns_session = False
        else:
            session = get_tenant_sessionmaker(tenant)()
            session.info["tenant_key"] = tenant
            owns_session = True
        try:
            user = await session.scalar(select(User).where(User.email == current_user.email))
            if user is None:
                continue
            existing = await session.scalar(
                select(WebPushSubscription).where(
                    WebPushSubscription.user_id == user.id,
                    WebPushSubscription.endpoint == endpoint,
                )
            )
            if existing:
                existing.user_id = user.id
                existing.p256dh = p256dh
                existing.auth = auth
                existing.expiration_time = expiration_time
                existing.user_agent = user_agent
            else:
                session.add(
                    WebPushSubscription(
                        user_id=user.id,
                        endpoint=endpoint,
                        p256dh=p256dh,
                        auth=auth,
                        expiration_time=expiration_time,
                        user_agent=user_agent,
                    )
                )
            await session.commit()
        finally:
            if owns_session:
                await session.close()


async def _delete_client_web_push_across_tenants(
    *,
    db: AsyncSession,
    current_user: User,
) -> None:
    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            session = db
            owns_session = False
        else:
            session = get_tenant_sessionmaker(tenant)()
            session.info["tenant_key"] = tenant
            owns_session = True
        try:
            user = await session.scalar(select(User).where(User.email == current_user.email))
            if user is None:
                continue
            result = await session.execute(
                select(WebPushSubscription).where(WebPushSubscription.user_id == user.id)
            )
            for subscription in result.scalars().all():
                await session.delete(subscription)
            await session.commit()
        finally:
            if owns_session:
                await session.close()


@router.get("", response_model=list[NotificacionResponse])
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificacionResponse]:
    roles = get_role_names(current_user)
    if "CLIENTE" in roles:
        return await _list_client_notifications_across_tenants(db=db, current_user=current_user)
    query = select(Notificacion).order_by(desc(Notificacion.fecha_creacion))
    if not roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        query = query.where(Notificacion.usuario_id == current_user.id)
    result = await db.execute(query)
    return [NotificacionResponse.model_validate(item) for item in result.scalars().all()]


@router.put("/{notificacion_id}/leida", response_model=NotificacionResponse)
async def mark_notification_as_read(
    notificacion_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificacionResponse:
    roles = get_role_names(current_user)
    if "CLIENTE" in roles:
        current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
        for tenant in settings.tenant_databases:
            if tenant == current_tenant:
                session = db
                owns_session = False
            else:
                session = get_tenant_sessionmaker(tenant)()
                session.info["tenant_key"] = tenant
                owns_session = True
            try:
                notificacion = await session.scalar(
                    select(Notificacion)
                    .join(User, User.id == Notificacion.usuario_id)
                    .where(
                        Notificacion.id == notificacion_id,
                        User.email == current_user.email,
                    )
                )
                if not notificacion:
                    continue
                notificacion.leida = True
                await session.commit()
                await session.refresh(notificacion)
                return NotificacionResponse.model_validate(notificacion)
            finally:
                if owns_session:
                    await session.close()
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    notificacion = await db.get(Notificacion, notificacion_id)
    if not notificacion:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    if not roles.intersection({"ADMINISTRADOR", "OPERADOR"}) and notificacion.usuario_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes modificar esta notificación")
    notificacion.leida = True
    await db.commit()
    await db.refresh(notificacion)
    return NotificacionResponse.model_validate(notificacion)


@router.post("/device-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_token(
    payload: DeviceTokenRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if "CLIENTE" in get_role_names(current_user):
        await _sync_client_device_token_across_tenants(
            db=db,
            current_user=current_user,
            token=payload.token,
            plataforma=payload.plataforma,
        )
        return
    existing = await db.scalar(
        select(UserDeviceToken).where(
            UserDeviceToken.user_id == current_user.id,
            UserDeviceToken.token == payload.token,
        )
    )
    if existing:
        existing.plataforma = payload.plataforma
    else:
        db.add(
            UserDeviceToken(
                user_id=current_user.id,
                token=payload.token,
                plataforma=payload.plataforma,
            )
        )
    await db.commit()


@router.delete("/device-token", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device_token(
    token: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if "CLIENTE" in get_role_names(current_user):
        await _delete_client_device_token_across_tenants(
            db=db,
            current_user=current_user,
            token=token,
        )
        return
    query = select(UserDeviceToken).where(UserDeviceToken.user_id == current_user.id)
    if token:
        query = query.where(UserDeviceToken.token == token)
    result = await db.execute(query)
    for row in result.scalars().all():
        await db.delete(row)
    await db.commit()


@router.get("/webpush/public-key", response_model=WebPushPublicKeyResponse)
async def get_web_push_public_key(
    current_user: User = Depends(get_current_user),
) -> WebPushPublicKeyResponse:
    if not settings.vapid_public_key:
        raise HTTPException(status_code=503, detail="Web Push no está configurado en el servidor")
    return WebPushPublicKeyResponse(publicKey=settings.vapid_public_key)


@router.post("/webpush/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe_web_push(
    payload: WebPushSubscriptionRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    # #region debug-point B:subscribe-entry
    _debug_report(
        "B",
        "backend/app/routers/gestion_operativa_web/notificaciones.py:subscribe_web_push",
        "subscribe web push called",
        {
            "tenant": db.info.get("tenant_key", settings.default_tenant or "default"),
            "user_id": current_user.id,
            "email": current_user.email,
            "roles": sorted(get_role_names(current_user)),
            "endpoint_suffix": payload.endpoint[-24:] if payload.endpoint else "",
            "has_p256dh": bool(payload.keys.p256dh),
            "has_auth": bool(payload.keys.auth),
        },
    )
    # #endregion
    if "CLIENTE" in get_role_names(current_user):
        await _sync_client_web_push_across_tenants(
            db=db,
            current_user=current_user,
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            expiration_time=payload.expirationTime,
            user_agent=payload.userAgent,
        )
        # #region debug-point B:subscribe-client-sync
        _debug_report(
            "B",
            "backend/app/routers/gestion_operativa_web/notificaciones.py:subscribe_web_push",
            "client web push synced across tenants",
            {
                "email": current_user.email,
                "endpoint_suffix": payload.endpoint[-24:] if payload.endpoint else "",
            },
        )
        # #endregion
        return
    existing = await db.scalar(
        select(WebPushSubscription).where(
            WebPushSubscription.user_id == current_user.id,
            WebPushSubscription.endpoint == payload.endpoint,
        )
    )
    if existing:
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.expiration_time = payload.expirationTime
        existing.user_agent = payload.userAgent
    else:
        db.add(
            WebPushSubscription(
                user_id=current_user.id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
                expiration_time=payload.expirationTime,
                user_agent=payload.userAgent,
            )
        )
    await db.commit()
    # #region debug-point B:subscribe-committed
    _debug_report(
        "B",
        "backend/app/routers/gestion_operativa_web/notificaciones.py:subscribe_web_push",
        "web push subscription committed",
        {
            "tenant": db.info.get("tenant_key", settings.default_tenant or "default"),
            "user_id": current_user.id,
            "endpoint_suffix": payload.endpoint[-24:] if payload.endpoint else "",
        },
    )
    # #endregion


@router.delete("/webpush/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_web_push(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if "CLIENTE" in get_role_names(current_user):
        await _delete_client_web_push_across_tenants(
            db=db,
            current_user=current_user,
        )
        return
    result = await db.execute(select(WebPushSubscription).where(WebPushSubscription.user_id == current_user.id))
    for subscription in result.scalars().all():
        await db.delete(subscription)
    await db.commit()


@router.get("/preferencias", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationPreferencesResponse:
    prefs = await db.scalar(select(UserNotificationPreferences).where(UserNotificationPreferences.user_id == current_user.id))
    if not prefs:
        prefs = UserNotificationPreferences(user_id=current_user.id, disabled_all=False, disabled_types={})
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return NotificationPreferencesResponse(disabledAll=prefs.disabled_all, disabledTypes=prefs.disabled_types or {})


@router.put("/preferencias", response_model=NotificationPreferencesResponse)
async def update_notification_preferences(
    payload: NotificationPreferencesUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationPreferencesResponse:
    prefs = await db.scalar(select(UserNotificationPreferences).where(UserNotificationPreferences.user_id == current_user.id))
    if not prefs:
        prefs = UserNotificationPreferences(user_id=current_user.id, disabled_all=False, disabled_types={})
        db.add(prefs)
    if payload.disabledAll is not None:
        prefs.disabled_all = payload.disabledAll
    if payload.disabledTypes is not None:
        prefs.disabled_types = payload.disabledTypes
    await db.commit()
    await db.refresh(prefs)
    return NotificationPreferencesResponse(disabledAll=prefs.disabled_all, disabledTypes=prefs.disabled_types or {})
