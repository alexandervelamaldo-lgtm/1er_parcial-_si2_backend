from fastapi import APIRouter

from app.config import get_settings


router = APIRouter(prefix="/config", tags=["Config"])


@router.get("/maps-key")
async def get_maps_key() -> dict[str, str]:
    settings = get_settings()
    return {
        "mapboxPublicToken": settings.mapbox_public_token,
        "mapboxStyleUrl": settings.mapbox_style_url,
    }
