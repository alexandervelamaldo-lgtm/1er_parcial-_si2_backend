from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from app.database import get_tenant_sessionmaker
from app.models.roles import Role
from app.models.taller_categorias import CategoriaTaller
from app.models.talleres import Taller
from app.models.users import User
from app.utils.auth import hash_password


# Nombre legible por slug de CategoriaTaller. Debe coincidir con los slugs que
# usa el filtrado estricto (taller_filtro_service) y la normalización de daño.
CATEGORIA_NOMBRES: dict[str, str] = {
    "llantas": "Llantas",
    "chaperia_pintura": "Chapería y pintura",
    "motor": "Motor y mecánica",
    "electricidad": "Sistemas eléctricos",
    "suspension": "Suspensión",
    "general": "General",
}


TENANTS: dict[str, dict] = {
    "mecanica_general": {
        "label": "Mecánica General",
        "services": ["mecanica", "motor", "electronica", "electrico"],
        # Un taller de motor y otro eléctrico: ambos daños se enrutan a este
        # tenant, así el filtrado estricto encuentra el especializado correcto.
        "category_slugs": ["motor", "electricidad"],
        "workshops": [
            {
                "name": "Taller Mecánica Segundo Anillo Norte",
                "address": "Av. Cristo Redentor, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.7709,
                "lng": -63.1834,
                "phone": "+591 69000001",
            },
            {
                "name": "Electro-Mecánica 2do Anillo Este",
                "address": "Av. Alemania, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.7816,
                "lng": -63.1692,
                "phone": "+591 69000002",
            },
        ],
    },
    "llaneros": {
        "label": "Llaneros",
        "services": ["llantas", "pinchadura", "vulcanizacion", "auxilio_llantas"],
        "category_slugs": ["llantas", "llantas"],
        "workshops": [
            {
                "name": "Llantero Express Segundo Anillo Sur",
                "address": "Av. Santos Dumont, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.8042,
                "lng": -63.1811,
                "phone": "+591 69000003",
            },
            {
                "name": "Vulcanización 2do Anillo Oeste",
                "address": "Av. Banzer, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.7897,
                "lng": -63.1978,
                "phone": "+591 69000004",
            },
        ],
    },
    "chapa_pintura": {
        "label": "Chapa y Pintura",
        "services": ["chapa", "pintura", "carroceria", "latoneria"],
        "category_slugs": ["chaperia_pintura", "chaperia_pintura"],
        "workshops": [
            {
                "name": "Chapería Segundo Anillo Centro",
                "address": "Av. Grigotá, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.7991,
                "lng": -63.1786,
                "phone": "+591 69000005",
            },
            {
                "name": "Pintura Rápida 2do Anillo Norte",
                "address": "Av. Beni, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.7718,
                "lng": -63.1762,
                "phone": "+591 69000006",
            },
        ],
    },
    "vehiculos_nuevos_garantia": {
        "label": "Vehículos Nuevos (Garantía)",
        "services": ["garantia", "vehiculos_nuevos", "postventa", "revision"],
        # Garantía/postventa no es un daño específico → cae en 'general'.
        "category_slugs": ["general", "general"],
        "workshops": [
            {
                "name": "Centro Garantía Segundo Anillo Este",
                "address": "Av. Mutualista, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.7773,
                "lng": -63.1639,
                "phone": "+591 69000007",
            },
            {
                "name": "Postventa 2do Anillo Sur",
                "address": "Av. Virgen de Cotoca, cerca del 2do Anillo, Santa Cruz de la Sierra",
                "lat": -17.8032,
                "lng": -63.1655,
                "phone": "+591 69000008",
            },
        ],
    },
}


async def _ensure_role(session, name: str) -> Role:
    role = await session.scalar(select(Role).where(Role.name == name))
    if role is None:
        role = Role(name=name)
        session.add(role)
        await session.flush()
    return role


async def _ensure_categoria(session, slug: str) -> CategoriaTaller:
    categoria = await session.scalar(select(CategoriaTaller).where(CategoriaTaller.slug == slug))
    if categoria is None:
        categoria = CategoriaTaller(
            slug=slug,
            nombre=CATEGORIA_NOMBRES.get(slug, slug.replace("_", " ").title()),
            descripcion=None,
        )
        session.add(categoria)
        await session.flush()
    return categoria


async def _ensure_workshop(
    *,
    tenant_key: str,
    index: int,
    email: str,
    password: str,
    name: str,
    address: str,
    lat: float,
    lng: float,
    phone: str,
    services: list[str],
    categoria_slug: str,
) -> None:
    sessionmaker = get_tenant_sessionmaker(tenant_key)
    async with sessionmaker() as session:
        role = await _ensure_role(session, "TALLER")
        categoria = await _ensure_categoria(session, categoria_slug)

        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=hash_password(password), is_active=True)
            user.roles.append(role)
            session.add(user)
            await session.flush()
        else:
            user.password_hash = hash_password(password)
            if role not in user.roles:
                user.roles.append(role)

        taller = await session.scalar(select(Taller).where(Taller.user_id == user.id))
        if taller is None:
            taller = Taller(
                user_id=user.id,
                categoria_id=categoria.id,
                nombre=name,
                direccion=address,
                latitud=float(lat),
                longitud=float(lng),
                telefono=phone,
                capacidad=8 + index,
                servicios="|".join(services),
                disponible=True,
                acepta_automaticamente=False,
            )
            session.add(taller)
        else:
            taller.categoria_id = categoria.id
            taller.nombre = name
            taller.direccion = address
            taller.latitud = float(lat)
            taller.longitud = float(lng)
            taller.telefono = phone
            taller.capacidad = 8 + index
            taller.servicios = "|".join(services)
            taller.disponible = True

        await session.commit()


async def main() -> None:
    base_domain = os.environ.get("WORKSHOP_EMAIL_DOMAIN", "platform.com").strip() or "platform.com"
    password = os.environ.get("WORKSHOP_DEFAULT_PASSWORD", "Workshop123!").strip() or "Workshop123!"

    for tenant_key, cfg in TENANTS.items():
        services = list(cfg.get("services") or [])
        workshops = list(cfg.get("workshops") or [])
        category_slugs = list(cfg.get("category_slugs") or [])
        for idx, w in enumerate(workshops, start=1):
            email = f"taller{idx}.{tenant_key}@{base_domain}"
            # category_slugs es paralelo a workshops por índice; si falta, cae en
            # 'general' (multiservicio) para no romper el filtrado estricto.
            categoria_slug = category_slugs[idx - 1] if idx - 1 < len(category_slugs) else "general"
            await _ensure_workshop(
                tenant_key=tenant_key,
                index=idx,
                email=email,
                password=password,
                name=w["name"],
                address=w["address"],
                lat=w["lat"],
                lng=w["lng"],
                phone=w["phone"],
                services=services,
                categoria_slug=categoria_slug,
            )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

