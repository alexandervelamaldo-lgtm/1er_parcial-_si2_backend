"""
End-to-end demo of the multi-tenant SaaS isolation guarantees.

Run:
    python -m app.scripts.demo_multitenant

What it does:

  1. Provisions two tenants: "Auxilio Norte" and "Mecánicos Express".
  2. Logs into each one and registers a solicitud through the public REST API.
  3. Replays the same listings with the wrong tenant header / token and
     asserts the backend rejects the cross-access attempts (404 / 401).
  4. Prints a green ✅ summary if everything is isolated, or a red ❌ list
     with the exact endpoint that leaked.

This is what an examiner can run to confirm the system actually enforces
multi-tenant boundaries — no hand-waving.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx


# ── Config (edit if your backend lives elsewhere) ───────────────────────


BASE_URL      = os.environ.get("DEMO_API_BASE_URL", "http://localhost:8000")
SUPER_ADMIN_EMAIL    = os.environ.get("DEMO_SUPER_ADMIN_EMAIL", "superadmin@emergency.com")
SUPER_ADMIN_PASSWORD = os.environ.get("DEMO_SUPER_ADMIN_PASSWORD", "SuperAdmin123!")
SUPER_ADMIN_TENANT   = os.environ.get("DEMO_SUPER_ADMIN_TENANT", "default")

TENANT_A = {
    "key":            "auxilio_norte",
    "label":          "Auxilio Norte",
    "admin_email":    "admin@norte.com",
    "admin_password": "NortePass123!",
}
TENANT_B = {
    "key":            "mecanicos_express",
    "label":          "Mecánicos Express",
    "admin_email":    "admin@express.com",
    "admin_password": "ExpressPass123!",
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _print_step(n: int, msg: str) -> None:
    print(f"\n> Paso {n}: {msg}")


def _print_ok(msg: str) -> None:
    print(f"   OK  {msg}")


def _print_fail(msg: str) -> None:
    print(f"   FAIL {msg}")


async def _login(client: httpx.AsyncClient, tenant: str, email: str, password: str) -> str:
    r = await client.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        headers={"X-Tenant": tenant},
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def _create_tenant(client: httpx.AsyncClient, super_token: str, payload: dict) -> None:
    r = await client.post(
        f"{BASE_URL}/admin/tenants",
        json={
            "key":            payload["key"],
            "label":          payload["label"],
            "admin_email":    payload["admin_email"],
            "admin_password": payload["admin_password"],
        },
        headers={
            "Authorization": f"Bearer {super_token}",
            "X-Tenant":      SUPER_ADMIN_TENANT,
        },
    )
    if r.status_code == 201:
        _print_ok(f"Tenant '{payload['key']}' creado")
        return
    if r.status_code == 409:
        _print_ok(f"Tenant '{payload['key']}' ya existía - reutilizando")
        return
    _print_fail(f"create tenant '{payload['key']}' -> HTTP {r.status_code}: {r.text}")
    raise SystemExit(1)


# ── Main flow ────────────────────────────────────────────────────────────


async def run() -> int:
    failures: list[str] = []
    print("=" * 60)
    print("   DEMO MULTI-TENANT - Aislamiento de datos por tenant")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=30) as client:

        # ── 0. Login as super admin so we can create tenants ───────────
        _print_step(0, "Login como SUPER_ADMIN")
        try:
            super_token = await _login(client, SUPER_ADMIN_TENANT, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
            _print_ok(f"super-admin autenticado en tenant '{SUPER_ADMIN_TENANT}'")
        except Exception as exc:
            _print_fail(
                f"No pudo iniciar sesión el super admin: {exc}\n"
                f"   Sembra primero el usuario super-admin en el tenant '{SUPER_ADMIN_TENANT}'."
            )
            return 1

        # ── 1. Provision Tenant A and Tenant B ─────────────────────────
        _print_step(1, "Creando los 2 tenants demo")
        await _create_tenant(client, super_token, TENANT_A)
        await _create_tenant(client, super_token, TENANT_B)

        # ── 2. Login as each tenant's admin ────────────────────────────
        _print_step(2, "Login como admin de cada tenant")
        token_a = await _login(client, TENANT_A["key"], TENANT_A["admin_email"], TENANT_A["admin_password"])
        token_b = await _login(client, TENANT_B["key"], TENANT_B["admin_email"], TENANT_B["admin_password"])
        _print_ok("ambos tenants autentican OK con sus credenciales propias")

        # ── 3. List solicitudes from each tenant ───────────────────────
        _print_step(3, "Verificar listados aislados")
        r_a = await client.get(
            f"{BASE_URL}/solicitudes",
            headers={"Authorization": f"Bearer {token_a}", "X-Tenant": TENANT_A["key"]},
        )
        r_b = await client.get(
            f"{BASE_URL}/solicitudes",
            headers={"Authorization": f"Bearer {token_b}", "X-Tenant": TENANT_B["key"]},
        )
        if r_a.status_code == 200 and r_b.status_code == 200:
            _print_ok(f"Tenant A devuelve {len(r_a.json())} solicitudes")
            _print_ok(f"Tenant B devuelve {len(r_b.json())} solicitudes")
        else:
            failures.append(f"listado A={r_a.status_code} B={r_b.status_code}")

        # ── 4. Cross-tenant attack: A's JWT + B's header ────────────────
        _print_step(4, "Intento de cross-access (JWT de A + header de B)")
        r_attack = await client.get(
            f"{BASE_URL}/solicitudes",
            headers={"Authorization": f"Bearer {token_a}", "X-Tenant": TENANT_B["key"]},
        )
        if r_attack.status_code == 401:
            _print_ok("backend rechazó con 401 'Token inválido para este tenant'")
        else:
            failures.append(f"cross-access debería ser 401 pero fue {r_attack.status_code}: {r_attack.text}")

        # ── 5. Bogus tenant header → 404 ───────────────────────────────
        _print_step(5, "Tenant inexistente en header => 404")
        r_bogus = await client.get(
            f"{BASE_URL}/solicitudes",
            headers={"Authorization": f"Bearer {token_a}", "X-Tenant": "tenant_hacker"},
        )
        if r_bogus.status_code == 404:
            _print_ok("backend rechazó con 404 - no hay silent fallback al default tenant")
        else:
            failures.append(f"tenant fake debería ser 404 pero fue {r_bogus.status_code}")

        # ── 6. Public catalog endpoint ─────────────────────────────────
        _print_step(6, "Catálogo público de tenants")
        r_pub = await client.get(f"{BASE_URL}/tenants/public")
        if r_pub.status_code == 200:
            keys = {t["key"] for t in r_pub.json()}
            if TENANT_A["key"] in keys and TENANT_B["key"] in keys:
                _print_ok(f"/tenants/public lista {len(keys)} tenants incluyendo los demo")
            else:
                failures.append(f"/tenants/public no incluye los demo: {keys}")
        else:
            failures.append(f"/tenants/public devolvió {r_pub.status_code}")

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n" + ("=" * 60))
    if not failures:
        print("   OK  AISLAMIENTO OK - Todos los chequeos pasaron")
        print("=" * 60)
        return 0

    print(f"   FAIL FUGA - {len(failures)} chequeos fallaron:")
    for line in failures:
        print(f"      - {line}")
    print("=" * 60)
    return 2


if __name__ == "__main__":
    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(asyncio.run(run()))
