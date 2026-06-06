from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path
import os

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db


router = APIRouter(prefix="/dev", tags=["Dev"])


class DevSeedRequest(BaseModel):
    confirm: str


class DevSeedResponse(BaseModel):
    ok: bool


@router.post("/seed", response_model=DevSeedResponse)
async def seed_demo_data(
    payload: DevSeedRequest,
    db: AsyncSession = Depends(get_db),
) -> DevSeedResponse:
    settings = get_settings()
    if settings.app_env.lower() not in {"development", "dev"}:
        raise HTTPException(status_code=404, detail="No disponible")
    if payload.confirm.strip().upper() != "RESET":
        raise HTTPException(status_code=400, detail="Confirmación inválida")

    from seed import seed as seed_fn

    try:
        os.environ.setdefault("DATABASE_URL", settings.database_url)
        from alembic import command
        from alembic.config import Config
        import anyio

        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        await anyio.to_thread.run_sync(command.upgrade, cfg, "head")
        await seed_fn()
        await db.commit()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Fallo al sembrar datos demo: {type(exc).__name__}: {exc}")
    return DevSeedResponse(ok=True)
