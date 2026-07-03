import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.middleware.error_handler import unhandled_exception_handler
from app.middleware.tenant_audit import TenantAuditMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
from app.routers.analytics import dashboard as analytics_dashboard
from app.routers.autenticacion_acceso import auth
from app.routers.gestion_operativa_web import backups, bitacora, clientes, kpis, notificaciones, talleres, tecnicos
import app.routers.gestion_solicitudes.chat as solicitudes_chat
import app.routers.gestion_solicitudes.solicitudes as solicitudes
import app.routers.gestion_solicitudes.vehiculos as vehiculos
from app.routers.inteligencia_automatizacion import ai as ai_router
from app.routers.pagos_facturacion import cotizaciones
from app.routers.pagos_facturacion import paypal as paypal_router
from app.routers import public_config
from app.routers import dev_tools
from app.routers import tenants as tenants_router
from app.routers.seguimiento_cliente_web import mapa
from app.routers.seguimiento_cliente_web import tracking_ws
from app.routers.sync import lote as sync_lote_router
from app.routers.voz import transcribir as voz_router


settings = get_settings()
allow_all_origins = settings.cors_origins == ["*"]
app = FastAPI(
    title="Sistema Inteligente de Asistencia de Emergencia Vehicular",
    version="1.0.0",
    docs_url="/docs",
)

app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_middleware(TenantAuditMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(clientes.router)
app.include_router(tecnicos.router)
app.include_router(vehiculos.router)
app.include_router(solicitudes.router)
app.include_router(solicitudes_chat.router)
app.include_router(ai_router.router)
app.include_router(public_config.router)
app.include_router(dev_tools.router)
app.include_router(talleres.router)
app.include_router(notificaciones.router)
app.include_router(kpis.router)
app.include_router(bitacora.router)
app.include_router(backups.router)
app.include_router(mapa.router)
app.include_router(tracking_ws.router)
app.include_router(cotizaciones.router)
app.include_router(paypal_router.router)
app.include_router(sync_lote_router.router)
app.include_router(voz_router.router)
app.include_router(tenants_router.router)
app.include_router(analytics_dashboard.router)


@app.on_event("startup")
async def _bootstrap_control_plane() -> None:
    """Crea el schema de la control DB al arrancar.

    Es idempotente — solo crea tablas faltantes. Si la control DB no
    está disponible (no configurada, host caído), loggeamos un warning
    pero NO impedimos el arranque del backend. El flujo de login normal
    sigue funcionando; solo se pierde la capacidad de login como super-
    admin hasta que la control DB esté lista.
    """
    try:
        from app.control_plane.database import init_control_db_schema
        await init_control_db_schema()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "control_plane — schema NO inicializado (%s). El login como super-admin no funcionará.",
            type(exc).__name__,
        )


@app.on_event("startup")
async def _sync_tenant_tables() -> None:
    """Crea tablas faltantes en cada tenant (idempotente).

    Base.metadata.create_all() con `checkfirst=True` (default) solo crea
    tablas que NO existen — no modifica columnas ni datos de las que ya
    están. Esto permite agregar tablas nuevas (ej. solicitud_chat_messages
    de la migración 021) sin necesidad de correr Alembic por tenant, útil
    en Render free (sin shell). Modelos existentes con cambios de columnas
    igual requieren migración explícita.

    Best-effort: cualquier fallo se loggea pero no impide el arranque.
    """
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        import app.models  # noqa: F401 - registrar todos los modelos en Base.metadata
        from app.database import Base
        from app.services.tenant_registry import tenant_registry
        from app.tenant_strategy import schema_translate_map_for_tenant

        tenants = tenant_registry.list_keys()
        if not tenants:
            return
        base_url = settings.database_url
        engine = create_async_engine(base_url, future=True)
        try:
            for tenant in tenants:
                try:
                    async with engine.begin() as conn:
                        translated = await conn.execution_options(
                            schema_translate_map=schema_translate_map_for_tenant(tenant),
                        )
                        await translated.run_sync(Base.metadata.create_all)
                    logging.getLogger(__name__).info(
                        "tenant_tables — sync OK tenant=%s", tenant,
                    )
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "tenant_tables — sync FALLÓ tenant=%s (%s)", tenant, type(exc).__name__,
                    )
        finally:
            await engine.dispose()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "tenant_tables — no se pudo sincronizar (%s).", type(exc).__name__,
        )


@app.on_event("startup")
async def _start_backup_scheduler() -> None:
    """Arranca el loop de respaldos automáticos por tenant.

    Es best-effort: si pg_dump no está o algo falla, se loggea pero NO se
    impide el arranque del backend. El scheduler revisa cada minuto qué
    tenants tienen un respaldo automático vencido (config en JSON por tenant).
    """
    try:
        from app.services.gestion_operativa_web import backup_service
        backup_service.start_scheduler()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "backup scheduler NO iniciado (%s).", type(exc).__name__,
        )


@app.on_event("shutdown")
async def _stop_backup_scheduler() -> None:
    try:
        from app.services.gestion_operativa_web import backup_service
        await backup_service.stop_scheduler()
    except Exception:  # noqa: BLE001
        pass


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}
