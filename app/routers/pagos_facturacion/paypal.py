"""
PayPal payment gateway router.

Flujo completo:
  1. POST /pagos/paypal/crear-orden/{solicitud_id}
       → valida solicitud, crea orden en PayPal, guarda PagoSolicitud(PENDIENTE_PAYPAL)
       → devuelve { order_id, approve_url } al móvil
  2. Móvil abre WebView en approve_url; usuario aprueba en PayPal
  3. PayPal redirige a GET /pagos/paypal/retorno?token=ORDER_ID&PayerID=...
       → el WebView detecta la URL antes de navegar y la intercepta
  4. POST /pagos/paypal/capturar  { order_id, solicitud_id }
       → captura el pago en PayPal, actualiza PagoSolicitud a PAGADO
  5. (Asíncrono) PayPal envía webhook POST /pagos/paypal/webhook
       → verificación de firma, confirmación idempotente del pago

Seguridad: las credenciales PayPal NUNCA se exponen al cliente móvil.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db, get_tenant_sessionmaker
from app.dependencies.auth import get_current_cliente_id, get_current_user, get_role_names
from app.models.clientes import Cliente
from app.models.estados_solicitud import EstadoSolicitud
from app.models.historial_eventos import HistorialEvento
from app.models.notificaciones import Notificacion
from app.models.operadores import Operador
from app.models.pagos import PagoSolicitud
from app.models.solicitudes import Solicitud
from app.models.device_tokens import UserDeviceToken
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.pagos_facturacion.pagos import PagoResponse
from app.services.gestion_operativa_web.notificacion_service import enviar_notificacion_push
from app.services.pagos_facturacion.payment_service import calculate_payment_breakdown
from app.services.pagos_facturacion.paypal_service import (
    PayPalError,
    PayPalNotConfiguredError,
    PayPalService,
    get_paypal_service,
)

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/pagos/paypal", tags=["Pagos — PayPal"])

# ─── Schemas ─────────────────────────────────────────────────────────────────


class PayPalOrdenResponse(BaseModel):
    order_id: str
    approve_url: str
    solicitud_id: int
    monto: float
    moneda: str


class PayPalCapturarRequest(BaseModel):
    order_id: str = Field(min_length=1)
    solicitud_id: int = Field(gt=0)


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _cargar_solicitud(db: AsyncSession, solicitud_id: int) -> Solicitud | None:
    result = await db.execute(
        select(Solicitud)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.cliente).selectinload(Cliente.user),
            selectinload(Solicitud.taller),
            selectinload(Solicitud.tecnico),
            selectinload(Solicitud.pagos),
            selectinload(Solicitud.historial),
        )
        .where(Solicitud.id == solicitud_id)
    )
    return result.scalar_one_or_none()


@asynccontextmanager
async def _open_client_payment_session(
    db: AsyncSession,
    *,
    solicitud_id: int,
    current_user: User,
    current_cliente_id: int | None,
) -> AsyncIterator[tuple[Solicitud | None, AsyncSession, int]]:
    solicitud = await _cargar_solicitud(db, solicitud_id)
    if solicitud is not None and solicitud.cliente_id == current_cliente_id:
        yield solicitud, db, current_user.id
        return

    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            continue
        tenant_sessionmaker = get_tenant_sessionmaker(tenant)
        async with tenant_sessionmaker() as session:
            session.info["tenant_key"] = tenant
            user = await session.scalar(select(User).where(User.email == current_user.email))
            if user is None:
                continue
            cliente_id = await session.scalar(select(Cliente.id).where(Cliente.user_id == user.id))
            if cliente_id is None:
                continue
            solicitud = await _cargar_solicitud(session, solicitud_id)
            if solicitud is not None and solicitud.cliente_id == cliente_id:
                yield solicitud, session, user.id
                return

    yield None, db, current_user.id


async def _get_estado(db: AsyncSession, nombre: str) -> EstadoSolicitud:
    estado = await db.scalar(select(EstadoSolicitud).where(EstadoSolicitud.nombre == nombre))
    if not estado:
        raise HTTPException(status_code=500, detail=f"Estado '{nombre}' no encontrado en DB")
    return estado


async def _get_operador_ids(db: AsyncSession) -> list[int]:
    result = await db.execute(select(Operador.user_id))
    return list(result.scalars().all())


async def _notificar(
    db: AsyncSession,
    user_ids: list[int],
    titulo: str,
    mensaje: str,
    tipo: str,
    deep_link: str | None = None,
) -> None:
    for uid in set(user_ids):
        db.add(Notificacion(usuario_id=uid, titulo=titulo, mensaje=mensaje, tipo=tipo))
    # Push notifications (best-effort — no rollback on failure)
    result = await db.execute(
        select(UserDeviceToken).where(UserDeviceToken.user_id.in_(list(set(user_ids))))
    )
    for device in result.scalars().all():
        data: dict = {"type": tipo}
        if deep_link:
            data["url"] = deep_link
        try:
            enviar_notificacion_push(device.token, titulo, mensaje, data)
        except Exception:
            pass


def _validar_pago_previo(solicitud: Solicitud) -> None:
    estado_actual = solicitud.estado.nombre if solicitud.estado else ""
    if estado_actual not in {"EN_ATENCION", "COMPLETADA"}:
        raise HTTPException(status_code=400, detail="La solicitud aún no está lista para pagar")
    if solicitud.cliente_aprobada is False:
        raise HTTPException(status_code=400, detail="Primero aprueba la propuesta del taller")
    if not solicitud.trabajo_terminado or solicitud.costo_final is None:
        raise HTTPException(status_code=400, detail="El técnico aún no ha registrado el trabajo y costo final")
    existing_paid = next((p for p in solicitud.pagos if p.estado == "PAGADO"), None)
    if existing_paid:
        raise HTTPException(status_code=400, detail="Esta solicitud ya tiene un pago confirmado")


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.post(
    "/crear-orden/{solicitud_id}",
    response_model=PayPalOrdenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crea una orden PayPal para pagar una solicitud",
)
async def crear_orden_paypal(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
    paypal: PayPalService = Depends(get_paypal_service),
) -> PayPalOrdenResponse:
    """
    Valida la solicitud, crea una orden PayPal y devuelve la URL de aprobación.
    El móvil abre esa URL en un WebView para que el usuario complete el pago.
    """
    if not paypal.configured:
        raise HTTPException(
            status_code=503,
            detail="PayPal no está configurado en el servidor. Contacta al administrador.",
        )
    async with _open_client_payment_session(
        db,
        solicitud_id=solicitud_id,
        current_user=current_user,
        current_cliente_id=current_cliente_id,
    ) as (solicitud, db, _usuario_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")

        roles = get_role_names(current_user)
        if "CLIENTE" not in roles:
            raise HTTPException(status_code=403, detail="Solo el cliente propietario puede pagar esta solicitud")

        _validar_pago_previo(solicitud)

        monto = round(float(solicitud.costo_final), 2)  # type: ignore[arg-type]
        return_url = f"{settings.backend_base_url}/pagos/paypal/retorno"
        cancel_url = f"{settings.backend_base_url}/pagos/paypal/cancelar"

        try:
            resultado = await paypal.create_order(
                amount=monto,
                solicitud_id=solicitud_id,
                return_url=return_url,
                cancel_url=cancel_url,
            )
        except PayPalNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PayPalError as exc:
            logger.error("PayPal create_order error: %s", exc)
            raise HTTPException(status_code=502, detail=f"Error comunicando con PayPal: {exc}") from exc

        breakdown = calculate_payment_breakdown(monto)
        pending = next(
            (p for p in solicitud.pagos if p.estado == "PENDIENTE_PAYPAL"),
            None,
        )
        if pending is None:
            pending = PagoSolicitud(
                solicitud_id=solicitud.id,
                cliente_id=solicitud.cliente_id,
                taller_id=solicitud.taller_id,
                monto_total=breakdown.total,
                monto_comision=breakdown.commission,
                monto_taller=breakdown.workshop_amount,
                metodo_pago="paypal",
                estado="PENDIENTE_PAYPAL",
                referencia_externa=resultado.order_id,
                observacion="Pago iniciado vía PayPal — pendiente de aprobación del usuario",
            )
            db.add(pending)
        else:
            pending.monto_total = breakdown.total
            pending.monto_comision = breakdown.commission
            pending.monto_taller = breakdown.workshop_amount
            pending.referencia_externa = resultado.order_id

        await db.commit()

        return PayPalOrdenResponse(
            order_id=resultado.order_id,
            approve_url=resultado.approve_url,
            solicitud_id=solicitud_id,
            monto=monto,
            moneda=settings.paypal_currency,
        )


@router.post(
    "/capturar",
    response_model=PagoResponse,
    summary="Captura un pago PayPal aprobado por el usuario",
)
async def capturar_orden_paypal(
    payload: PayPalCapturarRequest,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
    paypal: PayPalService = Depends(get_paypal_service),
) -> PagoSolicitud:
    """
    Captura la orden PayPal después de que el usuario la aprobó.
    Actualiza el estado del pago a PAGADO y completa la solicitud si corresponde.
    """
    async with _open_client_payment_session(
        db,
        solicitud_id=payload.solicitud_id,
        current_user=current_user,
        current_cliente_id=current_cliente_id,
    ) as (solicitud, db, usuario_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")

        roles = get_role_names(current_user)
        if "CLIENTE" not in roles:
            raise HTTPException(status_code=403, detail="Solo el cliente propietario puede confirmar este pago")

        pago = next(
            (p for p in solicitud.pagos if p.referencia_externa == payload.order_id),
            None,
        )
        if pago is None:
            raise HTTPException(
                status_code=404,
                detail="No se encontró un pago pendiente con ese order_id para esta solicitud",
            )
        if pago.estado == "PAGADO":
            await db.refresh(pago)
            return pago

        try:
            capture_data = await paypal.capture_order(payload.order_id)
        except PayPalError as exc:
            logger.error("PayPal capture error (order=%s): %s", payload.order_id, exc)
            raise HTTPException(status_code=502, detail=f"Error al capturar el pago en PayPal: {exc}") from exc

        capture_status = capture_data.get("status", "")
        if capture_status != "COMPLETED":
            raise HTTPException(
                status_code=400,
                detail=f"PayPal no completó el pago (estado: {capture_status}). Inténtalo de nuevo.",
            )

        captures = (
            capture_data.get("purchase_units", [{}])[0]
            .get("payments", {})
            .get("captures", [])
        )
        capture_id = captures[0]["id"] if captures else payload.order_id

        pago.estado = "PAGADO"
        pago.referencia_externa = f"{payload.order_id}:{capture_id}"
        pago.observacion = f"Pago PayPal capturado. Capture ID: {capture_id}"
        pago.fecha_pago = datetime.now(timezone.utc)

        estado_actual = solicitud.estado.nombre if solicitud.estado else ""

        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual or "SIN_ESTADO",
                estado_nuevo=estado_actual or "SIN_ESTADO",
                observacion=f"Pago PayPal confirmado por Bs {pago.monto_total:.2f}. "
                            f"Comisión plataforma: Bs {pago.monto_comision:.2f}. Capture ID: {capture_id}",
                usuario_id=usuario_id,
            )
        )

        if estado_actual != "COMPLETADA":
            estado_completada = await _get_estado(db, "COMPLETADA")
            solicitud.estado_id = estado_completada.id
            solicitud.fecha_cierre = datetime.now(timezone.utc)
            if solicitud.tecnico_id:
                tecnico = await db.get(Tecnico, solicitud.tecnico_id)
                if tecnico:
                    tecnico.disponibilidad = True
            db.add(
                HistorialEvento(
                    solicitud_id=solicitud.id,
                    estado_anterior=estado_actual or "SIN_ESTADO",
                    estado_nuevo="COMPLETADA",
                    observacion="Solicitud completada automáticamente tras confirmar el pago PayPal.",
                    usuario_id=usuario_id,
                )
            )

        notify_ids = [usuario_id]
        if solicitud.taller and solicitud.taller.user_id:
            notify_ids.append(solicitud.taller.user_id)
        notify_ids.extend(await _get_operador_ids(db))
        await _notificar(
            db,
            notify_ids,
            "Pago PayPal confirmado",
            f"Se confirmó el pago de la solicitud #{solicitud.id} "
            f"por Bs {pago.monto_total:.2f} vía PayPal.",
            "PAGO_CONFIRMADO",
            deep_link=f"/solicitudes/{solicitud.id}",
        )

        await db.commit()
        await db.refresh(pago)
        return pago


@router.get(
    "/retorno",
    summary="URL de retorno PayPal (el WebView detecta esta URL — no es para el navegador del usuario)",
    include_in_schema=True,
)
async def paypal_retorno(
    token: str | None = None,
    payer_id: str | None = None,
) -> dict:
    """
    PayPal redirige aquí tras la aprobación.
    El WebView del móvil detecta esta URL y extrae el token (order_id)
    antes de que el navegador la cargue, por lo que el usuario no ve esta respuesta.
    """
    return {
        "status": "approved",
        "order_id": token,
        "payer_id": payer_id,
        "message": "Pago aprobado. Vuelve a la app para confirmar.",
    }


@router.get(
    "/cancelar",
    summary="URL de cancelación PayPal",
    include_in_schema=True,
)
async def paypal_cancelar(token: str | None = None) -> dict:
    """
    PayPal redirige aquí si el usuario cancela el pago.
    El WebView del móvil detecta esta URL y cierra la pantalla de pago.
    """
    return {
        "status": "cancelled",
        "order_id": token,
        "message": "Pago cancelado. Puedes intentarlo de nuevo.",
    }


@router.post(
    "/webhook",
    summary="Receptor de eventos webhook de PayPal",
    status_code=status.HTTP_200_OK,
)
async def paypal_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    paypal: PayPalService = Depends(get_paypal_service),
    paypal_transmission_id: str | None = Header(default=None, alias="paypal-transmission-id"),
    paypal_transmission_time: str | None = Header(default=None, alias="paypal-transmission-time"),
    paypal_cert_url: str | None = Header(default=None, alias="paypal-cert-url"),
    paypal_auth_algo: str | None = Header(default=None, alias="paypal-auth-algo"),
    paypal_transmission_sig: str | None = Header(default=None, alias="paypal-transmission-sig"),
) -> dict:
    """
    Endpoint para webhooks de PayPal.
    Maneja PAYMENT.CAPTURE.COMPLETED para confirmar pagos de forma asíncrona.
    Registra PAYPAL_WEBHOOK_ID en .env para habilitar la verificación de firma.
    """
    raw_body = await request.body()

    # Verify signature (skipped silently if PAYPAL_WEBHOOK_ID is not set)
    if paypal.configured and all(
        [paypal_transmission_id, paypal_transmission_time, paypal_cert_url,
         paypal_auth_algo, paypal_transmission_sig]
    ):
        valid = await paypal.verify_webhook_signature(
            transmission_id=paypal_transmission_id or "",
            transmission_time=paypal_transmission_time or "",
            cert_url=paypal_cert_url or "",
            auth_algo=paypal_auth_algo or "",
            transmission_sig=paypal_transmission_sig or "",
            webhook_event_body=raw_body.decode(),
        )
        if not valid:
            logger.warning("PayPal webhook firma inválida — rechazando evento")
            raise HTTPException(status_code=400, detail="Firma del webhook inválida")

    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Cuerpo del webhook no es JSON válido")

    event_type: str = event.get("event_type", "")
    logger.info("PayPal webhook recibido: event_type=%s", event_type)

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        await _handle_capture_completed(db, event)

    # PayPal expects HTTP 200 — always return OK
    return {"received": True, "event_type": event_type}


async def _handle_capture_completed(db: AsyncSession, event: dict) -> None:
    """
    Idempotently confirm a payment when PayPal fires PAYMENT.CAPTURE.COMPLETED.
    Extracts order_id from the supplementary data and looks up the PagoSolicitud.
    """
    resource = event.get("resource", {})
    capture_id: str = resource.get("id", "")
    related_ids = resource.get("supplementary_data", {}).get("related_ids", {})
    order_id: str = related_ids.get("order_id", "")

    if not order_id:
        logger.warning("Webhook CAPTURE.COMPLETED sin order_id en supplementary_data")
        return

    # Find the pending payment by referencia_externa prefix (order_id)
    result = await db.execute(
        select(PagoSolicitud)
        .options(
            selectinload(PagoSolicitud.solicitud).selectinload(Solicitud.estado),
            selectinload(PagoSolicitud.solicitud).selectinload(Solicitud.taller),
            selectinload(PagoSolicitud.solicitud).selectinload(Solicitud.tecnico),
            selectinload(PagoSolicitud.solicitud).selectinload(Solicitud.historial),
        )
        .where(PagoSolicitud.referencia_externa.like(f"{order_id}%"))
        .where(PagoSolicitud.metodo_pago == "paypal")
    )
    pago = result.scalar_one_or_none()

    if pago is None:
        logger.warning("Webhook: PagoSolicitud no encontrado para order_id=%s", order_id)
        return
    if pago.estado == "PAGADO":
        logger.info("Webhook: pago order_id=%s ya está PAGADO — ignorando (idempotente)", order_id)
        return

    pago.estado = "PAGADO"
    pago.referencia_externa = f"{order_id}:{capture_id}"
    pago.observacion = f"Pago PayPal confirmado vía webhook. Capture ID: {capture_id}"
    pago.fecha_pago = datetime.now(timezone.utc)

    solicitud: Solicitud = pago.solicitud
    estado_actual = solicitud.estado.nombre if solicitud.estado else ""

    # System user placeholder (webhook has no user context — use cliente_id user)
    system_user_id = solicitud.cliente_id  # fallback

    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual,
            estado_nuevo=estado_actual,
            observacion=f"Pago PayPal confirmado automáticamente por webhook. Order: {order_id}",
            usuario_id=system_user_id,
        )
    )

    if estado_actual != "COMPLETADA":
        estado_completada = await _get_estado(db, "COMPLETADA")
        solicitud.estado_id = estado_completada.id
        solicitud.fecha_cierre = datetime.now(timezone.utc)
        if solicitud.tecnico_id:
            tecnico = await db.get(Tecnico, solicitud.tecnico_id)
            if tecnico:
                tecnico.disponibilidad = True
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual,
                estado_nuevo="COMPLETADA",
                observacion="Solicitud completada automáticamente tras confirmar pago PayPal (webhook).",
                usuario_id=system_user_id,
            )
        )

    notify_ids = [solicitud.cliente_id]
    if solicitud.taller and solicitud.taller.user_id:
        notify_ids.append(solicitud.taller.user_id)
    notify_ids.extend(await _get_operador_ids(db))
    await _notificar(
        db,
        [uid for uid in notify_ids if uid],
        "Pago PayPal confirmado",
        f"El pago de la solicitud #{solicitud.id} por Bs {pago.monto_total:.2f} fue confirmado por PayPal.",
        "PAGO_CONFIRMADO",
        deep_link=f"/solicitudes/{solicitud.id}",
    )

    await db.commit()
    logger.info("Pago PayPal confirmado vía webhook: solicitud_id=%s, order_id=%s", solicitud.id, order_id)
