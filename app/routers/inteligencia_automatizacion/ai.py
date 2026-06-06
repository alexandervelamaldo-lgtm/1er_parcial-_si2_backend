from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.ai_request_logs import AiRequestLog
from app.models.users import User
from app.schemas.inteligencia_automatizacion.ai import (
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
from app.services.inteligencia_automatizacion.gemini_client import GeminiClient
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
