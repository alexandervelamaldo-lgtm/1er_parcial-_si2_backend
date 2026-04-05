from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(default="", alias="DATABASE_URL")
    secret_key: str = Field(default="", alias="SECRET_KEY")
    algorithm: str = Field(default="HS256", alias="ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=30,
        alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    cors_origins: List[str] = Field(default_factory=lambda: ["*"], alias="CORS_ORIGINS")
    app_env: str = Field(default="development", alias="APP_ENV")
    backend_base_url: str = Field(default="http://localhost:8000", alias="BACKEND_BASE_URL")
    maps_api_key: str = Field(default="", alias="MAPS_API_KEY")
    firebase_credentials: str = Field(default="", alias="FIREBASE_CREDENTIALS")
    fcm_project_id: str = Field(default="", alias="FCM_PROJECT_ID")
    ai_provider: str = Field(default="mock", alias="AI_PROVIDER")
    ai_http_endpoint: str = Field(default="", alias="AI_HTTP_ENDPOINT")
    ai_api_key: str = Field(default="", alias="AI_API_KEY")

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.database_url or not settings.secret_key:
        raise RuntimeError("DATABASE_URL y SECRET_KEY son obligatorias")
    return settings
