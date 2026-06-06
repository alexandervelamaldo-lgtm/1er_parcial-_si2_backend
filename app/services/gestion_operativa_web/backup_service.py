"""Servicio de respaldos por tenant (pg_dump / pg_restore).

Cada organización (tenant) puede respaldar su propia base de datos desde el
panel web. El respaldo es un dump en formato *custom* de PostgreSQL
(`pg_dump -F c`), comprimido y apto para restauración selectiva con
`pg_restore`.

Decisiones de diseño:
  - **Aislamiento por tenant**: el router resuelve el tenant del request y
    nunca acepta un tenant arbitrario, así un taller jamás respalda la BD de
    otro. En modo `database` se dumpea la BD física del tenant; en modo
    `schema` se dumpea solo su schema (`-n tenant_<key>`).
  - **Subprocess en hilo**: en Windows el event loop es el *Selector* (lo fija
    `app.config` para asyncpg) y ese loop NO soporta subprocesos asyncio. Por
    eso ejecutamos pg_dump/pg_restore con `subprocess.run` dentro de
    `asyncio.to_thread`, que funciona igual en Windows y Linux.
  - **Automático sin dependencias nuevas**: un loop asyncio liviano arranca en
    el startup de la app y corre los respaldos programados (config en un JSON
    por tenant). No añadimos APScheduler ni cron.
  - **Best-effort**: ningún fallo de respaldo rompe la app. El scheduler traga
    y loggea cualquier excepción.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import get_settings

logger = logging.getLogger("backup")

# pg_dump/pg_restore pueden tardar en bases grandes; tope defensivo.
_TIMEOUT_S = 600
_LOCK = threading.Lock()

_TENANT_RE = re.compile(r"^[a-z0-9_]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.dump$")
_VALID_FREQS = ("hourly", "daily", "weekly")

_DEFAULT_SCHEDULE: dict = {
    "enabled": False,
    "frequency": "daily",
    "hour": 2,
    "retention": 7,
    "last_run": None,
}


# ── Excepciones ──────────────────────────────────────────────────────────


class BackupError(RuntimeError):
    """Fallo genérico de respaldo/restauración."""


class BackupNotFound(BackupError):
    """El archivo de respaldo pedido no existe para este tenant."""


class PgToolsUnavailable(BackupError):
    """No se encontró pg_dump/pg_restore en el sistema."""


# ── Localización de binarios PostgreSQL ──────────────────────────────────


def find_pg_tool(name: str) -> str | None:
    """Ubica `pg_dump`/`pg_restore`: PG_BIN_DIR → PATH → instalación estándar.

    En Windows escanea ``C:\\Program Files\\PostgreSQL\\<ver>\\bin`` y elige la
    versión más alta (pg_dump más nuevo puede dumpear servidores iguales o
    más viejos, así que la mayor versión es la opción segura).
    """
    exe = name + (".exe" if os.name == "nt" else "")

    configured = (get_settings().pg_bin_dir or "").strip()
    if configured:
        cand = Path(configured) / exe
        if cand.exists():
            return str(cand)

    on_path = shutil.which(name)
    if on_path:
        return on_path

    if os.name == "nt":
        candidates: list[tuple[int, str]] = []
        for base in (Path(r"C:\Program Files\PostgreSQL"), Path(r"C:\Program Files (x86)\PostgreSQL")):
            if not base.exists():
                continue
            for ver_dir in base.iterdir():
                cand = ver_dir / "bin" / exe
                if cand.exists():
                    try:
                        ver = int(ver_dir.name)
                    except ValueError:
                        ver = 0
                    candidates.append((ver, str(cand)))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

    return None


def pg_tools_available() -> bool:
    return bool(find_pg_tool("pg_dump") and find_pg_tool("pg_restore"))


# ── Rutas / validación ───────────────────────────────────────────────────


def _backups_root() -> Path:
    s = get_settings()
    configured = (s.backups_dir or "").strip()
    if configured:
        root = Path(configured)
        if not root.is_absolute():
            # Relativa a la raíz del backend (<backend>/...).
            root = Path(__file__).resolve().parents[3] / configured
    else:
        root = Path(__file__).resolve().parents[3] / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_tenant(tenant: str) -> str:
    key = (tenant or "").strip().lower()
    if not _TENANT_RE.match(key):
        raise BackupError(f"Clave de tenant inválida: '{tenant}'")
    return key


def _safe_backup_name(name: str) -> str:
    base = (name or "").strip()
    if not _NAME_RE.match(base) or ".." in base:
        raise BackupNotFound(name)
    return base


def _tenant_dir(tenant: str) -> Path:
    d = _backups_root() / _safe_tenant(tenant)
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_backup_path(tenant: str, name: str) -> Path:
    """Ruta del archivo, validando que viva DENTRO del directorio del tenant
    (evita path traversal con nombres como ``../otro``)."""
    safe_name = _safe_backup_name(name)
    base = _tenant_dir(tenant).resolve()
    path = (base / safe_name).resolve()
    if path.parent != base or not path.is_file():
        raise BackupNotFound(name)
    return path


# ── Conexión / URL ───────────────────────────────────────────────────────


def _tenant_db_url(tenant: str) -> str:
    s = get_settings()
    return s.tenant_databases.get(tenant) or s.database_url


def _parse_pg_url(url: str) -> dict:
    """Extrae host/puerto/usuario/clave/dbname de una URL SQLAlchemy.

    Acepta ``postgresql+asyncpg://user:pass@host:port/dbname`` — el driver
    (`+asyncpg`) es irrelevante para pg_dump.
    """
    parsed = urlsplit(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": unquote(parsed.username) if parsed.username else "postgres",
        "password": unquote(parsed.password) if parsed.password else "",
        "dbname": (parsed.path or "/").lstrip("/") or "postgres",
    }


def _schema_flag(tenant: str) -> list[str]:
    """En modo schema-per-tenant restringe el dump/restore al schema del
    tenant. En modo database devuelve [] (se opera sobre la BD entera)."""
    from app.tenant_strategy import schema_for_tenant, using_schema_strategy

    if using_schema_strategy():
        return ["-n", schema_for_tenant(tenant)]
    return []


def _run(args: list[str], env: dict, timeout: int) -> tuple[int, bytes, bytes]:
    """Ejecuta un proceso de forma BLOQUEANTE (se llama vía asyncio.to_thread)."""
    try:
        proc = subprocess.run(args, env=env, capture_output=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, b"", f"pg tool excedió el timeout de {timeout}s".encode()
    except FileNotFoundError as exc:
        return 127, b"", str(exc).encode()


def _clean_err(err: bytes) -> str:
    text = (err or b"").decode("utf-8", "replace").strip()
    # No filtramos la clave: nunca va en stderr, pero recortamos por las dudas.
    return text[-600:] if len(text) > 600 else text


# ── Metadatos de un archivo de respaldo ──────────────────────────────────


def _parse_ts_from_name(name: str) -> datetime | None:
    base = name[:-5] if name.endswith(".dump") else name
    parts = base.rsplit("_", 2)  # [<tenant...>, <ts>, <kind>]
    if len(parts) == 3:
        with contextlib.suppress(ValueError):
            return datetime.strptime(parts[1], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    return None


def _meta(path: Path) -> dict:
    st = path.stat()
    name = path.name
    kind = "auto" if name.endswith("_auto.dump") else "manual"
    created = _parse_ts_from_name(name) or datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    return {
        "name": name,
        "size_bytes": st.st_size,
        "size_human": human_size(st.st_size),
        "created_at": created,
        "kind": kind,
    }


def human_size(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{int(size)} {units[idx]}" if idx == 0 else f"{size:.1f} {units[idx]}"


def list_backups(tenant: str) -> list[dict]:
    d = _tenant_dir(tenant)
    items = [_meta(p) for p in d.glob("*.dump")]
    items.sort(key=lambda m: m["created_at"], reverse=True)
    return items


# ── Crear / restaurar / borrar ───────────────────────────────────────────


async def create_backup(tenant: str, *, kind: str = "manual") -> dict:
    tenant = _safe_tenant(tenant)
    if kind not in ("manual", "auto"):
        kind = "manual"
    tool = find_pg_tool("pg_dump")
    if not tool:
        raise PgToolsUnavailable("pg_dump no está disponible en el servidor.")

    conn = _parse_pg_url(_tenant_db_url(tenant))
    out_dir = _tenant_dir(tenant)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{tenant}_{ts}_{kind}.dump"
    final = out_dir / name
    tmp = out_dir / f"{name}.part"

    args = [
        tool,
        "-h", conn["host"],
        "-p", str(conn["port"]),
        "-U", conn["user"],
        "-d", conn["dbname"],
        "-F", "c",
        "--no-owner",
        "--no-privileges",
        "-f", str(tmp),
        *_schema_flag(tenant),
    ]
    env = {**os.environ, "PGPASSWORD": conn["password"]}

    rc, _out, err = await asyncio.to_thread(_run, args, env, _TIMEOUT_S)
    if rc != 0:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise BackupError(_clean_err(err) or f"pg_dump terminó con código {rc}")

    os.replace(tmp, final)
    logger.info("backup creado tenant=%s archivo=%s", tenant, name)
    return _meta(final)


async def restore_backup(tenant: str, name: str) -> None:
    tenant = _safe_tenant(tenant)
    path = resolve_backup_path(tenant, name)
    tool = find_pg_tool("pg_restore")
    if not tool:
        raise PgToolsUnavailable("pg_restore no está disponible en el servidor.")

    conn = _parse_pg_url(_tenant_db_url(tenant))
    args = [
        tool,
        "-h", conn["host"],
        "-p", str(conn["port"]),
        "-U", conn["user"],
        "-d", conn["dbname"],
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--single-transaction",
        *_schema_flag(tenant),
        str(path),
    ]
    env = {**os.environ, "PGPASSWORD": conn["password"]}

    rc, _out, err = await asyncio.to_thread(_run, args, env, _TIMEOUT_S)
    if rc != 0:
        raise BackupError(_clean_err(err) or f"pg_restore terminó con código {rc}")
    logger.info("backup restaurado tenant=%s archivo=%s", tenant, name)


def delete_backup(tenant: str, name: str) -> None:
    path = resolve_backup_path(tenant, name)
    path.unlink()
    logger.info("backup borrado tenant=%s archivo=%s", _safe_tenant(tenant), path.name)


# ── Programación (backup automático) ─────────────────────────────────────


def _schedule_file() -> Path:
    return _backups_root() / "_schedules.json"


def _load_all_schedules() -> dict:
    f = _schedule_file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_all_schedules(data: dict) -> None:
    _schedule_file().write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def _valid_freq(freq: str) -> str:
    f = (freq or "").strip().lower()
    return f if f in _VALID_FREQS else "daily"


def _valid_hour(hour) -> int:
    try:
        return min(23, max(0, int(hour)))
    except (TypeError, ValueError):
        return 2


def _valid_retention(retention) -> int:
    try:
        return min(50, max(1, int(retention)))
    except (TypeError, ValueError):
        return 7


def load_schedule(tenant: str) -> dict:
    tenant = _safe_tenant(tenant)
    raw = _load_all_schedules().get(tenant) or {}
    return {**_DEFAULT_SCHEDULE, **raw}


def save_schedule(tenant: str, *, enabled: bool, frequency: str, hour, retention) -> dict:
    tenant = _safe_tenant(tenant)
    with _LOCK:
        data = _load_all_schedules()
        prev = data.get(tenant) or {}
        data[tenant] = {
            "enabled": bool(enabled),
            "frequency": _valid_freq(frequency),
            "hour": _valid_hour(hour),
            "retention": _valid_retention(retention),
            # Conservamos last_run; si nunca corrió queda None → primer
            # respaldo automático inmediato cuando se habilita.
            "last_run": prev.get("last_run"),
        }
        _write_all_schedules(data)
    return load_schedule(tenant)


def _set_last_run(tenant: str, when: datetime) -> None:
    tenant = _safe_tenant(tenant)
    with _LOCK:
        data = _load_all_schedules()
        cfg = {**_DEFAULT_SCHEDULE, **(data.get(tenant) or {})}
        cfg["last_run"] = when.astimezone(timezone.utc).isoformat()
        data[tenant] = cfg
        _write_all_schedules(data)


def _parse_last_run(cfg: dict) -> datetime | None:
    raw = cfg.get("last_run")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def is_due(cfg: dict, now: datetime) -> bool:
    if not cfg.get("enabled"):
        return False
    last = _parse_last_run(cfg)
    if last is None:
        return True  # primer respaldo apenas se habilita
    freq = _valid_freq(cfg.get("frequency", "daily"))
    delta = now - last
    if freq == "hourly":
        return delta >= timedelta(hours=1)
    if freq == "weekly":
        return delta >= timedelta(days=7)
    # daily
    hour = _valid_hour(cfg.get("hour", 2))
    return last.date() < now.date() and now.hour >= hour


def next_run_iso(cfg: dict, now: datetime) -> str | None:
    """Próxima ejecución estimada (solo informativa para la UI)."""
    if not cfg.get("enabled"):
        return None
    last = _parse_last_run(cfg)
    freq = _valid_freq(cfg.get("frequency", "daily"))
    if last is None:
        return now.isoformat()
    if freq == "hourly":
        return (last + timedelta(hours=1)).isoformat()
    if freq == "weekly":
        return (last + timedelta(days=7)).isoformat()
    hour = _valid_hour(cfg.get("hour", 2))
    today_at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if last.date() < now.date() and now >= today_at:
        return now.isoformat()
    nxt = today_at if now < today_at else today_at + timedelta(days=1)
    return nxt.isoformat()


def prune_retention(tenant: str, retention: int) -> int:
    """Borra los respaldos AUTOMÁTICOS más viejos que excedan `retention`.
    Los manuales no se tocan (los gestiona el usuario). Devuelve cuántos borró."""
    retention = _valid_retention(retention)
    autos = [m for m in list_backups(tenant) if m["kind"] == "auto"]  # desc por fecha
    removed = 0
    for meta in autos[retention:]:
        with contextlib.suppress(OSError, BackupNotFound):
            resolve_backup_path(tenant, meta["name"]).unlink()
            removed += 1
    return removed


async def run_due_backups(now: datetime | None = None) -> None:
    """Recorre los schedules y dispara los respaldos vencidos. Best-effort."""
    now = now or datetime.now(timezone.utc)
    if not pg_tools_available():
        return
    for tenant, raw in list(_load_all_schedules().items()):
        cfg = {**_DEFAULT_SCHEDULE, **(raw or {})}
        try:
            if not is_due(cfg, now):
                continue
            await create_backup(tenant, kind="auto")
            _set_last_run(tenant, now)
            prune_retention(tenant, int(cfg.get("retention", 7)))
        except Exception:  # noqa: BLE001 — un tenant roto no detiene al resto
            logger.exception("backup automático falló para tenant '%s'", tenant)


# ── Ciclo de vida del scheduler ──────────────────────────────────────────

_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop(interval: int = 60) -> None:
    logger.info("backup scheduler iniciado (revisión cada %ss)", interval)
    try:
        while True:
            try:
                await run_due_backups()
            except Exception:  # noqa: BLE001
                logger.exception("scheduler de backups: tick falló")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("backup scheduler detenido")
        raise


def start_scheduler(interval: int = 60) -> None:
    global _scheduler_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # sin loop corriendo (p. ej. import en test) — no-op
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = loop.create_task(_scheduler_loop(interval))


async def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _scheduler_task
    _scheduler_task = None
