"""Bootstrap del primer super-admin en la control DB.

Uso:
    python -m app.control_plane.bootstrap --email alex@platform.com --password Secret123*

    # O via env vars (útil en CI/containers):
    SUPERADMIN_EMAIL=alex@platform.com SUPERADMIN_PASSWORD=Secret123* \
        python -m app.control_plane.bootstrap

    # O interactivo (sin args ni env):
    python -m app.control_plane.bootstrap

Características:
  - Idempotente: si el email ya existe, NO falla; solo informa.
  - Inicializa el schema de la control DB si no existe.
  - NO loggea la password (solo el email).
  - Es la ÚNICA forma de crear el primer super-admin — no hay endpoint
    público de signup para este rol.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import re
import sys
from typing import Optional

from sqlalchemy import select

from app.control_plane.database import (
    get_control_sessionmaker,
    init_control_db_schema,
)
from app.control_plane.models.super_admin import SuperAdmin
from app.utils.auth import hash_password

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.control_plane.bootstrap",
        description="Crea el primer super-admin en la control DB.",
    )
    parser.add_argument("--email", help="Email del super-admin")
    parser.add_argument("--password", help="Password (>=8 chars). Si se omite, se pregunta interactivamente.")
    parser.add_argument("--display-name", help="Nombre para mostrar (opcional)")
    return parser.parse_args(argv)


def _resolve_email(arg_email: Optional[str]) -> str:
    email = (arg_email or os.getenv("SUPERADMIN_EMAIL") or "").strip().lower()
    if not email:
        try:
            email = input("Email del super-admin: ").strip().lower()
        except EOFError:
            email = ""
    if not EMAIL_REGEX.match(email):
        raise SystemExit("Email inválido. Usa formato user@dominio.com.")
    return email


def _resolve_password(arg_password: Optional[str]) -> str:
    pwd = arg_password or os.getenv("SUPERADMIN_PASSWORD")
    if not pwd:
        try:
            pwd = getpass.getpass("Password: ")
            confirm = getpass.getpass("Confirmar password: ")
            if pwd != confirm:
                raise SystemExit("Las contraseñas no coinciden.")
        except EOFError:
            raise SystemExit("Password requerido.")
    if len(pwd) < 8:
        raise SystemExit("La password debe tener al menos 8 caracteres.")
    return pwd


async def bootstrap_super_admin(
    email: str, password: str, display_name: str | None = None,
) -> tuple[SuperAdmin, bool]:
    """Crea (o reusa) un super-admin. Devuelve (registro, created_now)."""
    await init_control_db_schema()
    sessionmaker = get_control_sessionmaker()
    async with sessionmaker() as session:
        existing = await session.scalar(
            select(SuperAdmin).where(SuperAdmin.email == email)
        )
        if existing:
            logger.info("bootstrap — super-admin %s ya existe (id=%d), no-op.", email, existing.id)
            return existing, False

        admin = SuperAdmin(
            email=email,
            password_hash=hash_password(password),
            display_name=display_name,
            suspended=False,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        logger.info("bootstrap — super-admin %s creado (id=%d).", email, admin.id)
        return admin, True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv or sys.argv[1:])
    email = _resolve_email(args.email)
    password = _resolve_password(args.password)
    display_name = args.display_name or os.getenv("SUPERADMIN_DISPLAY_NAME")
    _, created = asyncio.run(bootstrap_super_admin(email, password, display_name))
    if created:
        print(f"✓ Super-admin '{email}' creado en control DB.")
    else:
        print(f"• Super-admin '{email}' ya existía — no se hizo nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
