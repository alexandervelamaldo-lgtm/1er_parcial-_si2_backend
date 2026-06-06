"""Quién puede cerrar el trabajo (trabajo_terminado + costo_final).

Cubre el flujo "taller sin técnico": un taller dueño puede cerrar el trabajo
de su solicitud cuando NO hay técnico asignado. El técnico asignado mantiene
su camino habitual.
"""
from app.routers.gestion_solicitudes.solicitudes import can_finalize_work


def _check(roles, *, sol_tec, cur_tec, sol_tal, cur_tal):
    return can_finalize_work(
        roles,
        solicitud_tecnico_id=sol_tec,
        current_tecnico_id=cur_tec,
        solicitud_taller_id=sol_tal,
        current_taller_id=cur_tal,
    )


# ── Técnico asignado ──────────────────────────────────────────────────────
def test_tecnico_asignado_puede_cerrar() -> None:
    assert _check({"TECNICO"}, sol_tec=7, cur_tec=7, sol_tal=42, cur_tal=None)


def test_otro_tecnico_no_puede_cerrar() -> None:
    assert not _check({"TECNICO"}, sol_tec=7, cur_tec=9, sol_tal=42, cur_tal=None)


# ── Taller sin técnico ────────────────────────────────────────────────────
def test_taller_dueno_sin_tecnico_puede_cerrar() -> None:
    assert _check({"TALLER"}, sol_tec=None, cur_tec=None, sol_tal=42, cur_tal=42)


def test_taller_no_puede_cerrar_si_hay_tecnico_asignado() -> None:
    # Con técnico asignado, el cierre es responsabilidad del técnico.
    assert not _check({"TALLER"}, sol_tec=7, cur_tec=None, sol_tal=42, cur_tal=42)


def test_taller_ajeno_no_puede_cerrar() -> None:
    assert not _check({"TALLER"}, sol_tec=None, cur_tec=None, sol_tal=42, cur_tal=99)


def test_taller_sin_taller_id_en_solicitud_no_puede() -> None:
    assert not _check({"TALLER"}, sol_tec=None, cur_tec=None, sol_tal=None, cur_tal=42)


# ── Otros roles ───────────────────────────────────────────────────────────
def test_cliente_no_puede_cerrar() -> None:
    assert not _check({"CLIENTE"}, sol_tec=None, cur_tec=None, sol_tal=42, cur_tal=42)


def test_admin_sin_rol_operativo_no_puede_cerrar() -> None:
    assert not _check({"ADMINISTRADOR"}, sol_tec=7, cur_tec=7, sol_tal=42, cur_tal=42)
