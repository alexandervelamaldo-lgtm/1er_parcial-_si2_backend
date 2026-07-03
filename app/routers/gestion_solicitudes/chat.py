"""Chat en vivo cliente ↔ técnico durante una solicitud activa.

Endpoints:
  - GET  /solicitudes/{id}/chat/messages   → historial (paginable por since_id)
  - POST /solicitudes/{id}/chat/messages   → nuevo mensaje (persiste + broadcast)
  - POST /solicitudes/{id}/chat/read       → marca los mensajes ajenos como leídos

Autorización:
  - Solo el CLIENTE dueño de la solicitud y el TECNICO asignado pueden
    ver/enviar. Cualquier otro rol recibe 403.
  - La solicitud debe estar en un estado "activo" (no cerrada/finalizada
    ni cancelada) para permitir escribir. Leer siempre está permitido
    para las dos partes autorizadas (auditoría y consulta post-cierre).

Realtime:
  - Cada mensaje nuevo se envía por `hub.broadcast_to_users` a los dos
    usuarios involucrados con `type="chat_message"`. El cliente móvil /
    web ya está conectado al WS `/realtime/tracking`, así que reusamos el
    mismo canal sin tener que abrir otro socket.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user, get_role_names
from app.models.clientes import Cliente
from app.models.estados_solicitud import EstadoSolicitud
from app.models.solicitud_chat_audio import SolicitudChatAudioAttachment
from app.models.solicitud_chat_messages import SolicitudChatMessage
from app.models.solicitudes import Solicitud
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.gestion_solicitudes.chat import (
    SolicitudChatAudioInfo,
    SolicitudChatHistoryResponse,
    SolicitudChatMessageCreate,
    SolicitudChatMessageResponse,
    SolicitudChatReadResponse,
)
from app.services.realtime_hub import hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/solicitudes", tags=["Chat solicitud"])

# Estados en los que se puede seguir chateando. Coincide con los estados
# operativos "en curso" del backend. Si mañana agregan un estado nuevo
# tipo "PAUSADO", este set es el único lugar a tocar.
_ESTADOS_CHAT_ABIERTO = {
    "PENDIENTE",
    "ASIGNADA",
    "EN_CAMINO",
    "EN_ATENCION",
    "EN_PROCESO",
    "ESPERANDO_PAGO",
}


async def _resolver_participantes(
    db: AsyncSession, solicitud: Solicitud
) -> tuple[int | None, int | None, int | None]:
    """Devuelve `(cliente_user_id, tecnico_user_id, taller_user_id)` de la solicitud.

    El taller entra al chat en la etapa previa a la asignación de un técnico:
    cuando el cliente ya eligió taller pero ese taller aún no despachó un
    técnico individual. Cuando el técnico se asigna, sigue habiendo tres
    participantes válidos, pero el foco natural pasa a ser cliente↔técnico.
    """
    cliente_user_id: int | None = None
    tecnico_user_id: int | None = None
    taller_user_id: int | None = None
    if solicitud.cliente_id is not None:
        cliente_user_id = await db.scalar(
            select(Cliente.user_id).where(Cliente.id == solicitud.cliente_id)
        )
    if solicitud.tecnico_id is not None:
        tecnico_user_id = await db.scalar(
            select(Tecnico.user_id).where(Tecnico.id == solicitud.tecnico_id)
        )
    if solicitud.taller_id is not None:
        taller_user_id = await db.scalar(
            select(Taller.user_id).where(Taller.id == solicitud.taller_id)
        )
    return cliente_user_id, tecnico_user_id, taller_user_id


async def _autorizar_y_cargar(
    solicitud_id: int,
    current_user: User,
    db: AsyncSession,
) -> tuple[Solicitud, str, int | None, int | None, int | None]:
    """Verifica acceso y devuelve (solicitud, rol, cliente_uid, tecnico_uid, taller_uid)."""
    solicitud = await db.scalar(select(Solicitud).where(Solicitud.id == solicitud_id))
    if not solicitud:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada")

    cliente_uid, tecnico_uid, taller_uid = await _resolver_participantes(db, solicitud)
    roles = get_role_names(current_user)

    rol_chat: str | None = None
    if cliente_uid == current_user.id and "CLIENTE" in roles:
        rol_chat = "cliente"
    elif tecnico_uid == current_user.id and "TECNICO" in roles:
        rol_chat = "tecnico"
    elif taller_uid == current_user.id and "TALLER" in roles:
        rol_chat = "taller"

    if not rol_chat:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el cliente, el taller asignado y el técnico asignado pueden acceder a este chat.",
        )
    return solicitud, rol_chat, cliente_uid, tecnico_uid, taller_uid


async def _sender_display_name(db: AsyncSession, sender_user_id: int, sender_role: str) -> str:
    """Nombre corto para mostrar en la burbuja según el rol del emisor."""
    if sender_role == "cliente":
        nombre = await db.scalar(select(Cliente.nombre).where(Cliente.user_id == sender_user_id))
        if nombre:
            return nombre
    elif sender_role == "tecnico":
        nombre = await db.scalar(select(Tecnico.nombre).where(Tecnico.user_id == sender_user_id))
        if nombre:
            return nombre
    elif sender_role == "taller":
        nombre = await db.scalar(select(Taller.nombre).where(Taller.user_id == sender_user_id))
        if nombre:
            return nombre
    email = await db.scalar(select(User.email).where(User.id == sender_user_id))
    return (email or "").split("@")[0] or "Usuario"


def _tenant_key(db: AsyncSession) -> str:
    return db.info.get("tenant_key", "default")


async def _estado_permite_escritura(db: AsyncSession, solicitud: Solicitud) -> bool:
    estado_nombre = await db.scalar(
        select(EstadoSolicitud.nombre).where(EstadoSolicitud.id == solicitud.estado_id)
    )
    if not estado_nombre:
        return False
    return estado_nombre.upper() in _ESTADOS_CHAT_ABIERTO


async def _audio_info_for(db: AsyncSession, message_id: int, solicitud_id: int) -> SolicitudChatAudioInfo | None:
    """Devuelve el metadata del adjunto de audio del mensaje, si existe."""
    row = await db.scalar(
        select(SolicitudChatAudioAttachment).where(
            SolicitudChatAudioAttachment.message_id == message_id
        )
    )
    if not row:
        return None
    return SolicitudChatAudioInfo(
        content_type=row.content_type,
        duration_ms=row.duration_ms,
        size_bytes=row.size_bytes,
        url=f"/solicitudes/{solicitud_id}/chat/audio/{message_id}",
    )


# Límite de tamaño (bytes) para una nota de voz. ~2 minutos de opus a
# 24kbps ~= 360 KB; dejamos margen para AAC móvil.
_AUDIO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
# Whitelist de MIME types que aceptamos. Tanto webm (web) como aac/m4a
# (móvil) son ampliamente soportados por el <audio> HTML5.
_AUDIO_ALLOWED_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "audio/x-m4a",
    "audio/wav",
}


def _normalize_audio_ct(raw: str | None) -> str | None:
    """Extrae el MIME base (sin parámetros como ";codecs=opus") y valida whitelist."""
    if not raw:
        return None
    base = raw.split(";", 1)[0].strip().lower()
    return base if base in _AUDIO_ALLOWED_TYPES else None


@router.get(
    "/{solicitud_id}/chat/messages",
    response_model=SolicitudChatHistoryResponse,
)
async def listar_mensajes(
    solicitud_id: int,
    since_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SolicitudChatHistoryResponse:
    await _autorizar_y_cargar(solicitud_id, current_user, db)

    stmt = select(SolicitudChatMessage).where(SolicitudChatMessage.solicitud_id == solicitud_id)
    if since_id is not None and since_id > 0:
        stmt = stmt.where(SolicitudChatMessage.id > since_id)
    stmt = stmt.order_by(SolicitudChatMessage.id.asc()).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()

    messages: list[SolicitudChatMessageResponse] = []
    for row in rows:
        display = await _sender_display_name(db, row.sender_user_id, row.sender_role)
        audio = await _audio_info_for(db, row.id, solicitud_id)
        messages.append(
            SolicitudChatMessageResponse(
                id=row.id,
                solicitud_id=row.solicitud_id,
                sender_user_id=row.sender_user_id,
                sender_role=row.sender_role,
                sender_display_name=display,
                content=row.content,
                created_at=row.created_at,
                read_at=row.read_at,
                audio=audio,
            )
        )
    return SolicitudChatHistoryResponse(solicitud_id=solicitud_id, messages=messages)


@router.post(
    "/{solicitud_id}/chat/messages",
    response_model=SolicitudChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def enviar_mensaje(
    solicitud_id: int,
    payload: SolicitudChatMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SolicitudChatMessageResponse:
    solicitud, rol_chat, cliente_uid, tecnico_uid, taller_uid = await _autorizar_y_cargar(
        solicitud_id, current_user, db
    )

    if not await _estado_permite_escritura(db, solicitud):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La solicitud ya no está activa; el chat quedó como consulta.",
        )
    # Si aún no hay taller ni técnico, el cliente no tiene contraparte.
    if rol_chat == "cliente" and tecnico_uid is None and taller_uid is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aún no hay un taller ni un técnico asignado a esta solicitud.",
        )

    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Mensaje vacío")

    row = SolicitudChatMessage(
        solicitud_id=solicitud_id,
        sender_user_id=current_user.id,
        sender_role=rol_chat,
        content=content,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    display = await _sender_display_name(db, current_user.id, rol_chat)

    response = SolicitudChatMessageResponse(
        id=row.id,
        solicitud_id=row.solicitud_id,
        sender_user_id=row.sender_user_id,
        sender_role=row.sender_role,
        sender_display_name=display,
        content=row.content,
        created_at=row.created_at,
        read_at=row.read_at,
    )

    # Broadcast a los dos participantes vía el mismo WS que ya usan para
    # tracking. Si el destinatario está desconectado, el mensaje se
    # recupera al reabrir el detalle (queda persistido).
    tenant = _tenant_key(db)
    destinatarios = [uid for uid in (cliente_uid, tecnico_uid, taller_uid) if uid]
    if destinatarios:
        try:
            await hub.broadcast_to_users(
                tenant,
                destinatarios,
                {
                    "type": "chat_message",
                    "solicitud_id": solicitud_id,
                    "message": {
                        "id": response.id,
                        "sender_user_id": response.sender_user_id,
                        "sender_role": response.sender_role,
                        "sender_display_name": response.sender_display_name,
                        "content": response.content,
                        "created_at": response.created_at.isoformat()
                        if response.created_at
                        else None,
                    },
                },
            )
        except Exception:  # pragma: no cover - defensivo
            logger.exception("chat_message broadcast falló — tenant=%s sol=%s", tenant, solicitud_id)

    return response


@router.post(
    "/{solicitud_id}/chat/read",
    response_model=SolicitudChatReadResponse,
)
async def marcar_leidos(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SolicitudChatReadResponse:
    await _autorizar_y_cargar(solicitud_id, current_user, db)

    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(SolicitudChatMessage)
        .where(
            SolicitudChatMessage.solicitud_id == solicitud_id,
            SolicitudChatMessage.sender_user_id != current_user.id,
            SolicitudChatMessage.read_at.is_(None),
        )
        .values(read_at=now)
    )
    await db.commit()
    return SolicitudChatReadResponse(solicitud_id=solicitud_id, marked=result.rowcount or 0)


@router.post(
    "/{solicitud_id}/chat/audio",
    response_model=SolicitudChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def enviar_audio(
    solicitud_id: int,
    archivo: UploadFile = File(...),
    duration_ms: int | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SolicitudChatMessageResponse:
    """Sube una nota de voz al chat. Crea mensaje + adjunto + broadcast.

    El content_type se toma del archivo (`audio/webm`, `audio/mp4`, etc.).
    El content textual del mensaje queda vacío — el receptor renderiza una
    burbuja de audio en base al campo `audio` de la respuesta.
    """
    solicitud, rol_chat, cliente_uid, tecnico_uid, taller_uid = await _autorizar_y_cargar(
        solicitud_id, current_user, db
    )

    if not await _estado_permite_escritura(db, solicitud):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La solicitud ya no está activa; el chat quedó como consulta.",
        )
    if rol_chat == "cliente" and tecnico_uid is None and taller_uid is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aún no hay un taller ni un técnico asignado a esta solicitud.",
        )

    ct = _normalize_audio_ct(archivo.content_type)
    if not ct:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Formato de audio no soportado. Usa webm, m4a, mp3, ogg o wav.",
        )

    data = await archivo.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El archivo de audio está vacío.",
        )
    if len(data) > _AUDIO_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"La nota de voz excede el tamaño máximo ({_AUDIO_MAX_BYTES // 1024} KB).",
        )

    # Insertamos primero el mensaje (con content='' porque es audio-only),
    # después el adjunto. La FK unique protege de doble inserción.
    msg = SolicitudChatMessage(
        solicitud_id=solicitud_id,
        sender_user_id=current_user.id,
        sender_role=rol_chat,
        content="",  # audio-only: el content textual queda vacío
    )
    db.add(msg)
    await db.flush()  # necesitamos el msg.id para la FK del adjunto

    attachment = SolicitudChatAudioAttachment(
        message_id=msg.id,
        content_type=ct,
        data=data,
        duration_ms=duration_ms if (duration_ms and duration_ms > 0) else None,
        size_bytes=len(data),
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(msg)

    display = await _sender_display_name(db, current_user.id, rol_chat)
    audio_info = SolicitudChatAudioInfo(
        content_type=ct,
        duration_ms=attachment.duration_ms,
        size_bytes=attachment.size_bytes,
        url=f"/solicitudes/{solicitud_id}/chat/audio/{msg.id}",
    )

    response = SolicitudChatMessageResponse(
        id=msg.id,
        solicitud_id=msg.solicitud_id,
        sender_user_id=msg.sender_user_id,
        sender_role=msg.sender_role,
        sender_display_name=display,
        content=msg.content,
        created_at=msg.created_at,
        read_at=msg.read_at,
        audio=audio_info,
    )

    tenant = _tenant_key(db)
    destinatarios = [uid for uid in (cliente_uid, tecnico_uid, taller_uid) if uid]
    if destinatarios:
        try:
            await hub.broadcast_to_users(
                tenant,
                destinatarios,
                {
                    "type": "chat_message",
                    "solicitud_id": solicitud_id,
                    "message": {
                        "id": response.id,
                        "sender_user_id": response.sender_user_id,
                        "sender_role": response.sender_role,
                        "sender_display_name": response.sender_display_name,
                        "content": response.content,
                        "created_at": response.created_at.isoformat()
                        if response.created_at
                        else None,
                        "audio": {
                            "content_type": audio_info.content_type,
                            "duration_ms": audio_info.duration_ms,
                            "size_bytes": audio_info.size_bytes,
                            "url": audio_info.url,
                        },
                    },
                },
            )
        except Exception:  # pragma: no cover - defensivo
            logger.exception("chat audio broadcast falló — tenant=%s sol=%s", tenant, solicitud_id)

    return response


@router.get("/{solicitud_id}/chat/audio/{message_id}")
async def descargar_audio(
    solicitud_id: int,
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Devuelve los bytes del audio con su Content-Type original.

    Autorización idéntica a `listar_mensajes`: solo cliente / técnico /
    taller autorizados. Sin acceso público — así los audios de una
    solicitud no se pueden filtrar aunque alguien adivine el `message_id`.
    """
    await _autorizar_y_cargar(solicitud_id, current_user, db)

    # Verificamos que el mensaje pertenezca a la solicitud (no basta con
    # que exista el adjunto — evita leaks cross-solicitud si un mensaje
    # se moviera de tabla en el futuro).
    msg = await db.scalar(
        select(SolicitudChatMessage).where(
            SolicitudChatMessage.id == message_id,
            SolicitudChatMessage.solicitud_id == solicitud_id,
        )
    )
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mensaje no encontrado")

    attachment = await db.scalar(
        select(SolicitudChatAudioAttachment).where(
            SolicitudChatAudioAttachment.message_id == message_id
        )
    )
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Este mensaje no tiene audio")

    return Response(
        content=attachment.data,
        media_type=attachment.content_type,
        headers={
            "Content-Length": str(attachment.size_bytes),
            # Cache razonable: el audio nunca cambia una vez subido.
            "Cache-Control": "private, max-age=3600",
        },
    )
