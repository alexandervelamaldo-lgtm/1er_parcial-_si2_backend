import os

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    message = str(exc).lower()
    app_env = os.getenv("APP_ENV", "development").lower()
    is_production = app_env in {"prod", "production"}

    def is_db_connection_issue() -> bool:
        if (
            "connection was closed" in message
            or "connection refused" in message
            or "could not connect" in message
            or "timeout" in message
        ):
            return True
        if isinstance(exc, OperationalError):
            return True
        if asyncpg and isinstance(exc, (asyncpg.PostgresConnectionError, asyncpg.ConnectionDoesNotExistError)):
            return True
        if isinstance(exc, DBAPIError):
            orig = getattr(exc, "orig", None)
            if orig is None:
                return False
            orig_msg = str(orig).lower()
            if (
                "connection was closed" in orig_msg
                or "connection refused" in orig_msg
                or "could not connect" in orig_msg
                or "timeout" in orig_msg
            ):
                return True
            if asyncpg and isinstance(orig, (asyncpg.PostgresConnectionError, asyncpg.ConnectionDoesNotExistError)):
                return True
        return False

    if is_db_connection_issue():
        return JSONResponse(
            status_code=503,
            content={
                "message": "Servicio temporalmente no disponible",
                "detail": "La base de datos no está disponible. Verifica la conexión y vuelve a intentar.",
            },
        )

    detail = "Ocurrió un error interno no controlado"
    if not is_production:
        detail = str(exc)[:800]
    return JSONResponse(
        status_code=500,
        content={
            "message": "Ocurrió un error interno no controlado",
            "detail": detail,
        },
    )
