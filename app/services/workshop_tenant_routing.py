import re
import unicodedata

from sqlalchemy import select


def normalize_incident_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    plain = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain).strip()


async def resolve_workshop_tenant_key(*, tipo_incidente_nombre: str | None) -> str | None:
    text = normalize_incident_text(tipo_incidente_nombre)
    if not text:
        return None

    try:
        from app.control_plane.database import get_control_sessionmaker
        from app.control_plane.models.incident_tenant_keyword import IncidentTenantKeyword

        sessionmaker = get_control_sessionmaker()
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    select(IncidentTenantKeyword).order_by(
                        IncidentTenantKeyword.priority.desc(),
                        IncidentTenantKeyword.keyword.desc(),
                    )
                )
            ).scalars().all()
            for row in rows:
                keyword = normalize_incident_text(row.keyword)
                if keyword and keyword in text:
                    return row.tenant_key
    except Exception:
        pass

    fallback = [
        ("llaneros", ["llanta", "neumatico", "ponchada", "pinchada", "pinchadura", "vulcan"]),
        ("chapa_pintura", ["carroceria", "chapa", "pintura"]),
        ("vehiculos_nuevos_garantia", ["garantia", "vehiculo nuevo", "nuevo"]),
        ("mecanica_general", ["motor", "mecanica", "electrico", "electronica"]),
    ]
    for tenant_key, keywords in fallback:
        if any(keyword in text for keyword in keywords):
            return tenant_key
    return "mecanica_general"
