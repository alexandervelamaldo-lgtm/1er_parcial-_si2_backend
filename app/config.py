import asyncio
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class Settings(BaseSettings):
    database_url: str = Field(default="", alias="DATABASE_URL")
    secret_key: str = Field(default="", alias="SECRET_KEY")
    algorithm: str = Field(default="HS256", alias="ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=30,
        alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    cors_origins: List[str] = Field(default_factory=lambda: ["*"], alias="CORS_ORIGINS")
    cors_origin_regex: str = Field(default="", alias="CORS_ORIGIN_REGEX")
    app_env: str = Field(default="development", alias="APP_ENV")
    backend_base_url: str = Field(default="http://localhost:8000", alias="BACKEND_BASE_URL")
    mapbox_public_token: str = Field(default="", alias="MAPBOX_PUBLIC_TOKEN")
    mapbox_style_url: str = Field(default="mapbox://styles/mapbox/standard", alias="MAPBOX_STYLE_URL")
    firebase_credentials: str = Field(default="", alias="FIREBASE_CREDENTIALS")
    fcm_project_id: str = Field(default="", alias="FCM_PROJECT_ID")
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field(default="mailto:admin@example.com", alias="VAPID_SUBJECT")
    ai_provider: str = Field(default="mock", alias="AI_PROVIDER")
    ai_http_endpoint: str = Field(default="", alias="AI_HTTP_ENDPOINT")
    ai_api_key: str = Field(default="", alias="AI_API_KEY")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    gemini_text_model: str = Field(default="gemini-1.5-pro", alias="GEMINI_TEXT_MODEL")
    gemini_vision_model: str = Field(default="gemini-1.5-pro", alias="GEMINI_VISION_MODEL")
    ai_timeout_s: float = Field(default=2.0, alias="AI_TIMEOUT_S")
    tenant_databases: dict[str, str] = Field(default_factory=dict, alias="TENANT_DATABASES")
    default_tenant: str = Field(default="default", alias="DEFAULT_TENANT")
    # Estrategia de aislamiento multi-tenant:
    #   - "database": cada tenant tiene su propia base de datos física
    #     (comportamiento original, requiere CREATE DATABASE en Postgres).
    #     Usado en LOCAL y en proveedores que cobran por DB separada.
    #   - "schema":   todos los tenants comparten UNA sola DB pero cada
    #     uno tiene su propio schema PostgreSQL (CREATE SCHEMA tenant_X).
    #     Funciona en Render free / Heroku / Supabase free tier que
    #     incluyen 1 sola DB.
    tenant_strategy: str = Field(default="database", alias="TENANT_STRATEGY")
    # Control plane: DB separada donde viven los SUPER_ADMIN — NO es un
    # tenant. Si está vacío, derivamos automáticamente a partir de
    # `database_url` cambiando el nombre de la database a `<x>_control`.
    # Esto permite spin-up sin configurar nada extra. En producción es
    # mejor setear CONTROL_DATABASE_URL explícitamente.
    control_database_url: str = Field(default="", alias="CONTROL_DATABASE_URL")
    # JWT del super-admin expira más rápido que el del usuario normal.
    # Sus permisos son globales — la ventana de abuso debe ser corta.
    super_admin_token_expire_minutes: int = Field(default=15, alias="SUPER_ADMIN_TOKEN_EXPIRE_MINUTES")
    # PayPal — credentials stay server-side, never exposed to mobile clients
    paypal_client_id: str = Field(default="", alias="PAYPAL_CLIENT_ID")
    paypal_client_secret: str = Field(default="", alias="PAYPAL_CLIENT_SECRET")
    paypal_mode: str = Field(default="sandbox", alias="PAYPAL_MODE")  # "sandbox" | "live"
    paypal_webhook_id: str = Field(default="", alias="PAYPAL_WEBHOOK_ID")
    paypal_currency: str = Field(default="USD", alias="PAYPAL_CURRENCY")
    # OpenAI — used server-side only (Whisper transcription + GPT-4o-mini vision).
    # NEVER expose to clients. Cuando AI_PROVIDER="openai" estos modelos
    # reemplazan a Gemini para análisis de imagen + transcripción + estimación
    # de costo de reparación. Las claves de Gemini quedan deprecated pero
    # operativas (rollback con AI_PROVIDER="gemini").
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_vision_model: str = Field(default="gpt-4o-mini", alias="OPENAI_VISION_MODEL")
    openai_audio_model: str = Field(default="whisper-1", alias="OPENAI_AUDIO_MODEL")
    openai_vision_timeout_s: float = Field(default=30.0, alias="OPENAI_VISION_TIMEOUT_S")
    openai_audio_timeout_s: float = Field(default=60.0, alias="OPENAI_AUDIO_TIMEOUT_S")
    # Transcripción de audio: por defecto OpenAI Whisper, pero endpoint y clave
    # son configurables para apuntar a un proveedor compatible y GRATIS como
    # Groq (URL https://api.groq.com/openai/v1/audio/transcriptions, modelo
    # whisper-large-v3-turbo). Si OPENAI_AUDIO_API_KEY queda vacío se usa
    # OPENAI_API_KEY, de modo que la configuración previa sigue funcionando.
    # La clave NUNCA se expone al cliente (igual que el resto: solo backend).
    openai_audio_url: str = Field(
        default="https://api.openai.com/v1/audio/transcriptions",
        alias="OPENAI_AUDIO_URL",
    )
    openai_audio_api_key: str = Field(default="", alias="OPENAI_AUDIO_API_KEY")
    # Feature flag: si la calidad de la estimación no satisface en producción,
    # se apaga sin tocar código. Cuando es False, las solicitudes se crean
    # sin costo_ia_* (queda en None) y requiere_revision_humana=True.
    ia_costo_habilitado: bool = Field(default=True, alias="IA_COSTO_HABILITADO")
    # ── Backups ──────────────────────────────────────────────────────────
    # Carpeta del binario pg_dump/pg_restore. Si queda vacío se auto-descubre
    # (PATH y, en Windows, la instalación estándar de PostgreSQL). En Linux/
    # contenedores normalmente está en el PATH y no hace falta setearlo.
    pg_bin_dir: str = Field(default="", alias="PG_BIN_DIR")
    # Carpeta donde se guardan los respaldos generados. Si es relativa, se
    # resuelve contra la raíz del backend. Por defecto `<backend>/backups`.
    backups_dir: str = Field(default="", alias="BACKUPS_DIR")

    model_config = SettingsConfigDict(
        env_file=(
            str(Path(__file__).resolve().parents[1] / ".env"),
            ".env",
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.database_url = _normalize_database_url(settings.database_url)
    settings.tenant_databases = _normalize_tenant_databases(settings.tenant_databases, settings.database_url)
    settings.cors_origins = _normalize_cors_origins(settings.cors_origins)
    if not settings.database_url or not settings.secret_key:
        raise RuntimeError("DATABASE_URL y SECRET_KEY son obligatorias")
    return settings


def _normalize_database_url(database_url: str) -> str:
    normalized = (database_url or "").strip()
    if normalized.startswith("postgres://"):
        return normalized.replace("postgres://", "postgresql+asyncpg://", 1)
    if normalized.startswith("postgresql://") and "+asyncpg" not in normalized:
        return normalized.replace("postgresql://", "postgresql+asyncpg://", 1)
    return normalized


def _normalize_cors_origins(cors_origins: List[str] | str) -> List[str]:
    if isinstance(cors_origins, list):
        return cors_origins
    raw = cors_origins.strip()
    if not raw:
        return ["*"]
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        if isinstance(parsed, list):
            return [str(origin).strip() for origin in parsed if str(origin).strip()]
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _normalize_tenant_databases(value: dict[str, str] | str, default_database_url: str) -> dict[str, str]:
    if isinstance(value, dict):
        normalized = {str(k).strip(): _normalize_database_url(str(v)) for k, v in value.items() if str(k).strip() and str(v).strip()}
        return normalized or {"default": default_database_url}
    raw = (value or "").strip()
    if not raw:
        return {"default": default_database_url}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"default": default_database_url}
    if isinstance(parsed, dict):
        normalized = {str(k).strip(): _normalize_database_url(str(v)) for k, v in parsed.items() if str(k).strip() and str(v).strip()}
        return normalized or {"default": default_database_url}
    return {"default": default_database_url}
