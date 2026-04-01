from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.middleware.error_handler import unhandled_exception_handler
from app.routers import auth, clientes, mapa, notificaciones, solicitudes, talleres, tecnicos, vehiculos


settings = get_settings()
app = FastAPI(
    title="Sistema Inteligente de Asistencia de Emergencia Vehicular",
    version="1.0.0",
    docs_url="/docs",
)

app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(clientes.router)
app.include_router(tecnicos.router)
app.include_router(vehiculos.router)
app.include_router(solicitudes.router)
app.include_router(talleres.router)
app.include_router(notificaciones.router)
app.include_router(mapa.router)


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}
