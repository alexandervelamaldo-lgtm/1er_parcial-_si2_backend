from __future__ import annotations

import os
import subprocess
import sys

from app.services.tenant_registry import tenant_registry


def main() -> int:
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    tenants = tenant_registry.list_keys()
    if not tenants:
        print("No se encontraron tenants configurados.")
        return 1

    for tenant in tenants:
        env = os.environ.copy()
        env["TENANT_KEY"] = tenant
        print(f"[migrate] tenant={tenant}")
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend_dir,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            print(f"[migrate] tenant={tenant} fallo con exit_code={result.returncode}")
            return result.returncode
    print("[migrate] todos los tenants quedaron en head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
