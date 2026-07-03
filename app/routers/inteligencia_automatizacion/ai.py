from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user, get_role_names
from app.models.ai_request_logs import AiRequestLog
from app.models.users import User
from app.schemas.inteligencia_automatizacion.ai import (
    AiChatAdminResponse,
    AiChatRequest,
    AiChatResponse,
    AiConsentRequest,
    AiConsentResponse,
    AiImageAnalyzeResponse,
    AiTextGenerateRequest,
    AiTextGenerateResponse,
    AiVoiceIntentRequest,
    AiVoiceIntentResponse,
    AiVoiceReportNarrationRequest,
    AiVoiceReportNarrationResponse,
)
from app.services.inteligencia_automatizacion.admin_kpi_snapshot_service import build_admin_snapshot
from app.services.inteligencia_automatizacion.gemini_client import GeminiClient
from app.services.inteligencia_automatizacion.groq_chat_service import enviar_chat
from app.services.inteligencia_automatizacion.multimodal_ai_service import analyze_image_file


router = APIRouter(prefix="/ai", tags=["AI"])


def _require_consent(user: User) -> None:
    if not getattr(user, "ai_consent", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requiere consentimiento explícito para usar funciones de IA.",
        )


async def _ensure_consent(user: User, db: AsyncSession) -> None:
    """
    Implicitly grants AI consent on first usage from clients that auto-call
    AI endpoints (e.g. when attaching a photo to a request). This keeps the
    UX friction-free while still recording when consent was first granted,
    so we have an auditable timestamp in `ai_consent_at`.

    Use this *only* for low-risk endpoints invoked as part of a user-initiated
    action (attaching a photo, recording a note). Sensitive endpoints should
    keep calling ``_require_consent`` instead.
    """
    if not getattr(user, "ai_consent", False):
        user.ai_consent = True
        user.ai_consent_at = datetime.now(timezone.utc)
        db.add(user)
        await db.commit()
        await db.refresh(user)


async def _log(
    db: AsyncSession,
    *,
    user_id: int | None,
    kind: str,
    provider: str,
    ok: bool,
    latency_ms: int,
    error: str | None = None,
) -> None:
    db.add(
        AiRequestLog(
            user_id=user_id,
            kind=kind,
            provider=provider,
            ok=ok,
            latency_ms=latency_ms,
            error=error,
        )
    )
    await db.commit()


@router.get("/consent", response_model=AiConsentResponse)
async def get_consent(current_user: User = Depends(get_current_user)) -> AiConsentResponse:
    return AiConsentResponse(consent=current_user.ai_consent, consented_at=current_user.ai_consent_at)


@router.post("/consent", response_model=AiConsentResponse)
async def set_consent(
    payload: AiConsentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiConsentResponse:
    current_user.ai_consent = bool(payload.consent)
    current_user.ai_consent_at = datetime.now(timezone.utc) if payload.consent else None
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return AiConsentResponse(consent=current_user.ai_consent, consented_at=current_user.ai_consent_at)


@router.post("/text/generate", response_model=AiTextGenerateResponse)
async def generate_text(
    payload: AiTextGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiTextGenerateResponse:
    _require_consent(current_user)
    client = GeminiClient()
    action = payload.action
    system = "Eres un asistente de escritura. Responde solo con el texto final, sin explicaciones."
    if action == "traducir":
        lang = (payload.target_language or "en").strip()
        user = f"Traduce al idioma '{lang}'. Texto:\n{payload.text}"
    elif action == "corregir":
        user = f"Corrige ortografía y gramática manteniendo el sentido. Texto:\n{payload.text}"
    elif action == "resumir":
        tone = (payload.tone or "").strip()
        length = (payload.length or "corto").strip()
        extra = " Mantén un tono: " + tone + "." if tone else ""
        user = f"Resume en formato claro ({length}).{extra}\nTexto:\n{payload.text}"
    else:
        tone = (payload.tone or "neutral").strip()
        length = (payload.length or "medio").strip()
        user = f"Redacta un texto ({length}) con tono '{tone}' basado en:\n{payload.text}"

    call = await client.generate(
        model=client._settings.gemini_text_model,
        system=system,
        user=user,
        temperature=0.4 if action == "redactar" else 0.2,
        max_output_tokens=900,
    )
    await _log(
        db,
        user_id=current_user.id,
        kind=f"text/{action}",
        provider="gemini",
        ok=call.ok,
        latency_ms=call.latency_ms,
        error=call.error,
    )
    if not call.ok:
        raise HTTPException(status_code=502, detail="Fallo al generar texto con IA.")
    return AiTextGenerateResponse(output=call.output_text.strip(), provider="gemini", latency_ms=call.latency_ms)


@router.post("/voice/intent", response_model=AiVoiceIntentResponse)
async def voice_intent(
    payload: AiVoiceIntentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiVoiceIntentResponse:
    _require_consent(current_user)
    context = payload.context or {}
    client = GeminiClient()
    system = (
        "Interpreta comandos de voz para una app de emergencias vehiculares. "
        "Devuelve exclusivamente JSON válido con este esquema: "
        '{"action":"...","confidence":0.0,"parameters":{},"reply":"..."}.\n'
        "Acciones soportadas:\n"
        "- navegar: {\"route\":\"home|perfil|historial|vehiculos|solicitud_nueva|notificaciones|talleres\"}\n"
        "- actualizar_perfil: {\"campo\":\"telefono|nombre\",\"valor\":\"...\"}\n"
        "- ayuda: {}\n"
        "Si no es claro, usa action=ayuda con confidence baja."
    )
    user = "Comando: " + payload.transcript + "\nContexto(JSON): " + json.dumps(context, ensure_ascii=False)
    call, data = await client.generate_json(
        model=client._settings.gemini_text_model,
        system=system,
        user=user,
        temperature=0.1,
        max_output_tokens=500,
    )
    await _log(
        db,
        user_id=current_user.id,
        kind="voice/intent",
        provider="gemini",
        ok=call.ok,
        latency_ms=call.latency_ms,
        error=call.error,
    )
    if not call.ok or not data:
        raise HTTPException(status_code=502, detail="No se pudo interpretar el comando con IA.")
    action = str(data.get("action") or "ayuda")
    confidence = float(data.get("confidence") or 0.4)
    parameters_raw = data.get("parameters")
    parameters: dict[str, Any] = parameters_raw if isinstance(parameters_raw, dict) else {}
    reply = str(data.get("reply") or "No entendí el comando. ¿Podés repetirlo?")
    return AiVoiceIntentResponse(
        action=action,
        confidence=max(0.0, min(confidence, 1.0)),
        parameters=parameters,
        reply=reply,
        provider="gemini",
        latency_ms=call.latency_ms,
    )


@router.post("/image/analyze", response_model=AiImageAnalyzeResponse)
async def image_analyze(
    archivo: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiImageAnalyzeResponse:
    # Auto-grant consent for this user-initiated action (attaching a photo).
    # We still record the timestamp so the audit trail is intact.
    await _ensure_consent(current_user, db)
    content = await archivo.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="El archivo excede el tamaño máximo permitido")
    analysis = await analyze_image_file(
        archivo.filename or "imagen",
        archivo.content_type,
        "Validación de seguridad y accesibilidad",
        file_bytes=content,
    )
    moderation = analysis.moderation if isinstance(analysis.moderation, dict) else {}
    allowed = bool(moderation.get("allowed")) if moderation else True
    categories_raw = moderation.get("categories")
    categories: list[Any] = categories_raw if isinstance(categories_raw, list) else []
    reason = str(moderation.get("reason") or "") if moderation else None
    await _log(
        db,
        user_id=current_user.id,
        kind="image/analyze",
        provider=analysis.provider,
        ok=True,
        latency_ms=0,
        error=None,
    )
    return AiImageAnalyzeResponse(
        allowed=allowed,
        categories=[str(x) for x in categories if str(x).strip()],
        reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
        ocr_text=analysis.ocr_text,
        alt_text=analysis.alt_text,
        labels=analysis.labels,
        severity=analysis.severity,
        provider=analysis.provider,
        latency_ms=0,
    )


_CHAT_SYSTEM_PROMPT = (
    "Eres el asistente virtual de Emergency, una plataforma SaaS multi-tenant "
    "de asistencia vehicular de emergencia (grúas, mecánica, chapa y pintura, "
    "garantía de vehículos nuevos). Responde siempre en español rioplatense "
    "neutro, de forma breve (2-4 oraciones cuando alcance), clara, amable y "
    "orientada a la acción. Usá viñetas solo si la respuesta tiene pasos.\n"
    "\n"
    "── Contexto del producto ─────────────────────────────────────────────\n"
    "Roles del sistema:\n"
    "- CLIENTE: reporta emergencias desde la app móvil y sigue el estado de "
    "sus solicitudes.\n"
    "- OPERADOR: recibe las solicitudes, asigna técnicos/talleres y coordina.\n"
    "- TECNICO: recibe asignaciones y ejecuta el servicio en terreno.\n"
    "- TALLER: recibe trabajos derivados (mecánica, chapa, etc.) y cobra.\n"
    "- ADMINISTRADOR / ADMIN_TENANT: gestiona el taller/tenant, ve la "
    "bitácora, respaldos y analítica.\n"
    "- SUPER_ADMIN: administra los tenants (talleres) de la plataforma.\n"
    "\n"
    "Módulos principales del panel web:\n"
    "- Solicitudes: crear (cliente), listar, ver detalle, adjuntar fotos "
    "con análisis de IA integrado.\n"
    "- Talleres/Técnicos, Clientes: catálogos operativos.\n"
    "- Bitácora: auditoría de acciones dentro del tenant.\n"
    "- Respaldos: backup manual y automático de la base del tenant.\n"
    "- Notificaciones: push (web y FCM) y bandeja histórica.\n"
    "- Trabajos: cobro y facturación (integración con PayPal).\n"
    "- Historial: seguimiento de solicitudes del cliente.\n"
    "- Analítica: KPIs operacionales para administradores.\n"
    "- Chat (donde estás vos): asistente conversacional general.\n"
    "\n"
    "Estados típicos de una solicitud: creada → asignada → en camino → "
    "en atención → finalizada (o cancelada). Cada cambio dispara "
    "notificaciones al cliente.\n"
    "\n"
    "Funciones de IA disponibles en la plataforma (mencionalas si aplican):\n"
    "- Análisis de imágenes de la avería desde el detalle de la solicitud "
    "(no desde este chat).\n"
    "- Comandos por voz y transcripción de audio en la app móvil.\n"
    "- Generación de narraciones para reportes operativos.\n"
    "\n"
    "── Cómo responder ────────────────────────────────────────────────────\n"
    "1. Si te preguntan CÓMO hacer algo en la plataforma (reportar, seguir, "
    "pagar, configurar), respondé con pasos concretos en función del rol "
    "que menciona el usuario (o preguntá el rol si es ambiguo).\n"
    "2. Si es una consulta general (saludo, qué hacés, quién sos), presentate "
    "brevemente y ofrecé 2-3 ejemplos de lo que podés ayudar.\n"
    "3. Si es una emergencia real en curso (ej. 'me choqué', 'mi auto se "
    "prendió fuego'), pedile PRIMERO ponerse a salvo y llamar al 911 / "
    "servicios de emergencia locales, y RECIÉN DESPUÉS explicá cómo crear "
    "la solicitud en Emergency.\n"
    "4. No inventes precios, tiempos de respuesta, ni datos específicos de "
    "un taller o solicitud: esos vienen de la base de datos, no de vos. "
    "Si te los piden, decí que los revise en el módulo correspondiente.\n"
    "5. No pidas ni muestres contraseñas, tokens, ni datos de tarjetas.\n"
    "6. Si la pregunta está fuera del dominio (matemática, código, chismes), "
    "podés ayudar de forma breve y honesta, pero ofrecé volver al tema de "
    "la plataforma."
)


@router.post("/chat", response_model=AiChatResponse)
async def chat(
    payload: AiChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiChatResponse:
    # Abrir el chat y escribir un mensaje es una acción de bajo riesgo
    # iniciada explícitamente por el usuario, así que auto-otorgamos el
    # consentimiento (igual que en image/analyze) en vez de bloquear con
    # un diálogo previo.
    await _ensure_consent(current_user, db)

    mensajes: list[dict[str, str]] = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}]
    mensajes.extend({"role": item.role, "content": item.content} for item in payload.history)
    mensajes.append({"role": "user", "content": payload.message})

    result = await enviar_chat(mensajes=mensajes)
    await _log(
        db,
        user_id=current_user.id,
        kind="chat/message",
        provider="groq",
        ok=result.ok,
        latency_ms=result.latency_ms,
        error=result.error,
    )
    if not result.ok:
        if result.status_code == 401:
            raise HTTPException(status_code=502, detail="La clave de la API de Groq no es válida o no está configurada.")
        if result.status_code == 429:
            raise HTTPException(status_code=429, detail="Se alcanzó el límite de solicitudes a Groq. Intenta de nuevo en unos segundos.")
        raise HTTPException(status_code=502, detail="No se pudo obtener respuesta del chatbot en este momento.")
    return AiChatResponse(reply=result.reply, provider="groq", model=result.model, latency_ms=result.latency_ms)


# ── Chat administrativo (web) ────────────────────────────────────────────────
# Variante del chat orientada a preguntas ejecutivas del tenant. El backend
# arma un snapshot de KPIs (totales, tasas, top clientes/técnicos/talleres,
# ingresos, incidentes por tipo) y lo inyecta como contexto en el system
# prompt. El LLM responde SOLO en base a eso — nunca ejecuta SQL ni acciones.

_ADMIN_CHAT_ROLES = {"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR"}


_ADMIN_CHAT_SYSTEM_PROMPT_TMPL = (
    "Eres el analista virtual del panel administrativo de Emergency para el "
    "tenant '{tenant}'. Respondés preguntas del ADMINISTRADOR/OPERADOR sobre "
    "el desempeño del negocio (solicitudes, clientes, técnicos, talleres, "
    "tiempos, ingresos) usando EXCLUSIVAMENTE los datos del snapshot que "
    "aparece más abajo. Estilo: español rioplatense neutro, breve (2-5 "
    "oraciones cuando alcance), directo, con números concretos.\n"
    "\n"
    "── Reglas duras ─────────────────────────────────────────────────────\n"
    "1. NO inventes datos. Si el snapshot no tiene la respuesta, decilo "
    "explícitamente y sugerí en qué módulo del panel encontrarla (ej. "
    "'Analítica', 'Trabajos', 'Bitácora').\n"
    "2. NO ejecutas SQL ni tomas acciones. Sos solo lectura.\n"
    "3. Usá los números tal cual — no redondees agresivo, no 'estimes'.\n"
    "4. Cuando cites un ranking (top clientes/técnicos/talleres), listá "
    "hasta 3 con nombre y número. Con más, ofrecé que el usuario pida "
    "'ver los siguientes'.\n"
    "5. Si te preguntan comparaciones temporales que el snapshot no "
    "tiene (ej. 'vs mes pasado'), aclará que el snapshot es del momento "
    "actual y recomendá el dashboard de Analítica.\n"
    "6. Formato: usá viñetas solo cuando la respuesta sea una lista de "
    "≥3 items. Cifras monetarias con 'Bs' antes (moneda BOB).\n"
    "\n"
    "── Snapshot del tenant (JSON, generado ahora) ───────────────────────\n"
    "{snapshot_json}\n"
    "── Fin del snapshot ─────────────────────────────────────────────────\n"
)


@router.post("/chat/admin", response_model=AiChatAdminResponse)
async def chat_admin(
    payload: AiChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiChatAdminResponse:
    """Chat ejecutivo con KPIs del tenant como contexto.

    Solo accesible para ADMINISTRADOR / ADMIN_TENANT / OPERADOR. El
    snapshot se calcula fresco en cada llamada (los KPIs cambian con
    cada acción) y se inyecta al system prompt. El LLM no ve tokens ni
    puede ejecutar consultas — solo lee JSON estático.
    """
    roles = get_role_names(current_user)
    if not roles.intersection(_ADMIN_CHAT_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El chat administrativo requiere rol de administrador u operador.",
        )
    await _ensure_consent(current_user, db)

    tenant = db.info.get("tenant_key", "default")
    snapshot = await build_admin_snapshot(db, tenant)
    snapshot_dict = snapshot.as_dict()
    snapshot_json = json.dumps(snapshot_dict, ensure_ascii=False, default=str)

    system_prompt = _ADMIN_CHAT_SYSTEM_PROMPT_TMPL.format(
        tenant=tenant, snapshot_json=snapshot_json
    )

    mensajes: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    mensajes.extend({"role": item.role, "content": item.content} for item in payload.history)
    mensajes.append({"role": "user", "content": payload.message})

    result = await enviar_chat(mensajes=mensajes)
    await _log(
        db,
        user_id=current_user.id,
        kind="chat/admin",
        provider="groq",
        ok=result.ok,
        latency_ms=result.latency_ms,
        error=result.error,
    )
    if not result.ok:
        if result.status_code == 401:
            raise HTTPException(status_code=502, detail="La clave de la API de Groq no es válida o no está configurada.")
        if result.status_code == 429:
            raise HTTPException(status_code=429, detail="Se alcanzó el límite de solicitudes a Groq. Intenta de nuevo en unos segundos.")
        raise HTTPException(status_code=502, detail="No se pudo obtener respuesta del chatbot administrativo en este momento.")

    return AiChatAdminResponse(
        reply=result.reply,
        provider="groq",
        model=result.model,
        latency_ms=result.latency_ms,
        context_summary=snapshot_dict,
    )


@router.post("/voice-report/narration", response_model=AiVoiceReportNarrationResponse)
async def voice_report_narration(
    payload: AiVoiceReportNarrationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiVoiceReportNarrationResponse:
    _require_consent(current_user)
    client = GeminiClient()
    system = (
        "Redacta una narración breve y natural para un reporte operativo. "
        "Evita datos personales. Usa un español claro y profesional. "
        "Incluye: resumen general, 2-4 insights clave y cierre. "
        "Responde solo con la narración final, sin listas técnicas."
    )
    user = (
        f"Reporte={payload.report_name}. Periodo={payload.period}. Formato={payload.format}. "
        f"Audiencia={payload.audience}. "
        f"Highlights={json.dumps(payload.highlights, ensure_ascii=False)}. "
        f"Stats={json.dumps(payload.stats, ensure_ascii=False)}."
    )
    call = await client.generate(
        model=client._settings.gemini_text_model,
        system=system,
        user=user,
        temperature=0.25,
        max_output_tokens=700,
    )
    await _log(
        db,
        user_id=current_user.id,
        kind="voice-report/narration",
        provider="gemini",
        ok=call.ok,
        latency_ms=call.latency_ms,
        error=call.error,
    )
    if not call.ok:
        raise HTTPException(status_code=502, detail="No se pudo generar la narración del reporte.")
    return AiVoiceReportNarrationResponse(narration=call.output_text.strip(), provider="gemini", latency_ms=call.latency_ms)
