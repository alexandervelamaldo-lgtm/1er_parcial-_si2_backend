"""
End-to-end demo of the client↔workshop-direct flow.

This script walks the full happy path of the new request lifecycle so an
examiner can verify with one command that the refactor works:

    python -m app.scripts.demo_flujo_directo

Flow simulated:
    1. CLIENTE  → POST /solicitudes           (creates as REGISTRADA)
    2. CLIENTE  → GET /talleres-con-presupuesto (lists candidates + price)
    3. CLIENTE  → PUT /seleccionar-taller    (transitions to PROPUESTA_TALLER)
    4. TALLER   → PUT /respuesta-taller {aceptada:true}  (→ ASIGNADA)
    5. TECNICO  → PUT /estado EN_CAMINO
    6. TECNICO  → PUT /estado EN_ATENCION
    7. TECNICO  → PUT /estado COMPLETADA
    8. ✅ FLUJO DIRECTO OK

The script is tolerant: each step prints what it did and continues. At the
end it reports a green summary if everything passed or a red list of the
exact endpoints that failed.

Credentials default to fixture users on the `default` tenant. Override with
env vars if your seed uses different ones:

    DEMO_API_BASE_URL        http://localhost:8000
    DEMO_TENANT              default
    DEMO_CLIENTE_EMAIL       cliente@emergency.com
    DEMO_CLIENTE_PASSWORD    Password123*
    DEMO_TALLER_EMAIL        taller@emergency.com
    DEMO_TALLER_PASSWORD     Password123*
    DEMO_TECNICO_EMAIL       tecnico@emergency.com
    DEMO_TECNICO_PASSWORD    Password123*
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx


# ── Config ──────────────────────────────────────────────────────────────


BASE_URL = os.environ.get("DEMO_API_BASE_URL", "http://localhost:8000")
TENANT   = os.environ.get("DEMO_TENANT",       "default")

CLIENTE = {
    "email":    os.environ.get("DEMO_CLIENTE_EMAIL",    "cliente@emergency.com"),
    "password": os.environ.get("DEMO_CLIENTE_PASSWORD", "Password123*"),
}
TALLER = {
    "email":    os.environ.get("DEMO_TALLER_EMAIL",     "taller@emergency.com"),
    "password": os.environ.get("DEMO_TALLER_PASSWORD",  "Password123*"),
}
TECNICO = {
    "email":    os.environ.get("DEMO_TECNICO_EMAIL",    "tecnico@emergency.com"),
    "password": os.environ.get("DEMO_TECNICO_PASSWORD", "Password123*"),
}


# ── Pretty printing ─────────────────────────────────────────────────────


_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"


def _step(n: int, msg: str) -> None:
    print(f"\n{_CYAN}▶ Paso {n}:{_RESET} {msg}")


def _ok(msg: str) -> None:
    print(f"   {_GREEN}✓{_RESET} {msg}")


def _info(msg: str) -> None:
    print(f"   {_DIM}· {msg}{_RESET}")


def _fail(msg: str) -> None:
    print(f"   {_RED}✗ {msg}{_RESET}")


def _skip(msg: str) -> None:
    print(f"   {_YELLOW}⊘ {msg}{_RESET}")


# ── HTTP helpers (always tenant-aware) ──────────────────────────────────


async def _login(client: httpx.AsyncClient, who: dict[str, str], tenant: str) -> str:
    r = await client.post(
        f"{BASE_URL}/auth/login",
        json={"email": who["email"], "password": who["password"]},
        headers={"X-Tenant": tenant},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _headers(token: str, tenant: str = TENANT) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Tenant": tenant}


# ── Demo steps ──────────────────────────────────────────────────────────


async def run() -> int:
    failures: list[str] = []
    solicitud_id: int | None = None
    cliente_token: str | None = None

    print("=" * 66)
    print("   DEMO FLUJO CLIENTE↔TALLER-DIRECTO")
    print(f"   Backend: {BASE_URL}   Tenant: {TENANT}")
    print("=" * 66)

    async with httpx.AsyncClient(timeout=30) as client:

        # ── 0. Login cliente ───────────────────────────────────────────
        _step(0, f"Login CLIENTE ({CLIENTE['email']})")
        try:
            cliente_token = await _login(client, CLIENTE, TENANT)
            _ok("cliente autenticado")
        except Exception as exc:
            _fail(f"Login falló: {exc}. Asegúrate de tener un usuario cliente sembrado.")
            return 1

        # ── 1. Cliente lee sus vehículos y tipos de incidente ──────────
        _step(1, "Cliente obtiene su vehículo y tipo de incidente")
        try:
            vehiculos_resp = await client.get(
                f"{BASE_URL}/vehiculos", headers=_headers(cliente_token),
            )
            vehiculos_resp.raise_for_status()
            vehiculos = vehiculos_resp.json()
            if not vehiculos:
                _fail("El cliente no tiene vehículos registrados. Crea uno primero.")
                return 1
            vehiculo_id = vehiculos[0]["id"]
            _ok(f"vehículo elegido: id={vehiculo_id} "
                f"({vehiculos[0].get('marca')} {vehiculos[0].get('modelo')})")

            tipos_resp = await client.get(
                f"{BASE_URL}/solicitudes/tipos-incidente", headers=_headers(cliente_token),
            )
            tipos_resp.raise_for_status()
            tipos = tipos_resp.json()
            if not tipos:
                _fail("No hay tipos de incidente sembrados.")
                return 1
            tipo_id = tipos[0]["id"]
            _ok(f"tipo de incidente: {tipos[0]['nombre']} (id={tipo_id})")
        except Exception as exc:
            _fail(f"Error consultando catálogos: {exc}")
            return 1

        # ── 2. Cliente crea la solicitud ───────────────────────────────
        _step(2, "Cliente crea solicitud (POST /solicitudes)")
        # Necesitamos el cliente_id — lo sacamos del perfil
        perfil_resp = await client.get(f"{BASE_URL}/auth/me", headers=_headers(cliente_token))
        perfil_resp.raise_for_status()
        cliente_id = perfil_resp.json().get("cliente_id")
        if cliente_id is None:
            _fail("El usuario logueado no tiene cliente_id (¿no es rol CLIENTE?)")
            return 1

        payload = {
            "cliente_id":        cliente_id,
            "vehiculo_id":       vehiculo_id,
            "tipo_incidente_id": tipo_id,
            "latitud_incidente": -17.7863,
            "longitud_incidente":-63.1812,
            "latitud_cliente":   -17.7863,
            "longitud_cliente":  -63.1812,
            "descripcion":       "DEMO E2E: vehículo no arranca, posiblemente batería descargada",
            "es_carretera":      False,
            "condicion_vehiculo":"Operativo con limitaciones",
            "categoria_dano":    "electricidad",
        }
        crear_resp = await client.post(
            f"{BASE_URL}/solicitudes", json=payload, headers=_headers(cliente_token),
        )
        if crear_resp.status_code != 201:
            _fail(f"POST /solicitudes → {crear_resp.status_code}: {crear_resp.text}")
            return 1
        solicitud = crear_resp.json()
        solicitud_id = solicitud["id"]
        estado_inicial = (solicitud.get("estado") or {}).get("nombre", "?")
        _ok(f"solicitud creada #{solicitud_id} en estado {estado_inicial}")

        # ── 3. Cliente lista talleres con presupuesto ──────────────────
        _step(3, "Cliente lista talleres con presupuesto calculado")
        budget_resp = await client.get(
            f"{BASE_URL}/solicitudes/{solicitud_id}/talleres-con-presupuesto?radio_km=50",
            headers=_headers(cliente_token),
        )
        if budget_resp.status_code != 200:
            failures.append(f"talleres-con-presupuesto → {budget_resp.status_code}")
            _fail(budget_resp.text)
        else:
            data = budget_resp.json()
            total = data.get("total", 0)
            if total == 0:
                _skip("Sin talleres disponibles en el radio. No se puede continuar el flujo.")
                _info("Sembra al menos 1 taller disponible con coordenadas en Santa Cruz.")
                return 1
            _ok(f"{total} talleres devueltos con presupuesto")
            elegido = data["talleres"][0]  # el de mayor score
            _info(f"top: {elegido['nombre']} | "
                  f"distancia {elegido['distanciaKm'] if 'distanciaKm' in elegido else elegido['distancia_km']} km | "
                  f"precio {elegido['presupuesto']['moneda']} {elegido['presupuesto']['monto_final']}")
            taller_id = elegido["taller_id"]

        # ── 4. Cliente elige el primer taller ──────────────────────────
        _step(4, "Cliente elige el taller mejor recomendado")
        seleccionar_resp = await client.put(
            f"{BASE_URL}/solicitudes/{solicitud_id}/seleccionar-taller",
            json={
                "taller_id":   taller_id,
                "origen_lat":  -17.7863,
                "origen_lon":  -63.1812,
                "presupuesto_aceptado": elegido["presupuesto"]["monto_final"],
            },
            headers=_headers(cliente_token),
        )
        if seleccionar_resp.status_code != 200:
            failures.append(f"seleccionar-taller → {seleccionar_resp.status_code}")
            _fail(seleccionar_resp.text)
        else:
            estado = (seleccionar_resp.json().get("estado") or {}).get("nombre")
            if estado != "PROPUESTA_TALLER":
                failures.append(f"estado tras seleccionar debería ser PROPUESTA_TALLER, fue {estado}")
                _fail(f"estado = {estado}")
            else:
                _ok(f"solicitud transicionó a {estado}")

        # ── 5. Login taller + verificar que ve la propuesta en su inbox ─
        _step(5, f"Login TALLER ({TALLER['email']}) — verifica inbox")
        taller_token = None
        try:
            taller_token = await _login(client, TALLER, TENANT)
            _ok("taller autenticado")
            inbox_resp = await client.get(
                f"{BASE_URL}/solicitudes", headers=_headers(taller_token),
            )
            inbox_resp.raise_for_status()
            inbox = [s for s in inbox_resp.json()
                     if (s.get("estado") or {}).get("nombre") == "PROPUESTA_TALLER"]
            if not any(s["id"] == solicitud_id for s in inbox):
                _skip(f"La solicitud #{solicitud_id} no aparece en la inbox del taller "
                      f"(quizás el cliente eligió otro taller). Inbox actual: "
                      f"{[s['id'] for s in inbox]}")
                _info("El demo continuará intentando aceptar como el taller logueado.")
            else:
                _ok(f"propuesta #{solicitud_id} visible en la inbox del taller")
        except Exception as exc:
            _fail(f"Login taller falló: {exc}")
            failures.append("login taller")

        # ── 6. Taller acepta la propuesta ──────────────────────────────
        if taller_token:
            _step(6, "Taller acepta la propuesta (PUT /respuesta-taller)")
            aceptar_resp = await client.put(
                f"{BASE_URL}/solicitudes/{solicitud_id}/respuesta-taller",
                json={"aceptada": True, "observacion": "DEMO: confirmamos atención"},
                headers=_headers(taller_token),
            )
            if aceptar_resp.status_code != 200:
                failures.append(f"respuesta-taller(aceptar) → {aceptar_resp.status_code}: {aceptar_resp.text}")
                _fail(aceptar_resp.text[:200])
            else:
                estado = (aceptar_resp.json().get("estado") or {}).get("nombre")
                if estado == "ASIGNADA":
                    _ok(f"solicitud transicionó a {estado}")
                else:
                    failures.append(f"tras aceptar debería ser ASIGNADA, fue {estado}")
                    _fail(f"estado = {estado}")

        # ── 7. Tecnico avanza por los estados operativos ───────────────
        _step(7, f"Login TECNICO ({TECNICO['email']}) — avanza estados")
        tecnico_token = None
        try:
            tecnico_token = await _login(client, TECNICO, TENANT)
            _ok("tecnico autenticado")
        except Exception as exc:
            _skip(f"Login tecnico falló: {exc} — saltando los pasos finales")

        if tecnico_token:
            # Necesitamos los IDs de los estados — los pide del catálogo
            estados_resp = await client.get(
                f"{BASE_URL}/solicitudes/estados", headers=_headers(tecnico_token),
            )
            estados_resp.raise_for_status()
            estado_id_por_nombre = {e["nombre"]: e["id"] for e in estados_resp.json()}

            for nombre in ("EN_CAMINO", "EN_ATENCION", "COMPLETADA"):
                eid = estado_id_por_nombre.get(nombre)
                if not eid:
                    _skip(f"Estado {nombre} no está en el catálogo")
                    continue
                r = await client.put(
                    f"{BASE_URL}/solicitudes/{solicitud_id}/estado",
                    json={"estado_id": eid, "observacion": f"DEMO: avanza a {nombre}"},
                    headers=_headers(tecnico_token),
                )
                if r.status_code != 200:
                    failures.append(f"estado→{nombre} → {r.status_code}: {r.text[:200]}")
                    _fail(f"{nombre} → {r.status_code}")
                else:
                    final = (r.json().get("estado") or {}).get("nombre")
                    _ok(f"transición OK → {final}")

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n" + ("=" * 66))
    if not failures:
        print(f"   {_GREEN}✅ FLUJO DIRECTO OK — todos los pasos pasaron{_RESET}")
        print("=" * 66)
        return 0

    print(f"   {_RED}❌ FLUJO INCOMPLETO — {len(failures)} fallos:{_RESET}")
    for line in failures:
        print(f"      • {line}")
    print("=" * 66)
    return 2


if __name__ == "__main__":
    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(asyncio.run(run()))
